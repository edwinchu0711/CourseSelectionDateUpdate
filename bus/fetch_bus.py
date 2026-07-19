import json
import os
import sys
import requests
import urllib3

# 關閉 SSL 驗證警告（僅測試用）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL = "https://ibus.tbkc.gov.tw/ibus/graphql"

# 1. 目標關鍵字清單
TARGETS = ["中山大學", "哨船街", "濱海二路", "五福瀨南街口", "國際商場"]

# 定義存檔路徑 (bus/bus.json)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(SCRIPT_DIR, "bus.json")

# 建立 Session
session = requests.Session()
session.verify = False  # 關閉 SSL 驗證（測試用）
session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/json"
})

# -------------------------
# Step 1：取得所有路線
# -------------------------
routes_query = """
query($lang: String!) {
  routes(lang: $lang) {
    edges {
      node {
        id
        name
        departure
        destination
      }
    }
  }
}
"""

try:
    r = session.post(
        URL,
        json={
            "query": routes_query,
            "variables": {"lang": "zh"}
        },
        timeout=15
    )

    r.raise_for_status()
    data = r.json()
    routes_edges = data.get("data", {}).get("routes", {}).get("edges", [])

    if not routes_edges:
        print("取得路線失敗：API 回傳空路線列表。保留原 JSON 檔案。")
        sys.exit(0)

except Exception as e:
    print(f"取得路線失敗：{e}。保留原 JSON 檔案。")
    sys.exit(0)

route_ids = [
    int(edge["node"]["id"])
    for edge in routes_edges
    if edge.get("node") and "id" in edge["node"]
]

print(f"共 {len(route_ids)} 條路線，開始搜尋站名...\n")

# -------------------------
# Step 2：逐條查詢站牌
# -------------------------
station_query = """
query($xno: Int!) {
  route(xno: $xno, lang: "zh") {
    name
    stations {
      edges {
        node {
          id
          name
        }
      }
    }
  }
}
"""

matched = []

for xno in route_ids:
    try:
        r = session.post(
            URL,
            json={
                "query": station_query,
                "variables": {
                    "xno": xno
                }
            },
            timeout=15
        )

        route_data = r.json().get("data", {}).get("route")

        if route_data is None:
            continue

        stop_names = [
            s["node"]["name"]
            for s in route_data.get("stations", {}).get("edges", [])
            if s.get("node") and "name" in s["node"]
        ]

        # 比對邏輯：找出這條路線中了哪些關鍵字
        hit_targets = [target for target in TARGETS if any(target in name for name in stop_names)]

        if hit_targets:
            # 儲存 路線ID、路線名稱、以及被命中的關鍵字
            matched.append((xno, route_data["name"], hit_targets))
            print(f"[命中] xno={xno} {route_data['name']} (符合: {', '.join(hit_targets)})")

    except Exception as e:
        print(f"路線 {xno} 查詢失敗：{e}")

# -------------------------
# Step 3：輸出與儲存結果
# -------------------------
print("\n==================================================")
print("搜尋結果（包含目標站點的公車路線）")
print("==================================================")

# 若抓取結果為空，不覆蓋之前的 json 檔案
if not matched:
    print("沒有找到符合的路線或抓取資料為空。保留原 JSON 檔案不予覆蓋。")
    sys.exit(0)

# 排序邏輯：包含 "中山大學" 的存在最前面 (key 0)，其餘排後面 (key 1)，次要排序依路線名稱 (name)
sorted_matched = sorted(matched, key=lambda x: (0 if "中山大學" in x[2] else 1, x[1]))

for xno, name, hits in sorted_matched:
    hits_str = ", ".join(hits)
    print(f"xno={xno:<4} {name:<12} [符合關鍵字: {hits_str}]")

# 準備寫入 JSON 的資料結構
output_data = [
    {
        "xno": xno,
        "name": name,
        "matched_targets": hits
    }
    for xno, name, hits in sorted_matched
]

try:
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"\n成功將 {len(output_data)} 筆路線資料儲存至 {JSON_FILE}")
except Exception as e:
    print(f"寫入 JSON 檔案失敗：{e}")
