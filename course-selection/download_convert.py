import os
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


def select_largest_filename(urls: list[str]) -> str | None:
    """選擇檔名字典序最大的 URL（模仿原 Dart 邏輯）"""
    if not urls:
        return None

    def get_filename(url):
        path = urllib.parse.urlparse(url).path
        name = os.path.basename(path)
        return name.split('?')[0] if '?' in name else name

    best = max(urls, key=get_filename)
    return best


def find_pdf_link(html: str, current_url: str) -> str | None:
    """在 HTML 中尋找選課須知相關的 PDF 連結"""
    parser = SelcrsHTMLParser()
    parser.feed(html)

    keywords = ['選課須知', '選課須知及注意事項', '選課手冊']
    candidate_urls = []

    # 第一輪：文字/title 含關鍵字 且 href 直接是 PDF
    for href, text, title in parser.links:
        full_text = text + (title or '')
        href_lower = href.lower()
        is_pdf = href_lower.endswith('.pdf') or '.pdf?' in href_lower
        has_keyword = any(kw in full_text or kw in href for kw in keywords)
        if is_pdf and has_keyword:
            candidate_urls.append(resolve_url(current_url, href))
            if len(candidate_urls) >= 3:
                break

    if candidate_urls:
        return select_largest_filename(candidate_urls)

    # 第二輪：文字含關鍵字，解析後的完整 URL 是 PDF
    for href, text, title in parser.links:
        full_text = text + (title or '')
        if any(kw in full_text for kw in keywords):
            resolved = resolve_url(current_url, href)
            if resolved.lower().endswith('.pdf') or '.pdf?' in resolved.lower():
                candidate_urls.append(resolved)
                if len(candidate_urls) >= 3:
                    break

    return select_largest_filename(candidate_urls) if candidate_urls else None


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
    print(f"   PDF  → {pdf_path}")
    print(f"   Markdown → {md_path}")


if __name__ == "__main__":
    main()
