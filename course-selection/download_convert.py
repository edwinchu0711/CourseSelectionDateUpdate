import os
import re
import urllib.request
import urllib.parse
from html.parser import HTMLParser
import pdfplumber

# ─────────────────────────────────────────
# 設定區
# ─────────────────────────────────────────
BASE_URL = "https://selcrs.nsysu.edu.tw/"
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))  # 輸出目錄，設定為腳本所在的資料夾 (course-selection)


# ─────────────────────────────────────────
# Part 1：HTML 解析器
# ─────────────────────────────────────────
class SelcrsHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._current_href = None
        self._current_title = None
        self._current_text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            attrs_dict = dict(attrs)
            self._current_href = attrs_dict.get('href')
            self._current_title = attrs_dict.get('title', '')
            self._current_text = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self._current_href is not None:
            text = "".join(self._current_text).strip()
            self.links.append((self._current_href, text, self._current_title))
            self._current_href = None
            self._current_title = None
            self._current_text = []


# ─────────────────────────────────────────
# Part 2：網路工具函式
# ─────────────────────────────────────────
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
}


def fetch_html(url: str) -> str | None:
    """抓取網頁 HTML 內容"""
    print(f"[Info] 正在抓取網頁: {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"[Error] 無法獲取網頁內容: {e}")
        return None


def resolve_url(base: str, href: str) -> str:
    """將相對路徑解析為絕對路徑"""
    return urllib.parse.urljoin(base, href)


def extract_semester_key(text: str) -> int:
    """
    從連結文字中提取學年期數字作為排序鍵
    例如：'114學年度第2學期' → 1142
         '115學年度第1學期' → 1151
    若無法解析，回傳 0
    """
    # 嘗試從文字中提取 "XXX學年度第X學期"
    match = re.search(r'(\d{3})\s*學年度\s*第\s*([12])\s*學期', text)
    if match:
        year = int(match.group(1))      # 例如 114、115
        semester = int(match.group(2))  # 1 或 2
        return year * 10 + semester     # 例如 1142、1151
    return 0  # 無法解析時回傳 0


def select_best_pdf(candidates: list[tuple[str, str]]) -> str | None:
    """
    從候選 PDF 中選出最佳的一個
    candidates: List of (url, display_text)

    優先策略：從顯示文字提取學年期數字比較（例如 1151 > 1142）
    備用策略：字典序最大的檔名（原本邏輯）
    """
    if not candidates:
        return None

    # 嘗試用學年期數字排序
    semester_scored = [
        (extract_semester_key(text), url, text)
        for url, text in candidates
    ]

    # 檢查是否有任何候選成功解析出學年期（key > 0）
    max_key = max(k for k, _, _ in semester_scored)
    if max_key > 0:
        # 有解析成功 → 取學年期代碼最大的
        best = max(semester_scored, key=lambda x: x[0])
        print(f"[Info] 依學年期選擇 PDF（學年期代碼: {best[0]}）: {best[2][:30]}...")
        return best[1]  # 回傳 url

    # 備用：字典序最大的檔名（原本邏輯）
    print("[Info] 無法解析學年期，改用檔名字典序選擇 PDF")

    def get_filename(url: str) -> str:
        path = urllib.parse.urlparse(url).path
        name = os.path.basename(path)
        return name.split('?')[0] if '?' in name else name

    return max(candidates, key=lambda x: get_filename(x[0]))[0]


def find_pdf_link(html: str, current_url: str) -> str | None:
    """在 HTML 中尋找選課須知相關的 PDF 連結"""
    parser = SelcrsHTMLParser()
    parser.feed(html)

    keywords = ['選課須知', '選課須知及注意事項', '選課手冊']
    # 儲存 (url, display_text) 的 tuple，display_text 用於學年期解析
    candidate_pairs: list[tuple[str, str]] = []

    # 第一輪：文字/title 含關鍵字 且 href 直接是 PDF
    for href, text, title in parser.links:
        full_text = text + (title or '')
        href_lower = href.lower()
        is_pdf = href_lower.endswith('.pdf') or '.pdf?' in href_lower
        has_keyword = any(kw in full_text or kw in href for kw in keywords)
        if is_pdf and has_keyword:
            resolved = resolve_url(current_url, href)
            candidate_pairs.append((resolved, full_text))  # 同時儲存顯示文字
            if len(candidate_pairs) >= 5:  # 放寬至 5 個，讓學年期比較更準確
                break

    if candidate_pairs:
        return select_best_pdf(candidate_pairs)

    # 第二輪：文字含關鍵字，解析後的完整 URL 是 PDF
    for href, text, title in parser.links:
        full_text = text + (title or '')
        if any(kw in full_text for kw in keywords):
            resolved = resolve_url(current_url, href)
            if resolved.lower().endswith('.pdf') or '.pdf?' in resolved.lower():
                candidate_pairs.append((resolved, full_text))
                if len(candidate_pairs) >= 5:
                    break

    return select_best_pdf(candidate_pairs) if candidate_pairs else None


def find_sub_link(html: str, current_url: str) -> str | None:
    """尋找含有關鍵字的子頁面連結"""
    parser = SelcrsHTMLParser()
    parser.feed(html)

    keywords = ['選課須知', '注意事項', '選課公告', '選課手冊']
    for href, text, title in parser.links:
        full_text = text + (title or '')
        if any(kw in full_text for kw in keywords):
            return resolve_url(current_url, href)
    return None


def download_file(url: str, save_path: str) -> bool:
    """下載檔案至指定路徑"""
    print(f"[Info] 開始下載 PDF: {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            with open(save_path, 'wb') as f:
                f.write(resp.read())
        print(f"[Success] 下載完成！儲存路徑: {save_path}")
        return True
    except Exception as e:
        print(f"[Error] 下載 PDF 失敗: {e}")
        return False


# ─────────────────────────────────────────
# Part 3：PDF → Markdown 轉換
# ─────────────────────────────────────────
def pdf_to_markdown(pdf_path: str, md_path: str) -> None:
    """使用 pdfplumber 將 PDF 轉換為 Markdown"""
    print(f"\n[Info] 開始將 PDF 轉換為 Markdown: {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        with open(md_path, "w", encoding="utf-8") as md_file:
            for i, page in enumerate(pdf.pages):
                md_file.write(f"## Page {i + 1}\n\n")

                # 表格處理
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row_idx, row in enumerate(table):
                            clean_row = [
                                str(cell).replace('\n', ' ').strip() if cell is not None else ""
                                for cell in row
                            ]
                            md_file.write("| " + " | ".join(clean_row) + " |\n")
                            if row_idx == 0:
                                md_file.write("| " + " | ".join(["---"] * len(row)) + " |\n")
                        md_file.write("\n\n")

                # 純文字處理
                text = page.extract_text()
                if text:
                    md_file.write(text + "\n\n")

                md_file.write("\n---\n\n")

    print(f"[Success] Markdown 已儲存至: {md_path}")


# ─────────────────────────────────────────
# Part 4：主流程
# ─────────────────────────────────────────
def main():
    # Step 1：抓取首頁
    homepage_html = fetch_html(BASE_URL)
    if not homepage_html:
        print("[Error] 無法連接到選課系統，程式終止。")
        return

    # Step 2：首頁尋找 PDF
    pdf_url = find_pdf_link(homepage_html, BASE_URL)

    # Step 3：若首頁找不到，進入子頁面
    if not pdf_url:
        print("[Info] 首頁未直接發現 PDF，嘗試尋找子頁面...")
        sub_link = find_sub_link(homepage_html, BASE_URL)
        if sub_link:
            print(f"[Info] 找到子頁面: {sub_link}")
            subpage_html = fetch_html(sub_link)
            if subpage_html:
                pdf_url = find_pdf_link(subpage_html, sub_link)

    # Step 4：下載 PDF
    if not pdf_url:
        print("[Error] 無法找到任何選課須知 PDF 連結，程式終止。")
        return

    raw_filename = os.path.basename(urllib.parse.urlparse(pdf_url).path)
    pdf_filename = raw_filename if raw_filename.lower().endswith('.pdf') else "選課須知.pdf"
    pdf_path = os.path.join(OUTPUT_DIR, pdf_filename)

    if not download_file(pdf_url, pdf_path):
        print("[Error] PDF 下載失敗，程式終止。")
        return

    # Step 5：PDF 轉 Markdown
    md_path = os.path.join(OUTPUT_DIR, "course-selection.md")
    pdf_to_markdown(pdf_path, md_path)

    print(f"\n🎉 全部完成！")
    print(f"   PDF      → {pdf_path}")
    print(f"   Markdown → {md_path}")


if __name__ == "__main__":
    main()
