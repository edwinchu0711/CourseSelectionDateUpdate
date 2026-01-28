import os
import json
import time
import requests
import urllib3
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime, timedelta, timezone
from google import genai
from google.genai import types

# 關閉 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_dynamic_pdf_url():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--disable-gpu")
    
    try:
        # 在 GitHub Actions 中，webdriver 通常已經預裝，直接啟動即可
        driver = webdriver.Chrome(options=chrome_options)
        base_url = "https://selcrs.nsysu.edu.tw/"
        print(f"正在訪問: {base_url}")
        driver.get(base_url)
        
        wait = WebDriverWait(driver, 20)
        link_element = wait.until(EC.presence_of_element_located((By.PARTIAL_LINK_TEXT, "選課須知")))
        next_url = link_element.get_attribute("href")
        
        print(f"跳轉至: {next_url}")
        driver.get(next_url)
        time.sleep(3) 
        
        pdf_links = driver.find_elements(By.TAG_NAME, "a")
        for link in pdf_links:
            href = link.get_attribute("href")
            text = link.text
            if href and ".pdf" in href.lower() and "選課須知" in text:
                print(f"✅ 找到 PDF: {href}")
                return href
        return None
    except Exception as e:
        print(f"❌ Selenium 錯誤: {e}")
        return None
    finally:
        if 'driver' in locals():
            driver.quit()

def main():
    print("🚀 開始執行 GitHub Action 自動化爬蟲...")
    
    # 1. 抓取 PDF URL
    pdf_url = get_dynamic_pdf_url()
    if not pdf_url:
        print("❌ 無法取得 PDF URL，任務終止")
        exit(1)
    
    # 2. 下載 PDF
    pdf_filename = "latest_course_info.pdf"
    try:
        response = requests.get(pdf_url, verify=False, timeout=60)
        with open(pdf_filename, "wb") as f:
            f.write(response.content)
    except Exception as e:
        print(f"❌ PDF 下載失敗: {e}")
        exit(1)

    # 3. AI 處理 (Gemini)
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("❌ 找不到 GEMINI_API_KEY 環境變數")
            exit(1)
            
        client = genai.Client(api_key=api_key)
        uploaded_file = client.files.upload(file=pdf_filename)
        
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(3)
            uploaded_file = client.files.get(name=uploaded_file.name)

       prompt = """請閱讀這份選課須知 PDF，提取出以下項目的具體時間（包含日期與時段），並嚴格以 JSON 格式回傳。如果文件中有多個時段（例如不同年級），請一併列出。 需求項目：1.課程查詢2.初選一3.初選一公佈4.初選二5.初選二公佈6.加退選一7.加退選一公佈8.加退選二9.加退選二公佈 10.異常處理
                11.選課確認
                12.棄選時間  
                13.必修課程確認
                14.系所輔導學生選課
                15.超修學分申請
                (就以上15個，不要其他的)
                「課程查詢」的這個標題前面可以保留學年度，例如"110-1 課程查詢"然後每一項就都有開始時間，結束時間，若是只有其中一個那就是開始時間有，然後結束時間就空白範例格式：{"114-2 課程查詢": { "開始時間": "115年1/6(二) 13:00", "結束時間": "" },"初選一": { "開始時間": "1/30(五) 09:00", "結束時間": "2/2(一) 17:00" }}(日期間不要有空白 只有星期後和時間前可以有一個空白)最後加上一個"更新時間"欄位，填入目前的日期時間。"""
        response = client.models.generate_content(
            model="gemini-flash-lite-latest",
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=uploaded_file.mime_type),
                        types.Part.from_text(text=prompt),
                    ],
                ),
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )

        data_dict = json.loads(response.text.strip())
        
        # --- 核心過濾邏輯：移除包含「校際選課申請」的資料 ---
        # 因為產出是字典，我們過濾 Key 值
        filtered_data = {k: v for k, v in data_dict.items() if "校際選課申請" not in k}
        
        result = {
            "data": filtered_data,
            "source_url": pdf_url,
            "update_time": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        }

        # 寫入 data.json
        with open('data.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
            
        print("✅ 資料處理成功並已過濾，準備提交。")

    except Exception as e:
        print(f"❌ AI 處理失敗: {e}")
        exit(1)

if __name__ == "__main__":
    main()
