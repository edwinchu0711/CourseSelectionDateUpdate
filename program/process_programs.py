import os
import json
import time
import requests
import datetime
from google import genai
from 學程 import scrape_nsysu_programs
import sys

# Constants
METADATA_FILE = "metadata.json"
DATA_DIR = "data"
MODEL_NAME = "gemini-flash-lite-latest"
API_KEY = os.environ.get("GEMINI_API_KEY")
INTERVAL = 6

def get_client():
    if not API_KEY:
        print("Error: GEMINI_API_KEY environment variable not set.")
        sys.exit(1)
    return genai.Client(api_key=API_KEY)

def get_pdf_size(url):
    try:
        response = requests.head(url, allow_redirects=True)
        return int(response.headers.get('content-length', 0))
    except Exception as e:
        print(f"Error getting PDF size for {url}: {e}")
        return 0

def download_pdf(url, filename):
    try:
        response = requests.get(url)
        with open(filename, 'wb') as f:
            f.write(response.content)
        return True
    except Exception as e:
        print(f"Error downloading PDF from {url}: {e}")
        return False

def analyze_pdf_with_gemini(client, pdf_path, program_name):
    print(f"Analyzing {program_name} with Gemini (google-genai)...")
    
    # Upload file to Gemini
    try:
        sample_file = client.files.upload(file=pdf_path)
        
        # In the new SDK, we can wait for processing if needed, 
        # but for simple PDFs it's usually immediate or handled by the backend.
        # However, to be safe:
        while sample_file.state == "PROCESSING":
            time.sleep(2)
            sample_file = client.files.get(name=sample_file.name)
            
        if sample_file.state == "FAILED":
            raise Exception("File processing failed")

        prompt = f"""你是一位專業的文件整理助手。正在分析的學程名稱是：{program_name}。
請將以下學程課程規劃 PDF 的所有內容，完整整理為結構化 Markdown 格式，規則如下：

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
        
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[sample_file, prompt]
        )
        
        # Delete the file from Gemini storage to clean up
        client.files.delete(name=sample_file.name)
        
        return response.text
    except Exception as e:
        print(f"AI Error for {program_name}: {e}")
        return None

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "partial" # "full" or "partial"
    
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
    else:
        metadata = {}

    client = get_client()
    
    print("Scraping programs...")
    all_programs = scrape_nsysu_programs()
    print(f"Found {len(all_programs)} programs.")

    to_process = []
    
    if mode == "full":
        # Process everything that HAS changed
        for prog in all_programs:
            name = prog['name']
            url = prog['link']
            current_size = get_pdf_size(url)
            
            if name not in metadata or metadata[name].get('size') != current_size:
                to_process.append({
                    "name": name,
                    "url": url,
                    "size": current_size,
                    "needs_ai": True
                })
            else:
                # Even if size is same, we update the last_updated time to show it was checked
                metadata[name]["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Save metadata for those we skipped
        with open(METADATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=4)
            
    else:
        # Partial: Pick 5 items total
        # Priority:
        # 1. New items (not in metadata)
        # 2. Changed items (size different)
        # 3. Oldest items
        
        items_with_info = []
        for prog in all_programs:
            name = prog['name']
            url = prog['link']
            
            if name not in metadata:
                priority = 0 # New
                last_updated = "1970-01-01 00:00:00"
            else:
                # We don't know the size yet without a request, but we can check metadata
                priority = 1
                last_updated = metadata[name].get('last_updated', "1970-01-01 00:00:00")
            
            items_with_info.append({
                "name": name,
                "url": url,
                "priority": priority,
                "last_updated": last_updated
            })
            
        # Sort by priority, then by last_updated
        items_with_info.sort(key=lambda x: (x['priority'], x['last_updated']))
        
        # Take top 5
        to_process_candidates = items_with_info[:5]
        
        for item in to_process_candidates:
            name = item['name']
            url = item['url']
            current_size = get_pdf_size(url)
            
            needs_ai = False
            if name not in metadata or metadata[name].get('size') != current_size:
                needs_ai = True
                
            to_process.append({
                "name": name,
                "url": url,
                "size": current_size,
                "needs_ai": needs_ai
            })

    print(f"Mode: {mode}. Items to process: {len(to_process)}")

    ai_count = 0
    for i, item in enumerate(to_process):
        name = item['name']
        url = item['url']
        size = item['size']
        needs_ai = item['needs_ai']
        
        if needs_ai:
            pdf_filename = f"temp_{int(time.time())}.pdf"
            if download_pdf(url, pdf_filename):
                result = analyze_pdf_with_gemini(client, pdf_filename, name)
                if result:
                    # Save data
                    safe_name = "".join([c for c in name if c.isalnum() or c in (' ', '.', '_')]).strip()
                    with open(os.path.join(DATA_DIR, f"{safe_name}.txt"), 'w', encoding='utf-8') as f:
                        f.write(result)
                    
                    # Update metadata
                    metadata[name] = {
                        "size": size,
                        "last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    print(f"Successfully updated {name} (AI analysis completed)")
                    ai_count += 1
                
                if os.path.exists(pdf_filename):
                    os.remove(pdf_filename)
        else:
            # Size is the same, strictly skip AI but update timestamp
            metadata[name]["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"Skipped AI for {name} (Size unchanged), but updated timestamp.")

        # Save metadata after each item to be safe
        with open(METADATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=4)
        
        # Interval only if we actually did an AI request and there are more items
        if needs_ai and i < len(to_process) - 1:
            print(f"Waiting {INTERVAL} seconds...")
            time.sleep(INTERVAL)

    print(f"Finished. Total AI requests made: {ai_count}")


if __name__ == "__main__":
    main()
