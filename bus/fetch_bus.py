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

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 建立 Session 並設定 Retry
session = requests.Session()
session.verify = False  # 關閉 SSL 驗證

retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))
session.mount('http://', HTTPAdapter(max_retries=retries))

session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://ibus.tbkc.gov.tw",
    "Referer": "https://ibus.tbkc.gov.tw/ibus/"
})

# -------------------------
# Helper: POST GraphQL with Proxy Fallback
# -------------------------
def post_graphql(query, variables, timeout=30):
    payload = {"query": query, "variables": variables}
    
    # 嘗試 1：直連 / TUN 模式網卡連線
    try:
        r = session.post(URL, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e1:
        # 嘗試 2：備用代理 - WARP 本地 SOCKS5 / HTTP Proxy
        proxies_to_try = [
            {"http": "socks5h://127.0.0.1:4000", "https": "socks5h://127.0.0.1:4000"},
            {"http": "http://127.0.0.1:4000", "https": "http://127.0.0.1:4000"},
            {"http": "http://127.0.0.1:4001", "https": "http://127.0.0.1:4001"},
        ]
        for px in proxies_to_try:
            try:
                r = requests.post(URL, json=payload, headers=session.headers, verify=False, timeout=timeout, proxies=px)
                r.raise_for_status()
                return r.json()
            except Exception:
                continue
        raise e1

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
    data = post_graphql(routes_query, {"lang": "zh"}, timeout=30)
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

total_routes = len(route_ids)
print("==================================================", flush=True)
print(f"成功取得路線清單！共 {total_routes} 條路線，開始逐一比對站牌...", flush=True)
print(f"目標搜尋關鍵字：{', '.join(TARGETS)}", flush=True)
print("==================================================\n", flush=True)

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

for idx, xno in enumerate(route_ids, 1):
    try:
        data = post_graphql(station_query, {"xno": xno}, timeout=30)
        route_data = data.get("data", {}).get("route")

        if route_data is None:
            if idx % 20 == 0 or idx == total_routes:
                print(f"[{idx}/{total_routes}] ⏳ 已掃描 {idx} 條路線... (目前累計命中 {len(matched)} 條)", flush=True)
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
            print(f"[{idx}/{total_routes}] 🎯 [命中] xno={xno:<4} {route_data['name']:<12} (符合: {', '.join(hit_targets)})", flush=True)
        elif idx % 20 == 0 or idx == total_routes:
            print(f"[{idx}/{total_routes}] ⏳ 已掃描 {idx} 條路線... (目前累計命中 {len(matched)} 條)", flush=True)

    except Exception as e:
        print(f"[{idx}/{total_routes}] ❌ 路線 {xno} 查詢失敗：{e}", flush=True)

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
