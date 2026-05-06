import requests
from bs4 import BeautifulSoup
import urllib.parse
import sys

# Ensure the console output handles UTF-8 correctly (fixes UnicodeEncodeError on Windows)
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        # Fallback for older Python versions
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def scrape_nsysu_programs():
    url = "https://ctdr.nsysu.edu.tw/class2.php"
    programs = []
    
    try:
        response = requests.get(url)
        response.encoding = response.apparent_encoding
        response.raise_for_status()
    except Exception as e:
        print(f"無法存取網頁: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Find all tables with class "plan"
    tables = soup.find_all('table', class_='plan')
    
    if not tables:
        print("找不到任何資料表格")
        return []

    for i, table in enumerate(tables):
        # Skip the last table (Discontinued Programs)
        if i == len(tables) - 1:
            continue
            
        rows = table.find_all('tr')
        for row in rows:
            cols = row.find_all(['td', 'th'])
            
            # Skip header rows
            if row.get('bgcolor') == '#FFFF99' or row.find('th'):
                continue
                
            # Handle the first 3 tables (Standard Programs)
            if len(cols) >= 5:
                name = cols[0].get_text(strip=True)
                features = cols[1].get_text(strip=True)
                
                link_tag = cols[4].find('a')
                link = ""
                if link_tag and 'href' in link_tag.attrs:
                    link = urllib.parse.urljoin(url, link_tag['href'])
                
                if name and link:
                    programs.append({
                        "name": name,
                        "features": features,
                        "link": link,
                        "status": "active"
                    })

    return programs

if __name__ == "__main__":
    all_programs = scrape_nsysu_programs()
    for prog in all_programs:
        print(f"Name: {prog['name']}, Link: {prog['link']}")

