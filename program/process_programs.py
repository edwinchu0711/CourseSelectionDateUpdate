import os
import json
import argparse
import time
import requests
import datetime
import re
import sys
import uuid
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai
from 學程 import scrape_nsysu_programs
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# ==========================================
# 1. 路徑與全域變數設定
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
METADATA_FILE = os.path.join(BASE_DIR, "metadata.json")
DATA_DIR = os.path.join(BASE_DIR, "data")

MODEL_NAME = "gemini-flash-lite-latest"
MAX_RETRIES = 5

# 執行緒鎖 (保護共同資源不被多個執行緒打架)
PRINT_LOCK = threading.Lock()
META_LOCK = threading.Lock()
INPUT_LOCK = threading.Lock()

def safe_print(*args, **kwargs):
    """執行緒安全的 print，防止終端機文字交錯"""
    with PRINT_LOCK:
        print(*args, **kwargs)

def get_clean_name(name):
    """移除第一個「學程」後面如果跟著「原」、「 」、「(」、「（」之後的所有文字"""
    clean_name = name
    start_idx = 0
    while True:
        idx = clean_name.find("學程", start_idx)
        if idx == -1:
            break
        if idx + 2 < len(clean_name):
            if clean_name[idx + 2] in ("原", " ", "(", "（"):
                clean_name = clean_name[:idx + 2]
                break
            else:
                start_idx = idx + 2
        else:
            break
    return clean_name

def normalize_academic_year(input_str):
    year_match = re.search(r'(\d{2,3})', input_str)
    if not year_match:
        return input_str.strip()
        
    year = year_match.group(1)
    year_end_pos = year_match.end()
    rest_of_str = input_str[year_end_pos:]
    
    sem = "1"
    if "2" in rest_of_str or "二" in rest_of_str or "second" in rest_of_str.lower():
        sem = "2"
    elif "1" in rest_of_str or "一" in rest_of_str or "first" in rest_of_str.lower():
        sem = "1"
        
    return f"{year}-{sem}"

def init_clients(ci_mode=False):
    """初始化並回傳包含多個 API Key Client 的佇列"""
    if ci_mode:
        key = os.environ.get("GEMINI_API_KEY")
        if not key or not key.strip():
            safe_print("❌ 錯誤: CI 模式需要設定 GEMINI_API_KEY 環境變數。")
            sys.exit(1)
        client_q = queue.Queue()
        client_q.put(genai.Client(api_key=key))
        safe_print("✅ CI 模式：使用單一 GEMINI_API_KEY。")
        return client_q, 1
    keys = [
        os.environ.get("GEMINI_API_KEY"),
        os.environ.get("GEMINI_API_KEY2"),
        os.environ.get("GEMINI_API_KEY3")
    ]
    # 過濾掉空的或未設定的 Key
    valid_keys = [k for k in keys if k and k.strip()]
    
    if not valid_keys:
        safe_print("❌ 錯誤: 在 .env 找不到任何有效的 GEMINI_API_KEY。")
        sys.exit(1)
        
    client_q = queue.Queue()
    for k in valid_keys:
        client_q.put(genai.Client(api_key=k))
        
    safe_print(f"✅ 成功載入 {len(valid_keys)} 把 API Keys，將啟用 {len(valid_keys)} 個執行緒同步處理。")
    return client_q, len(valid_keys)

def execute_with_retry(func, *args, **kwargs):
    """加上對 503 與 429 錯誤的處理與動態退避"""
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_msg = str(e)
            # 捕捉 503 伺服器錯誤 或 429/Quota 請求過於頻繁
            if "503" in error_msg or "429" in error_msg or "Quota" in error_msg:
                if attempt < MAX_RETRIES - 1:
                    wait_time = 5 + (attempt * 5) # 5, 10, 15... 指數或線性退避
                    safe_print(f"  ⚠️ [重試機制] 遇到伺服器繁忙(503/429)，等待 {wait_time} 秒後進行第 {attempt + 1} 次重試...")
                    time.sleep(wait_time)
                else:
                    safe_print("  ❌ [終止執行] 連續多次遇到伺服器錯誤，放棄此任務。")
                    raise e
            else:
                raise e

def get_pdf_size(url):
    try:
        response = requests.head(url, allow_redirects=True, timeout=10)
        return int(response.headers.get('content-length', 0))
    except Exception as e:
        safe_print(f"Error getting PDF size for {url}: {e}")
        return 0

