import json
import re
import argparse
import os
import sys
import time
from pathlib import Path

# ── 讀取 .env 檔案 ────────────────────────────────────────────────────────────
def load_dotenv(env_path: Path = Path(".env")) -> None:
    """手動解析 .env，不依賴 python-dotenv"""
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

load_dotenv()

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# ── 路徑常數 ──────────────────────────────────────────────────────────────────
DATA_DIR   = Path(__file__).parent / "data"
RULES_FILE = Path(__file__).parent / "rules" / "rules.json"

# ── System Prompt ─────────────────────────────────────────────────────────────
PROMPT_FILE = Path(__file__).parent / "prompt.txt"
if PROMPT_FILE.exists():
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read()
else:
    SYSTEM_PROMPT = ""


# ── 互動式輸入：base URL / model（程式啟動時執行）──────────────────────────
def prompt_connection_settings(default_base_url: str, default_model: str) -> tuple[str, str]:
    """
    在程式最開頭讓使用者輸入 base URL 與 model 名稱。
    直接按 Enter 則使用括號內的預設值。
    """
    print("=" * 60)
    print("  🔧 API 連線設定")
    print("=" * 60)

    # ── 輸入 base URL ────────────────────────────────────────────────────────
    while True:
        raw = input(f"  Base URL [{default_base_url}] : ").strip()
        base_url = raw if raw else default_base_url
        if base_url:
            break
        print("  ⚠️  Base URL 不可為空，請重新輸入")

    # ── 輸入 model 名稱 ──────────────────────────────────────────────────────
    while True:
        raw = input(f"  Model    [{default_model}] : ").strip()
        model = raw if raw else default_model
        if model:
            break
        print("  ⚠️  Model 名稱不可為空，請重新輸入")

    print()
    return base_url, model


# ── 從檔名萃取主要學程名稱提示 ───────────────────────────────────────────────
def extract_program_hint(filename: str) -> str:
    """
    從檔名萃取主要學程名稱，作為給 LLM 的提示。
    """
    name = filename

    # Step 1：移除開頭的「學年-學期-」前綴，例如 "108-2-"
    name = re.sub(r"^\d{3}-\d-", "", name).strip()

    # Step 2：遇到「兩個以上連續空白」就截斷（後面是備註）
    name = re.split(r"\s{2,}", name)[0].strip()

    # Step 3：若名稱已有中文字，遇到「空格+大寫英文字母」截斷
    if re.search(r"[\u4e00-\u9fff]", name):
        name = re.split(r"\s+(?=[A-Z])", name)[0].strip()

    # Step 4：移除常見的行政後綴
    suffixes = [
        r"\s*全英語學程.*$",
        r"\s*Program Taught.*$",
        r"\s*自\d+年.*$",
        r"\s*停止受理.*$",
    ]
    for pattern in suffixes:
        name = re.sub(pattern, "", name).strip()

    return name.strip()

# ── 讀取現有 rules.json ───────────────────────────────────────────────────────
def load_rules() -> list[dict]:
    """載入現有的 rules.json；若不存在或內容為空則回傳空列表"""
    if RULES_FILE.exists():
        with open(RULES_FILE, encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"⚠️  rules.json 格式錯誤，將備份後重新建立：{e}")
            backup = RULES_FILE.with_suffix(".broken.json")
            RULES_FILE.rename(backup)
            print(f"   📦 已備份至：{backup}")
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "programs" in data:
            return data["programs"]
    return []

# ── 儲存 rules.json ───────────────────────────────────────────────────────────
def save_rules(programs: list[dict]) -> None:
    """將程式列表存回 rules.json"""
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(programs, f, ensure_ascii=False, indent=2)

# ── 合併新資料至現有 programs 列表 ────────────────────────────────────────────
def merge_into_rules(programs: list[dict], new_data: dict) -> tuple[int, int]:
    """
    將 new_data 合併進 programs 列表。
    回傳 (added_versions, skipped_versions)
    """
    new_name     = new_data.get("program_name", "")
    new_versions = new_data.get("versions", [])
    added = skipped = 0

    existing_program = next(
        (p for p in programs if p.get("program_name") == new_name), None
    )

    if existing_program is None:
        programs.append(new_data)
        return len(new_versions), 0

    existing_versions = existing_program.setdefault("versions", [])

    for nv in new_versions:
        ny, ns = nv.get("academic_year"), nv.get("semester")
        duplicate = any(
            ev.get("academic_year") == ny and ev.get("semester") == ns
            for ev in existing_versions
        )
        if duplicate:
            skipped += 1
        else:
            existing_versions.append(nv)
            existing_versions.sort(
                key=lambda v: (v.get("academic_year", 0), v.get("semester", 0))
            )
            added += 1

    for key, value in new_data.items():
        if key not in ("versions", "program_name"):
            existing_program.setdefault(key, value)

    return added, skipped

