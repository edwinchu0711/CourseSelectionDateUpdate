import requests
from bs4 import BeautifulSoup
import urllib.parse
import re
import sys
import os
import json
from datetime import datetime, timezone

# Ensure the console output handles UTF-8 correctly (fixes UnicodeEncodeError on Windows)
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Program name to program_id mapping
_PROGRAM_ID_MAP = {
    "軟體工程學程": "software_engineering",
    "日本研究學程": "japanese_studies",
    "金融工程學程": "financial_engineering",
    "生醫科技與生技產業學程": "biomedical_tech",
    "半導體科技與應用學程": "semiconductor_tech",
    "人工智慧與人類社會學程": "ai_society",
    "環境教育與永續發展學程": "environmental_education",
    "文化創意產業學程": "cultural_creative",
    "全球政經與區域發展學程": "global_politics",
    "數位內容與互動科技學程": "digital_content",
}

MANIFEST_FILENAME = "pdf_manifest.json"


def _derive_program_id(name: str) -> str:
    """Derive a program_id from program name. Uses known mapping or slugify."""
    if name in _PROGRAM_ID_MAP:
        return _PROGRAM_ID_MAP[name]
    slug = re.sub(r'[^\w一-鿿]', '_', name).strip('_')
    slug = re.sub(r'_+', '_', slug)
    return slug if slug else "unknown_program"


def _derive_version() -> str:
    """Derive the current academic year version string, e.g. '114-1'."""
    now = datetime.now()
    roc_year = now.year - 1911
    semester = "1" if now.month <= 7 else "2"
    return f"{roc_year}-{semester}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_manifest(manifest_path: str) -> dict:
    """Load existing manifest, or return empty structure."""
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"lastFullScan": None, "programs": {}}