def download_pdf(url, filename):
    try:
        response = requests.get(url, timeout=30)
        with open(filename, 'wb') as f:
            f.write(response.content)
        return True
    except Exception as e:
        safe_print(f"Error downloading PDF from {url}: {e}")
        return False

def extract_json_from_text(text):
    if not text:
        return None
        
    block_match = re.search(r'```(?:json)?(.*?)```', text, re.DOTALL | re.IGNORECASE)
    if block_match:
        json_str = block_match.group(1).strip()
    else:
        json_str = text.strip()

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        array_match = re.search(r'\[.*\]', text, re.DOTALL)
        if array_match:
            try:
                return json.loads(array_match.group(0))
            except json.JSONDecodeError:
                pass
                
        obj_match = re.search(r'\{.*\}', text, re.DOTALL)
        if obj_match:
            try:
                return json.loads(obj_match.group(0))
            except json.JSONDecodeError:
                pass
    return None

def analyze_pdf_with_gemini(client, pdf_path, program_name, recorded_semesters, force_update, ci_mode=False):
    safe_print(f"🔍 Analyzing {program_name} with Gemini...")
    
    try:
        sample_file = execute_with_retry(client.files.upload, file=pdf_path)
        
        while sample_file.state == "PROCESSING":
            time.sleep(2)
            sample_file = execute_with_retry(client.files.get, name=sample_file.name)
            
        if sample_file.state == "FAILED":
            raise Exception("File processing failed")

        safe_print(f"  -> [{program_name}] 正在向 Gemini 詢問該學程有哪些學年度學期版本...")
        extract_prompt = """請閱讀這個學程的 PDF 文件，找出其中包含的所有「學年度學期版本」及其對應的頁碼範圍。
請嚴格以 JSON 陣列格式回傳，例如：
[
  {"semester_raw": "114學年度第二學期", "pages": "1-2"},
  {"semester_raw": "113學年度第一學期", "pages": "3"}
]
請注意：
- 務必只回傳純 JSON 陣列，不要包含 ```json ``` 標記或任何額外的前後文字與說明。
- 確保 "semester_raw" 包含完整的中文學年度與學期名稱。
- "pages" 請註明該版本在 PDF 中的確切頁碼。"""

        response = execute_with_retry(
            client.models.generate_content,
            model=MODEL_NAME,
            contents=[sample_file, extract_prompt]
        )
        
        groups = extract_json_from_text(response.text)
        if not groups or not isinstance(groups, list):
            safe_print(f"⚠️ [{program_name}] 無法解析出學年度版本。回傳內容為:\n{response.text}")
            
            if ci_mode:
                safe_print(f"  ⚠️ [{program_name}] CI 模式：無法偵測學期版本，跳過此學程。")
                groups = []
            else:
                with INPUT_LOCK:
                    try:
                        num_groups_str = input(f"\n⚠️ 請手動輸入此學程「{program_name}」有幾個學年度（直接 Enter 可跳過）：").strip()
                        if num_groups_str:
                            num_groups = int(num_groups_str)
                            groups = []
                            for g_idx in range(num_groups):
                                sem_str = input(f"  第 {g_idx + 1} 組的學年度學期：").strip()
                                pages_str = input(f"  第 {g_idx + 1} 組的頁碼：").strip()
                                groups.append({"semester_raw": sem_str, "pages": pages_str})
                        else:
                            groups = []
                    except Exception:
                        safe_print(f"  [{program_name}] 手動輸入跳過。")
                        groups = []

        if not groups:
            safe_print(f"  [{program_name}] 沒有找到組別資訊，跳過此學程。")
            execute_with_retry(client.files.delete, name=sample_file.name)
            return []

        safe_print(f"  [{program_name}] 自動偵測到 {len(groups)} 個學年度版本。")

        results = []
        total_groups = len(groups)

        for i, group in enumerate(groups, 1):
            semester_raw = group.get('semester_raw')
            pages = group.get('pages')
            norm_sem = normalize_academic_year(semester_raw)
            
            # ==========================================
            # 判斷是否跳過：已有紀錄且不強制更新
            # ==========================================
            if not force_update and norm_sem in recorded_semesters:
                safe_print(f"  -> [{program_name}] [{i}/{total_groups}] ⏭️ 略過「{semester_raw}」：已有紀錄。")
                continue
            
            prompt = f"""你是一位專業的文件整理助手。正在分析的學程名稱是：{program_name}。

【提取範圍限制】
請只處理以下指定的版本與頁碼範圍：
- 指定學年度學期版本：{semester_raw}
- 指定頁碼範圍：{pages}
請只分析、提取、並處理 PDF 中對應於這組特定頁碼範圍與學年度學期的內容。其他無關的頁面或版本內容請完全忽略。

請將該學年度學期的學程課程規劃內容，完整整理為結構化 Markdown 格式，規則如下：

━━━━━━━━━━━━━━━━━━━━━━
【一、版本分區】
━━━━━━━━━━━━━━━━━━━━━━
- 每個「學年度學期版本」獨立為一個 H2 標題區塊
  格式：## 【XXX 學年度第 X 學期版本】
- 標題下方附一行會議通過日期資訊（原文照錄）

━━━━━━━━━━━━━━━━━━━━━━
【二、課程表格】
━━━━━━━━━━━━━━━━━━━━━━
每個版本內依「核心課程」與「選修課程」分為兩個 H3 子區塊。
每個子區塊使用 Markdown 表格呈現。

▸ 自動偵測欄位：
  - 若 PDF 有「各系所課程名稱」欄位 → 使用五欄：
    | 開課單位 | 課程名稱 | 各系所對應課程名稱 | 學分數 | 備註 |
  - 若無此欄位 → 使用四欄：
    | 開課單位 | 課程名稱 | 學分數 | 備註 |

▸ 欄位處理規則：
  1. 【開課單位】
     - 多個系所並列時，以頓號「、」分隔寫在同一格
     - 保留原始名稱（如「跨院選修(工)」、「社、管學院」）
  2. 【課程名稱】
     - 完整保留，包含符號：＊號、※號、（一）（二）等
     - ※號課程代表研究所課程，需保留
     - ＊號課程代表有最低學分限制的選修，需保留
  3. 【各系所對應課程名稱】（若有此欄）
     - 完整保留所有「或」的替代名稱
  4. 【備註欄】
     - 完整保留，包含：
       · 抵免對應課程清單（如「得以 A/B/C 辦理抵免」）
       · 採認互斥說明（如「至多採認一科」）
       · 重複抵免限制（如「若已抵免為核心課程，則不再重複抵免」）
       · 博雅向度、聯盟學程名稱
       · 新開課程標記（如「109-2 新開課程」）
     - 備註欄為空者填入「—」

━━━━━━━━━━━━━━━━━━━━━━
【三、學分規定標示】
━━━━━━━━━━━━━━━━━━━━━━
- 核心課程學分數，用引用區塊標示：
  > 核心課程學分數：XX 學分
- 總學分數規定，同樣用引用區塊標示：
  > 總學分數：至少 XX 學分

━━━━━━━━━━━━━━━━━━━━━━
【四、備註條文】
━━━━━━━━━━━━━━━━━━━━━━
- 每個版本表格結束後，附上該版本所有備註條文
- 使用編號條列，原文照錄，不得刪減或改寫
- 格式：
  **備註：**
  1. xxxxxxx
  2. xxxxxxx

━━━━━━━━━━━━━━━━━━━━━━
【五、全域限制】
━━━━━━━━━━━━━━━━━━━━━━
- 不得省略任何課程、任何開課單位、任何備註內容
- 不得加入 PDF 原文沒有的說明、推論或補充
- 若某版本核心課程學分數為 0，仍需保留該子區塊並標注「（本版本無核心課程）」
- 輸出純 Markdown，不要包在程式碼區塊內"""
            
            safe_print(f"  -> [{program_name}] [{i}/{total_groups}] 送出 API 請求分析「{semester_raw}」...")
            
            try:
                response = execute_with_retry(
                    client.models.generate_content,
                    model=MODEL_NAME,
                    contents=[sample_file, prompt]
                )
                
                results.append({
                    "semester_raw": semester_raw,
                    "norm_sem": norm_sem,
                    "text": response.text
                })
            except Exception as e:
                safe_print(f"  ❌ [{program_name}] 處理 {semester_raw} 發生錯誤: {e}")
                
        try:
            execute_with_retry(client.files.delete, name=sample_file.name)
        except Exception as e:
            safe_print(f"  ⚠️ [{program_name}] 清理遠端檔案失敗: {e}")
            
        return results
    except Exception as e:
        safe_print(f"AI Error for {program_name}: {e}")
        return []