# ── 單一檔案轉換（含 503 / rate-limit 重試邏輯）──────────────────────────────
def convert_file(
    txt_path: str,
    client: "OpenAI",
    model: str,
    max_retries: int = 5,
    retry_delay: float = 10.0,
    no_retry_deadline: float = 0.0,
) -> dict | None:
    """
    將單一 TXT 檔案透過 LLM API 轉換為 dict。
    - 遇到 503 / rate-limit 錯誤時使用指數退避自動重試
    - 連續失敗 max_retries 次才放棄
    """
    path = Path(txt_path)
    if not path.exists():
        print(f"  ❌ 找不到檔案：{txt_path}")
        return None

    with open(path, encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        print(f"  ⚠️  檔案為空：{txt_path}")
        return None

    filename = path.stem
    hint     = extract_program_hint(filename)

    # ── 組合 user message ────────────────────────────────────────────────────
    user_message = (
        f"The source filename is「{filename}」.\n"
        f"The primary program name extracted from the filename is「{hint}」"
        f" — use this as program_name unless the document body clearly states a different official name.\n"
        f"Do NOT include English translations, administrative notes, or extra descriptions in program_name.\n\n"
        f"Convert the following program rules text to JSON:\n\n{content}"
    )

    consecutive_errors = 0
    current_delay      = retry_delay   # 指數退避起始值
    result_text        = None

    while consecutive_errors < max_retries:
        print(f"  🤖 [{filename}] 使用模型：{model}")

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_message},
                ],
                temperature=0.1,
                max_tokens=16000,
                timeout=1800.0,
            )
            result_text = response.choices[0].message.content

            # 移除 markdown code block 包裝
            if result_text.strip().startswith("```"):
                lines = result_text.strip().split("\n")
                lines = [l for l in lines if not l.startswith("```")]
                result_text = "\n".join(lines)

            data = json.loads(result_text)

            # ── 容錯與自動修復 ──────────────────────────────────────────────
            if isinstance(data, list) and len(data) > 0:
                data = data[0]

            if not isinstance(data, dict):
                raise ValueError("解析出來的 JSON 不是一個字典物件")

            if "program_name" not in data or not data["program_name"]:
                data["program_name"] = hint

            # 如果 LLM 把 program_id 寫成 id，則進行轉換
            if "id" in data and "program_id" not in data:
                data["program_id"] = data["id"]

            if "program_id" not in data or not data["program_id"]:
                year_sem_match = re.match(r"^(\d{3}-\d)-", filename)
                prefix = year_sem_match.group(1) if year_sem_match else ""
                if prefix:
                    data["program_id"] = f"{prefix}-{data['program_name']}"
                else:
                    data["program_id"] = data["program_name"]

            if "versions" in data and isinstance(data["versions"], list):
                year_sem_match = re.match(r"^(\d{3})-(\d)-", filename)
                for v in data["versions"]:
                    if isinstance(v, dict):
                        if year_sem_match:
                            if "academic_year" not in v or v["academic_year"] is None:
                                v["academic_year"] = int(year_sem_match.group(1))
                            if "semester" not in v or v["semester"] is None:
                                v["semester"] = int(year_sem_match.group(2))

            # 基本欄位驗證
            assert "program_id"   in data,             "缺少 program_id"
            assert "program_name" in data,             "缺少 program_name"
            assert "versions"     in data,             "缺少 versions"
            assert len(data["versions"]) > 0,          "versions 為空"
            for v in data["versions"]:
                assert "academic_year" in v,           "version 缺少 academic_year"
                assert "course_groups" in v,           "version 缺少 course_groups"
                for g in v["course_groups"]:
                    assert "id"       in g,            "group 缺少 id"
                    assert "subjects" in g,            "group 缺少 subjects"
                    for s in g["subjects"]:
                        assert "program_subject" in s, "subject 缺少 program_subject"
                        assert "alternatives"    in s, "subject 缺少 alternatives"

            # ✅ 成功，重置退避計數
            consecutive_errors = 0
            return data

        except Exception as e:
            err_str = str(e).lower()

            # ── 判斷是否為可重試的錯誤類型 ──────────────────────────────────
            is_retriable = any(k in err_str for k in [
                "rate limit", "too many requests", "429",
                "503", "service unavailable", "overloaded",
                "connection", "timeout", "timed out",
            ])

            if is_retriable:
                if no_retry_deadline > 0 and time.time() > no_retry_deadline:
                    print(f"  ⏳ [{filename}] 執行時間已超過 310 分鐘，不接受重試，直接放棄")
                    return None

                consecutive_errors += 1
                remaining = max_retries - consecutive_errors

                if consecutive_errors >= max_retries:
                    print(f"  ❌ [{filename}] 連續失敗 {max_retries} 次，放棄")
                    return None

                print(
                    f"  ⚠️  [{filename}] API 忙碌（{type(e).__name__}: {e}），"
                    f"{current_delay:.0f}s 後重試（剩餘 {remaining} 次）"
                )
                time.sleep(current_delay)
                # 指數退避：每次重試等待時間翻倍，上限 120 秒
                current_delay = min(current_delay * 1.2, 120.0)

            else:
                print(f"  ❌ [{filename}] 轉換失敗：{type(e).__name__}: {e}")
                if result_text is not None:
                    debug_path = RULES_FILE.parent / f"_debug_{filename}.txt"
                    try:
                        with open(debug_path, "w", encoding="utf-8") as dbg:
                            dbg.write(result_text)
                        print(f"  📝 原始回應已儲存至：{debug_path}")
                    except Exception:
                        pass
                return None

    return None