def _save_manifest(manifest: dict, manifest_path: str):
    """Save manifest to file."""
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def scrape_nsysu_programs():
    url = "https://ctdr.nsysu.edu.tw/class2.php"
    programs = []

    try:
        response = requests.get(url)
        response.encoding = response.apparent_encoding
        response.raise_for_status()
    except Exception as e:
        print(f"無法存取網頁: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')

    tables = soup.find_all('table', class_='plan')

    if not tables:
        print("找不到任何資料表格")
        return []

    for i, table in enumerate(tables):
        rows = table.find_all('tr')
        is_discontinued_table = (i == len(tables) - 1)

        for row in rows:
            cols = row.find_all(['td', 'th'])

            if row.get('bgcolor') == '#FFFF99' or row.find('th'):
                continue

            if not is_discontinued_table:
                if len(cols) >= 5:
                    name = cols[0].get_text(strip=True)
                    features = cols[1].get_text(strip=True)

                    link_tag = cols[4].find('a')
                    link = ""
                    if link_tag and 'href' in link_tag.attrs:
                        link = urllib.parse.urljoin(url, link_tag['href'])

                    if name and link:
                        programs.append({
                            "name": name,
                            "features": features,
                            "link": link,
                            "status": "active",
                            "programId": _derive_program_id(name),
                        })

            else:
                if len(cols) >= 3:
                    raw_names = cols[1].get_text(strip=True)

                    link_tag = cols[2].find('a')
                    link = ""
                    if link_tag and 'href' in link_tag.attrs:
                        link = urllib.parse.urljoin(url, link_tag['href'])

                    name_list = re.split(r'(?<=\))、|(?<=）)、', raw_names)

                    for name in name_list:
                        clean_name = re.sub(r'\(.*?\)|（.*?）', '', name).strip()
                        if clean_name and link:
                            programs.append({
                                "name": clean_name,
                                "features": "",
                                "link": link,
                                "status": "discontinued",
                                "programId": _derive_program_id(clean_name),
                            })

    return programs


def download_pdfs(
    output_dir: str,
    version: str | None = None,
    max_update: int = 5,
) -> list[dict]:
    """Incrementally download/update PDF files for active programs.

    Strategy:
    - Only download up to `max_update` PDFs per run (the oldest checked ones).
    - If a PDF already exists and the remote file size is the same, skip download
      but update lastCheckedAt in the manifest.
    - If a PDF doesn't exist locally or the size changed, download it.
    - All download history is tracked in pdf_manifest.json.

    Args:
        output_dir: Directory to save PDFs (e.g., 'data/pdfs').
        version: Academic year version string (e.g., '114-1').
        max_update: Maximum number of PDFs to update per run.

    Returns:
        List of download results.
    """
    if version is None:
        version = _derive_version()

    os.makedirs(output_dir, exist_ok=True)
    manifest_path = os.path.join(output_dir, MANIFEST_FILENAME)
    manifest = _load_manifest(manifest_path)

    programs = scrape_nsysu_programs()
    active_programs = [p for p in programs if p["status"] == "active"]

    # Sort by lastCheckedAt (oldest first); never-checked programs go first
    def _sort_key(prog):
        pid = prog["programId"]
        entry = manifest["programs"].get(pid, {})
        last_checked = entry.get("lastCheckedAt", "")
        return last_checked or ""

    active_programs.sort(key=_sort_key)

    # Pick the max_update oldest
    to_update = active_programs[:max_update]

    results = []
    now = _now_iso()
    manifest["lastFullScan"] = now

    for prog in to_update:
        program_id = prog["programId"]
        link = prog["link"]

        if not link:
            print(f"跳過 {prog['name']}：無下載連結")
            continue

        filename = f"{program_id}_{version}.pdf"
        filepath = os.path.join(output_dir, filename)

        try:
            print(f"檢查: {prog['name']} ({filename})")
            resp = requests.get(link, timeout=30, stream=True)
            resp.raise_for_status()

            remote_size = int(resp.headers.get("Content-Length", 0))
            content_type = resp.headers.get("Content-Type", "")

            # Check if local file exists and has same size
            local_entry = manifest["programs"].get(program_id, {})
            local_size = local_entry.get("fileSize", -1)

            if os.path.exists(filepath) and remote_size > 0 and remote_size == local_size:
                # Same size — skip download, just update checked date
                print(f"  檔案大小相同，跳過下載 ({remote_size} bytes)")
                manifest["programs"][program_id] = {
                    "name": prog["name"],
                    "link": link,
                    "lastDownloadedAt": local_entry.get("lastDownloadedAt", now),
                    "lastCheckedAt": now,
                    "fileSize": remote_size,
                    "filename": filename,
                    "status": "current",
                }
                results.append({
                    "programId": program_id,
                    "name": prog["name"],
                    "filepath": filepath,
                    "status": "skipped_same_size",
                })
                continue

            # Download the file
            print(f"  下載中: {prog['name']} → {filename}")
            content = resp.content

            if "pdf" not in content_type.lower() and not link.lower().endswith(".pdf"):
                print(f"  警告: {prog['name']} 的回應可能不是 PDF (Content-Type: {content_type})")

            with open(filepath, "wb") as f:
                f.write(content)

            actual_size = os.path.getsize(filepath)

            manifest["programs"][program_id] = {
                "name": prog["name"],
                "link": link,
                "lastDownloadedAt": now,
                "lastCheckedAt": now,
                "fileSize": actual_size,
                "filename": filename,
                "status": "current",
            }

            print(f"  完成: {filename} ({actual_size} bytes)")
            results.append({
                "programId": program_id,
                "name": prog["name"],
                "filepath": filepath,
                "status": "downloaded",
                "fileSize": actual_size,
            })

        except Exception as e:
            print(f"  下載失敗 {prog['name']}: {e}")
            manifest["programs"][program_id] = {
                "name": prog["name"],
                "link": link,
                "lastCheckedAt": now,
                "fileSize": local_entry.get("fileSize", 0),
                "filename": local_entry.get("filename", filename),
                "status": "failed",
                "error": str(e),
            }
            results.append({
                "programId": program_id,
                "name": prog["name"],
                "filepath": None,
                "status": "failed",
                "error": str(e),
            })

    # Also update manifest entries for programs NOT in this update batch
    for prog in active_programs:
        pid = prog["programId"]
        if pid not in {r["programId"] for r in results}:
            if pid in manifest["programs"]:
                manifest["programs"][pid]["lastCheckedAt"] = now
            else:
                manifest["programs"][pid] = {
                    "name": prog["name"],
                    "link": prog["link"],
                    "lastCheckedAt": now,
                    "fileSize": 0,
                    "filename": f"{pid}_{version}.pdf",
                    "status": "pending",
                }

    _save_manifest(manifest, manifest_path)

    downloaded = sum(1 for r in results if r["status"] == "downloaded")
    skipped = sum(1 for r in results if r["status"] == "skipped_same_size")
    failed = sum(1 for r in results if r["status"] == "failed")
    print(f"\n本次更新 {len(results)} 個學程：{downloaded} 下載、{skipped} 無變更、{failed} 失敗")
    print(f"清單已存至: {manifest_path}")

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NSYSU 學程資料爬蟲與 PDF 下載")
    parser.add_argument("--download", action="store_true", help="增量下載最舊的 5 個 active 學程 PDF")
    parser.add_argument("--download-all", action="store_true", help="強制下載所有 active 學程 PDF")
    parser.add_argument("--output-dir", default="data/pdfs", help="PDF 下載目錄 (預設: data/pdfs)")
    parser.add_argument("--version", default=None, help="學年版本 (例如: 114-1)")
    parser.add_argument("--max-update", type=int, default=5, help="每次最多更新的 PDF 數量 (預設: 5)")
    args = parser.parse_args()

    if args.download_all:
        download_pdfs(args.output_dir, args.version, max_update=999)
    elif args.download:
        download_pdfs(args.output_dir, args.version, max_update=args.max_update)
    else:
        all_programs = scrape_nsysu_programs()
        for prog in all_programs:
            print(f"Name: {prog['name']}, ID: {prog.get('programId', 'N/A')}, Link: {prog['link']}")