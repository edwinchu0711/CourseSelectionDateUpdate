import os
import sys
from dotenv import load_dotenv
from 學程 import scrape_nsysu_programs
from process_programs import run_processing_loop, get_pdf_size

def main():
    # Load .env file
    load_dotenv()
    
    # Needs GEMINI_API_KEY
    if not os.environ.get("GEMINI_API_KEY"):
        print("⚠️ 警告：找不到 GEMINI_API_KEY 環境變數。")
        print("請在 .env 檔案中設定 GEMINI_API_KEY=your_key")
        return

    try:
        count_str = input("請輸入最多要抓取幾個學程 PDF (輸入 -1 代表處理全部)：").strip()
        if not count_str:
            print("未輸入數量，結束。")
            return
        count = int(count_str)
    except ValueError:
        print("輸入無效，請輸入數字。")
        return

    print("🔍 正在透過學程.py抓取資料...")
    all_programs = scrape_nsysu_programs()
    if not all_programs:
        print("❌ 找不到任何學程資料！")
        return

    if count == -1:
        programs_to_process = all_programs
    else:
        programs_to_process = all_programs[:count]
        
    print(f"📌 將處理 {len(programs_to_process)} 個學程。")
    
    to_process = []
    for prog in programs_to_process:
        to_process.append({
            "name": prog['name'],
            "url": prog['link'],
            "size": get_pdf_size(prog['link']),
            "needs_ai": True
        })
        
    run_processing_loop(to_process)

if __name__ == "__main__":
    main()