# ── 工作單元：轉換 + 合併 + 存檔 ────────────────────────────────────────────
def process_file(
    file_path: Path,
    index: int,
    total: int,
    client: "OpenAI",
    model: str,
    programs: list[dict],
    retry_delay: float,
    counters: dict,
    deadline: float,
    no_retry_deadline: float,
) -> None:
    """
    單一檔案的處理函式：
      1. 呼叫 convert_file 取得結構化資料
      2. 合併至 programs 並立即存檔
      3. 更新計數器
    """
    print(f"\n[{index}/{total}] 🗂  {file_path.name}")

    # ── 檢查是否超過執行時間上限 ────────────────────────────────────────────
    if deadline > 0 and time.time() > deadline:
        counters["skipped"] += 1
        print(f"  ⏳ [{index}/{total}] 已超過設定的執行時間上限，跳過處理")
        return

    # ── 在呼叫 API 前，利用檔名先判斷是否已存在於 rules.json 中 ──────────
    filename = file_path.stem
    match = re.match(r"^(\d{3})-(\d)-", filename)
    if match:
        year = int(match.group(1))
        sem  = int(match.group(2))
        hint = extract_program_hint(filename)

        # 檢查是否已有相同學年學期的版本
        is_duplicate = False
        for p in programs:
            p_name = p.get("program_name", "")
            if hint == p_name:
                for v in p.get("versions", []):
                    if v.get("academic_year") == year and v.get("semester") == sem:
                        is_duplicate = True
                        break
            if is_duplicate:
                break

        if is_duplicate:
            counters["skipped"] += 1
            print(f"  ⏭️  [{index}/{total}] 已存在 {year}-{sem} {hint}，跳過 API 呼叫")
            return

    data = convert_file(
        str(file_path), client, model,
        max_retries=5, retry_delay=retry_delay,
        no_retry_deadline=no_retry_deadline,
    )

    # 合併資料 + 存檔 + 更新計數器
    if data is None:
        counters["failed"] += 1
        return

    added, skipped = merge_into_rules(programs, data)
    counters["added"]   += added
    counters["skipped"] += skipped
    save_rules(programs)   # 每次成功後立即存檔

    parts = []
    if added:   parts.append(f"新增 {added} 個版本")
    if skipped: parts.append(f"跳過 {skipped} 個重複")
    print(f"  ✅ [{index}/{total}] {data['program_name']} — {'、'.join(parts) or '無異動'}")