# ==========================================
# 3. 主執行迴圈 (多執行緒架構)
# ==========================================
def run_processing_loop(to_process, force_update=False, ci_mode=False):
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
    else:
        metadata = {}

    client_queue, num_workers = init_clients(ci_mode=ci_mode)
    global_ai_count = 0

    def worker(item):
        nonlocal global_ai_count
        name = item['name']
        url = item['url']
        size = item['size']
        needs_ai = item['needs_ai']
        
        with META_LOCK:
            if name not in metadata:
                metadata[name] = {"size": 0, "last_updated": "", "semesters": {}}
            elif "semesters" not in metadata[name]:
                metadata[name]["semesters"] = {}
            recorded_semesters = metadata[name]["semesters"].copy()
            
        if not needs_ai and not force_update:
            safe_print(f"⏭️  Skipped {name} (PDF Size unchanged).")
            with META_LOCK:
                metadata[name]["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(METADATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, ensure_ascii=False, indent=4)
            return

        client = client_queue.get()
        try:
            pdf_filename = os.path.join(BASE_DIR, f"temp_{uuid.uuid4().hex[:8]}.pdf")
            
            if download_pdf(url, pdf_filename):
                safe_print(f"\n──────────────────────────────────────────────────\n正在處理：{name} (強制更新: {force_update})\n──────────────────────────────────────────────────")
                
                results = analyze_pdf_with_gemini(client, pdf_filename, name, recorded_semesters, force_update, ci_mode=ci_mode)
                current_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                if results is not None: 
                    with META_LOCK:
                        for g_res in results:
                            norm_sem = g_res['norm_sem']
                            
                            clean_name = get_clean_name(name)
                            safe_name = "".join([c for c in clean_name if c.isalnum() or c in (' ', '.', '_')]).strip()
                            txt_filename = f"{norm_sem}-{safe_name}.txt"
                            
                            with open(os.path.join(DATA_DIR, txt_filename), 'w', encoding='utf-8') as f:
                                f.write(g_res['text'])
                            
                            metadata[name]["semesters"][norm_sem] = current_time_str
                            safe_print(f"  ✅ [{name}] 已儲存至 {txt_filename}")
                            
                            global_ai_count += 1
                        
                        metadata[name]["size"] = size
                        metadata[name]["last_updated"] = current_time_str
                        with open(METADATA_FILE, 'w', encoding='utf-8') as f:
                            json.dump(metadata, f, ensure_ascii=False, indent=4)
                
                if os.path.exists(pdf_filename):
                    os.remove(pdf_filename)
        finally:
            client_queue.put(client)

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(worker, item) for item in to_process]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                safe_print(f"❌ 執行緒發生預期外錯誤: {e}")

    safe_print(f"\n🎉 Finished. 實際執行解析的學期總數 (AI 消耗次數): {global_ai_count}")
    safe_print(f"檔案存放於: {DATA_DIR}")

