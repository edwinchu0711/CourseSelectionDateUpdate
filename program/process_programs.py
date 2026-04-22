import os
import json
import time
import requests
import datetime
import google.generativeai as genai
from 學程 import scrape_nsysu_programs
import sys

# Constants
METADATA_FILE = "metadata.json"
DATA_DIR = "data"
MODEL_NAME = "gemini-flash-lite-latest"
API_KEY = os.environ.get("GEMINI_API_KEY")
INTERVAL = 6

def setup_genai():
    if not API_KEY:
        print("Error: GEMINI_API_KEY environment variable not set.")
        sys.exit(1)
    genai.configure(api_key=API_KEY)

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

def analyze_pdf_with_gemini(pdf_path, program_name):
    print(f"Analyzing {program_name} with Gemini...")
    model = genai.GenerativeModel(MODEL_NAME)
    
    # Upload file to Gemini
    try:
        sample_file = genai.upload_file(path=pdf_path, display_name=program_name)
        
        # Wait for file to be processed
        while sample_file.state.name == "PROCESSING":
            time.sleep(2)
            sample_file = genai.get_file(sample_file.name)
            
        if sample_file.state.name == "FAILED":
            raise Exception("File processing failed")

        prompt = f"請詳細記錄並分析這個學程（{program_name}）的內容，包含學程目標、修課規定、應修科目等詳細資訊。"
        
        response = model.generate_content([sample_file, prompt])
        
        # Delete the file from Gemini storage to clean up
        genai.delete_file(sample_file.name)
        
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

    setup_genai()
    
    print("Scraping programs...")
    all_programs = scrape_nsysu_programs()
    print(f"Found {len(all_programs)} programs.")

    to_update = []
    
    # Check which ones need update
    for prog in all_programs:
        name = prog['name']
        url = prog['link']
        current_size = get_pdf_size(url)
        
        needs_update = False
        if name not in metadata:
            needs_update = True
        elif metadata[name].get('size') != current_size:
            needs_update = True
            
        if needs_update:
            to_update.append({
                "name": name,
                "url": url,
                "size": current_size,
                "priority": 0 # New or changed
            })
        else:
            # For partial update, we might need to pick the oldest ones if we don't have enough new ones
            last_updated = metadata[name].get('last_updated', "1970-01-01 00:00:00")
            to_update.append({
                "name": name,
                "url": url,
                "size": current_size,
                "priority": 1, # Existing
                "last_updated": last_updated
            })

    # Decide final list based on mode
    if mode == "full":
        # Only update those that actually need update (size changed or new)
        final_list = [p for p in to_update if p['priority'] == 0]
    else:
        # Partial: 5 items
        # Priority: New/Changed first, then Oldest
        new_or_changed = [p for p in to_update if p['priority'] == 0]
        if len(new_or_changed) >= 5:
            final_list = new_or_changed[:5]
        else:
            existing = [p for p in to_update if p['priority'] == 1]
            # Sort by last_updated
            existing.sort(key=lambda x: x['last_updated'])
            final_list = new_or_changed + existing[:(5 - len(new_or_changed))]

    print(f"Mode: {mode}. Items to update: {len(final_list)}")

    for i, item in enumerate(final_list):
        name = item['name']
        url = item['url']
        size = item['size']
        
        pdf_filename = f"temp_{int(time.time())}.pdf"
        if download_pdf(url, pdf_filename):
            result = analyze_pdf_with_gemini(pdf_filename, name)
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
                
                # Save metadata immediately
                with open(METADATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, ensure_ascii=False, indent=4)
                    
                print(f"Successfully updated {name}")
            
            if os.path.exists(pdf_filename):
                os.remove(pdf_filename)
        
        # Interval
        if i < len(final_list) - 1:
            print(f"Waiting {INTERVAL} seconds...")
            time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
