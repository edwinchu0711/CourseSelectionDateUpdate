import os
import json
from datetime import datetime, timedelta, timezone
from google import genai
from google.genai import types


def validate_schedule_json(json_path):
    """
    檢查 selection_schedule.json 的格式是否正確。

    檢查規則：
    1. 檔案必須存在
    2. 必須是合法 JSON
    3. 最外層必須是 dict
    4. 除了「更新時間」以外，每個項目都必須是 dict
    5. 每個項目都必須包含「開始時間」與「結束時間」
    6. 若某一項的「開始時間」和「結束時間」同時為空，則視為錯誤
    """

    # 檢查 JSON 檔案是否存在
    if not os.path.exists(json_path):
        print(f"❌ JSON 檢查失敗: 找不到檔案 {json_path}")
        return False

    try:
        # 嘗試讀取 JSON 檔案
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

    except json.JSONDecodeError as e:
        # 如果 JSON 格式錯誤，會進到這裡
        print(f"❌ JSON 檢查失敗: JSON 格式不正確")
        print(f"錯誤位置: line {e.lineno}, column {e.colno}")
        print(f"錯誤訊息: {e.msg}")
        return False

    except Exception as e:
        # 其他讀檔錯誤
        print(f"❌ JSON 檢查失敗: 無法讀取 JSON 檔案")
        print(f"錯誤訊息: {e}")
        return False

    # 檢查最外層是不是 JSON object，也就是 Python 的 dict
    if not isinstance(data, dict):
        print("❌ JSON 檢查失敗: 最外層格式必須是 JSON object")
        return False

    # 逐一檢查每個 key-value
    for item_name, item_value in data.items():

        # 「更新時間」不是時程項目，可以跳過
        if item_name == "更新時間":
            continue

        # 每個時程項目的 value 必須是 dict
        if not isinstance(item_value, dict):
            print(f"❌ JSON 檢查失敗:「{item_name}」的內容必須是物件格式")
            return False

        # 檢查是否有「開始時間」欄位
        if "開始時間" not in item_value:
            print(f"❌ JSON 檢查失敗:「{item_name}」缺少「開始時間」欄位")
            return False

        # 檢查是否有「結束時間」欄位
        if "結束時間" not in item_value:
            print(f"❌ JSON 檢查失敗:「{item_name}」缺少「結束時間」欄位")
            return False

        # 取出開始時間，並確保不是 None
        start_time = item_value.get("開始時間") or ""

        # 取出結束時間，並確保不是 None
        end_time = item_value.get("結束時間") or ""

        # 去除前後空白
        start_time = str(start_time).strip()
        end_time = str(end_time).strip()

        # 如果開始時間和結束時間都是空的，就不允許 push
        if start_time == "" and end_time == "":
            print(
                f"❌ JSON 檢查失敗:「{item_name}」的"
                f"「開始時間」與「結束時間」不能同時為空"
            )
            return False

    print("✅ JSON 檢查通過，可以進行 push")
    return True


def main():
    print("🚀 開始執行選課時程 LLM 分析...")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ 錯誤: 找不到環境變數 GEMINI_API_KEY")
        exit(1)

    base_dir = os.path.dirname(os.path.abspath(__file__))

    md_path = os.path.join(base_dir, "course-selection.md")

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
        print(
            f"⚠️ 偵測到 md 檔案字數較多 ({original_len} 字)，"
            f"已截斷至前 {len(md_content)} 字以節省 Token。"
        )

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
12. 棄選時間

格式要求(注意!)：
- 回傳格式必須為 JSON 對象，Key 為時程項目名稱，Value 為包含「開始時間」、「結束時間」的對象。
- 日期格式保留原文件中的樣式（包含年份、月份、日期、時間，請勿包含星期），例如："115/6/24 13:00" 或 "115/8/20 09:00"(也要注意有沒有跨年，年份可能會有不同)。
- 結束時間欄位如果沒有明確的結束時間，結束時間欄位請留空。
- "課程查詢" 欄位名稱前面請保留學年度，例如 "114-2 課程查詢"。
- 額外加上一個 "更新時間" 欄位，填入最新的系統處理時間。
- 語言限定繁體中文

回傳範例格式：
{
  "114-2 課程查詢": {
    "開始時間": "114/2/21 13:00",
    "結束時間": ""
  },
  "初選一": {
    "開始時間": "114/3/20 09:00",
    "結束時間": "114/3/21 17:00"
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
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(
                    thinking_level="high"
                )
            )
        )

        raw_text = response.text.strip()
        data_dict = json.loads(raw_text)

        # 確保有更新時間
        if "更新時間" not in data_dict:
            data_dict["更新時間"] = datetime.now(
                timezone(timedelta(hours=8))
            ).strftime("%Y-%m-%d %H:%M:%S")

        output_path = os.path.join(base_dir, "selection_schedule.json")

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data_dict, f, ensure_ascii=False, indent=2)

        print(f"✅ 選課時程已成功提取並儲存至 {output_path}")

        # 寫入後立刻檢查 JSON
        if not validate_schedule_json(output_path):
            print("🛑 因 JSON 檢查未通過，停止流程，不進行 push。")
            exit(1)

        # 如果你的 push 是在 GitHub Action 後面的 step 做，
        # 那這支程式 exit(0) 才會讓後續 step 繼續執行。
        print("✅ JSON 檢查完成，後續可以進行 push。")

    except Exception as e:
        print(f"❌ AI 分析或寫入失敗: {e}")
        exit(1)


if __name__ == "__main__":
    main()