# ==========================================
# 4. CLI Entry Point
# ==========================================
def main():
    parser = argparse.ArgumentParser(description="Process NSYSU program PDFs")
    parser.add_argument("--ci", action="store_true", help="CI mode: skip interactive prompts")
    parser.add_argument("--force", action="store_true", help="Force re-process all programs")
    parser.add_argument("--changed-file", type=str, default=None, help="Read changed program list from a JSON file")
    args = parser.parse_args()

    to_process = []

    if args.changed_file:
        with open(args.changed_file, 'r', encoding='utf-8') as f:
            changed_list = json.load(f)
        for entry in changed_list:
            to_process.append({
                "name": get_clean_name(entry["name"]),
                "url": entry.get("url") or entry.get("link", ""),
                "size": entry.get("size", 0),
                "needs_ai": True,
            })
        safe_print(f"📂 Loaded {len(to_process)} programs from {args.changed_file}")
    else:
        all_programs = scrape_nsysu_programs()
        metadata = {}
        if os.path.exists(METADATA_FILE):
            with open(METADATA_FILE, 'r', encoding='utf-8') as f:
                metadata = json.load(f)

        for prog in all_programs:
            raw_name = prog["name"]
            name = get_clean_name(raw_name)
            url = prog["link"]
            size = get_pdf_size(url)
            stored_size = metadata.get(name, {}).get("size", 0)
            needs_ai = args.force or (size != stored_size)
            to_process.append({
                "name": name,
                "url": url,
                "size": size,
                "needs_ai": needs_ai,
            })

        changed_count = sum(1 for p in to_process if p["needs_ai"])
        safe_print(f"📊 Found {len(to_process)} programs, {changed_count} need processing.")

    run_processing_loop(to_process, force_update=args.force, ci_mode=args.ci)

if __name__ == "__main__":
    main()