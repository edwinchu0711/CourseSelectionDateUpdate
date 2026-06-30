import os
import json
from google import genai
from google.genai import types

def main():
    print("🚀 開始執行選課時程 LLM 分析...")
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ 錯誤: 找不到環境變數 GEMINI_API_KEY")
        exit(1)
        
    md_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "course-selection.md")
    if not os.path.exists(md_path):
        print(f"❌ 錯誤: 找不到 Markdown 檔案 {md_path}")
        exit(1)
        
    with open(md_path, "r", encoding="utf-8") as f:
        md_content = f.read()
        
    # 根據字數長度截斷以減少 token 使用
    original_len = len(md_content)
    if original_len > 20000:
        md_content = md_content[:7000]
    elif original_len > 15000:
        md_content = md_content[:5500]
    elif original_len > 10000:
        md_content = md_content[:4000]
    elif original_len > 8000:
        md_content = md_content[:3500]
        
    if len(md_content) < original_len:
        print(f"⚠️ 偵測到 md 檔案字數較多 ({original_len} 字)，已截斷至前 {len(md_content)} 字以節省 Token。")
        
    client = genai.Client(api_key=api_key)
    
    prompt = """
請閱讀這份選課須知 Markdown 內容，從中提取出「選課相關時程」，並嚴格以指定的 JSON 格式回傳。
如果文件中有同一項目的多個時段（例如不同年級），請一併列出。

主要提取項目：
1. 課程查詢
2. 初選一
3. 初選一公佈
4. 初選二
5. 初選二公佈
6. 加退選一
7. 加退選一公佈 
8. 加退選二 
9. 加退選二公佈 
10. 異常處理
11. 選課確認
12. 學分費繳交
13. 棄選時間

格式要求(注意!)：
- 回傳格式必須為 JSON 對象，Key 為時程項目名稱，Value 為包含「開始時間」、「結束時間」的對象。
- 日期格式保留原文件中的樣式（包含月份、日期、時間，請勿包含年份與星期），例如："6/24 13:00" 或 "8/20 09:00"。
- 結束時間欄位如果沒有明確的結束時間，結束時間欄位請留空。
- "課程查詢" 欄位名稱前面請保留學年度，例如 "115-1 課程查詢"。
- 額外加上一個 "更新時間" 欄位，填入最新的系統處理時間。

回傳範例格式：
{
  "115-1 課程查詢": {
    "開始時間": "6/24 13:00",
    "結束時間": "",
  },
  "初選一": {
    "開始時間": "8/20 09:00",
    "結束時間": "8/21 17:00",
  }
}
"""

    try:
        response = client.models.generate_content(
            model="gemini-flash-lite-latest",
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=f"Markdown 內容:\n{md_content}"),
                        types.Part.from_text(text=prompt),
                    ],
                ),
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        
        raw_text = response.text.strip()
        data_dict = json.loads(raw_text)
        
        # 確保有更新時間
        from datetime import datetime, timedelta, timezone
        if "更新時間" not in data_dict:
            data_dict["更新時間"] = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
            
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selection_schedule.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data_dict, f, ensure_ascii=False, indent=2)
            
        print(f"✅ 選課時程已成功提取並儲存至 {output_path}")
        
    except Exception as e:
        print(f"❌ AI 分析或寫入失敗: {e}")
        exit(1)

if __name__ == "__main__":
    main()