# ── 主程式 ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="將學程 TXT 檔案轉換為結構化 JSON 並匯入 rules.json"
    )
    parser.add_argument(
        "--base-url", default=None,
        help="API base URL（留空則在啟動時互動輸入）"
    )
    parser.add_argument(
        "--model", default=None,
        help="模型名稱（留空則在啟動時互動輸入）"
    )
    parser.add_argument(
        "--api-key", default=None,
        help="API 金鑰（預設從 .env 的 OLLAMA_KEY 讀取）"
    )
    parser.add_argument("--files",       nargs="*", help="指定要轉換的 TXT 檔案")
    parser.add_argument("--all",         action="store_true", help="轉換 data/ 下所有 TXT")
    parser.add_argument(
        "--retry-delay", type=float, default=10.0,
        help="503/rate-limit 首次重試等待秒數（之後指數退避，預設：10）"
    )
    parser.add_argument(
        "--timeout-mins", type=float, default=310.0,
        help="程式執行時間上限（分鐘），預設 310 分鐘 (5 小時)。設為 0 代表不限制。"
    )

    args = parser.parse_args()

    # ── 互動式輸入 base URL 與 model（若未透過 CLI 指定）────────────────────
    DEFAULT_BASE_URL = "https://ollama.com/v1"
    DEFAULT_MODEL    = "kimi-k2.6:cloud"
    # DEFAULT_MODEL    = "gemini-3-flash-preview"
    base_url, model = prompt_connection_settings(
        default_base_url = args.base_url  or DEFAULT_BASE_URL,
        default_model    = args.model     or DEFAULT_MODEL,
    )

    # ── 取得 API 金鑰（優先順序：--api-key > .env OLLAMA_KEY）──────────────
    api_key = args.api_key or os.environ.get("OLLAMA_KEY", "ollama")

    if not OpenAI:
        print("❌ 未安裝 openai 套件。請執行：pip install openai")
        sys.exit(1)

    client = OpenAI(base_url=base_url, api_key=api_key)

    print(f"🤖 模型：{model}")
    print(f"🌐 Base URL：{base_url}")

    # ── 決定候選檔案清單 ──────────────────────────────────────────────────────
    if args.all:
        candidate_files  = sorted(DATA_DIR.glob("*.txt"))
        files_to_process = candidate_files
        print(f"📚 共 {len(candidate_files)} 個 TXT 檔案，全部轉換")

    elif args.files:
        candidate_files  = [Path(f) for f in args.files]
        files_to_process = candidate_files

    else:
        # 互動模式
        candidate_files = sorted(DATA_DIR.glob("*.txt"))
        if not candidate_files:
            print(f"❌ {DATA_DIR} 目錄下沒有 TXT 檔案。")
            sys.exit(1)

        print(f"\n📂 data/ 目錄下共有 {len(candidate_files)} 個 TXT 檔案")
        print()

        while True:
            try:
                ans = input(
                    f"請輸入要轉換的數量（1 ~ {len(candidate_files)}，輸入 0 = 全部）：> "
                ).strip()
                count = int(ans)
                if count < 0 or count > len(candidate_files):
                    print(f"  ⚠️  請輸入 0 ~ {len(candidate_files)} 之間的數字")
                    continue
                break
            except ValueError:
                print("  ⚠️  請輸入有效的整數")

        files_to_process = candidate_files if count == 0 else candidate_files[:count]

        print(f"\n📋 即將處理 {len(files_to_process)} 個檔案：")
        for f in files_to_process:
            hint = extract_program_hint(f.stem)
            print(f"   • {hint}  ({f.name})")

    if not files_to_process:
        print("⚠️  沒有選擇任何檔案，結束。")
        return

    print(f"\n🚀 開始循序轉換 → {RULES_FILE}\n")

    # ── 載入現有 rules.json ───────────────────────────────────────────────────
    programs = load_rules()
    print(f"📖 已載入現有 rules.json（共 {len(programs)} 個學程）\n")

    # ── 計數器 ────────────────────────────────────────────────────────────────
    counters = {"added": 0, "skipped": 0, "failed": 0}

    total            = len(files_to_process)
    global_start_time = time.time()
    deadline          = global_start_time + args.timeout_mins * 60.0 if args.timeout_mins > 0 else 0.0
    no_retry_deadline = global_start_time + 310.0 * 60.0

    # ── 循序處理每個檔案 ──────────────────────────────────────────────────────
    for idx, file_path in enumerate(files_to_process):
        process_file(
            file_path,
            idx + 1,          # 1-based index
            total,
            client,
            model,
            programs,
            args.retry_delay,
            counters,
            deadline,
            no_retry_deadline,
        )

    # ── 最終摘要 ──────────────────────────────────────────────────────────────
    succeeded = total - counters["failed"]

    print(f"\n{'='*60}")
    print(f"轉換完成")
    print(f"  ✅ 成功：{succeeded} 個檔案")
    print(f"  ❌ 失敗：{counters['failed']} 個檔案")
    print(f"  ➕ 新增版本：{counters['added']}")
    print(f"  ⏭️  跳過重複：{counters['skipped']}")
    print(f"  📄 輸出：{RULES_FILE}")
    print(f"  📦 學程總數：{len(programs)}")
    print(f"  🤖 使用模型：{model}")
    print(f"  🌐 Base URL：{base_url}")


if __name__ == "__main__":
    main()