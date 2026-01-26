import requests
from bs4 import BeautifulSoup
import re
import json
import os
from datetime import datetime
from icalendar import Calendar
import urllib.parse

# 設定檔案名稱
OUTPUT_FILE = 'calendar.json'

# 目標網站 (起始點)
START_URL = "https://selcrs.nsysu.edu.tw/"
# 為了處理相對路徑，定義 domain
BASE_DOMAIN = "https://oaa.nsysu.edu.tw"

def get_soup(url, encoding='utf-8'):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=30, verify=False) # 學校網站憑證有時會有問題，verify=False 較保險
        response.encoding = encoding
        return BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def parse_roc_date(date_str):
    """將 114.06.26 格式轉換為可用於排序的 tuple 或數字"""
    try:
        parts = date_str.split('.')
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except:
        return (0, 0, 0)

def main():
    print("--- 開始執行爬蟲 ---")

    # 1. 進入 selcrs 找尋「行事曆」連結
    soup = get_soup(START_URL, encoding='utf-8') # selcrs 通常是 utf-8
    if not soup: return

    calendar_link = None
    # 尋找文字包含 "行事曆" 的連結
    for a in soup.find_all('a', href=True):
        if "行事曆" in a.get_text():
            calendar_link = a['href']
            break
    
    if not calendar_link:
        print("找不到「行事曆」連結")
        return

    print(f"找到行事曆入口: {calendar_link}")

    # 2. 進入行事曆列表頁面
    soup = get_soup(calendar_link, encoding='utf-8')
    if not soup: return

    # 3. 尋找所有「中文版ICAL檔」並解析日期
    ical_candidates = []
    
    # 針對連結文字做篩選
    for a in soup.find_all('a', href=True):
        text = a.get_text().strip()
        if "中文版ICAL檔" in text:
            # 使用 Regex 抓取括號內的日期 (如 114.06.26)
            match = re.search(r'\((\d{3}\.\d{1,2}\.\d{1,2})更新\)', text)
            if match:
                version_date = match.group(1)
                ical_candidates.append({
                    'text': text,
                    'url': a['href'],
                    'version': version_date,
                    'version_tuple': parse_roc_date(version_date)
                })

    if not ical_candidates:
        print("未找到任何 ICAL 下載連結")
        return

    # 4. 選取最新的版本
    # 根據 version_tuple 進行排序，取最大值
    newest = sorted(ical_candidates, key=lambda x: x['version_tuple'], reverse=True)[0]
    print(f"最新版本為: {newest['text']} (版本日期: {newest['version']})")

    # 5. 檢查是否需要更新
    # 讀取現有的 json 檢查上次更新的版本
    current_data = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                current_data = json.load(f)
        except:
            pass
    
    last_saved_version = current_data.get('metadata', {}).get('source_version', '')

    if last_saved_version == newest['version']:
        print(f"版本 {newest['version']} 已存在，無需更新。")
        return # 結束程式

    print(f"發現新版本 (舊: {last_saved_version} -> 新: {newest['version']})，準備下載...")

    # 6. 進入該版本的內頁，尋找 .ics 檔案連結
    # 處理相對路徑
    target_page_url = newest['url']
    if not target_page_url.startswith('http'):
        target_page_url = urllib.parse.urljoin(BASE_DOMAIN, target_page_url)
        
    soup = get_soup(target_page_url)
    if not soup: return

    ics_download_url = None
    for a in soup.find_all('a', href=True):
        # 檢查連結結尾是否為 .ics (忽略 query string 影響，只看路徑或文字特徵)
        # 根據你的描述，連結文字包含 .ics 且 href 是一長串下載 script
        if ".ics" in a.get_text() or a['href'].lower().endswith('.ics'):
            ics_download_url = a['href']
            break
    
    if not ics_download_url:
        print("在內頁找不到 .ics 檔案下載連結")
        return

    # 處理下載連結的相對路徑
    if not ics_download_url.startswith('http'):
        ics_download_url = urllib.parse.urljoin(BASE_DOMAIN, ics_download_url)

    # 7. 下載並轉換 ICS -> JSON
    print(f"下載 ICS: {ics_download_url}")
    ics_resp = requests.get(ics_download_url, verify=False)
    
    if ics_resp.status_code != 200:
        print("下載失敗")
        return

    # 解析 ICS
    cal = Calendar.from_ical(ics_resp.content)
    events_list = []

    for component in cal.walk():
        if component.name == "VEVENT":
            event = {}
            # 摘要
            event['summary'] = str(component.get('summary', ''))
            
            # 處理時間 (可能只有日期，也可能有時間)
            dtstart = component.get('dtstart')
            if dtstart:
                event['start'] = dtstart.dt.isoformat()
            
            dtend = component.get('dtend')
            if dtend:
                event['end'] = dtend.dt.isoformat()
                
            description = component.get('description')
            if description:
                event['description'] = str(description)
            
            location = component.get('location')
            if location:
                event['location'] = str(location)

            events_list.append(event)

    # 8. 建構最終資料結構並存檔
    final_output = {
        "metadata": {
            "source_version": newest['version'], # 用於比對是否需要更新
            "source_title": newest['text'],
            "github_fetched_at": datetime.now().isoformat(), # GitHub 抓取時間
        },
        "events": events_list
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=2)
    
    print(f"成功更新 {OUTPUT_FILE}")

if __name__ == "__main__":
    # 抑制 SSL 警告 (因為學校網站常有這問題)
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    main()
