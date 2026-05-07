import json
import re
import argparse
import os
import sys
import time
import threading
import random
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

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

# ── 全域鎖（保護 programs 列表與檔案寫入）────────────────────────────────────
_programs_lock = threading.Lock()

# ── 模型備援狀態（含鎖）──────────────────────────────────────────────────────
# 記錄目前使用的 model，以及是否已切換到備援
_model_lock          = threading.Lock()
_current_model: str  = ""          # 由 main() 初始化
_model_fallback_done = False        # 是否已切換至備援 model
_PRIMARY_MODEL       = ""           # 由 main() 初始化
_FALLBACK_MODEL      = ""           # 由 main() 初始化

def get_current_model() -> str:
    """執行緒安全地取得目前使用的 model 名稱"""
    with _model_lock:
        return _current_model

def trigger_model_fallback(reason: str) -> bool:
    """
    嘗試將 model 切換至備援。
    若已切換過則不重複切換，回傳 True 表示本次成功觸發切換。
    """
    global _current_model, _model_fallback_done
    with _model_lock:
        if _model_fallback_done:
            return False   # 已切換過，不重複動作
        _model_fallback_done = True
        _current_model = _FALLBACK_MODEL
        tprint(
            f"\n  🔀 模型備援觸發！原因：{reason}\n"
            f"     切換：{_PRIMARY_MODEL}  →  {_FALLBACK_MODEL}\n"
        )
        return True

# ── 速率限制器（確保不超過 API 呼叫頻率限制）────────────────────────────────
class RateLimiter:
    def __init__(self, max_requests: int, period: float):
        self.max_requests = max_requests
        self.period = period
        self.timestamps = []
        self.cond = threading.Condition()

    def wait(self):
        with self.cond:
            while True:
                now = time.time()
                # 移除已經超過時間窗口的紀錄
                self.timestamps = [t for t in self.timestamps if now - t < self.period]

                if len(self.timestamps) < self.max_requests:
                    self.timestamps.append(time.time())
                    break
                else:
                    # 等待直到最早的一個請求移出時間窗口
                    sleep_time = self.period - (now - self.timestamps[0])
                    if sleep_time > 0:
                        self.cond.wait(timeout=sleep_time)

# 限制每分鐘 40 次有效請求
_api_rate_limiter = RateLimiter(max_requests=40, period=60.0)

# ── 印出鎖（避免多執行緒輸出交錯）───────────────────────────────────────────
_print_lock = threading.Lock()

def tprint(*args, **kwargs):
    """執行緒安全的 print"""
    with _print_lock:
        print(*args, **kwargs)

# ── System Prompt ─────────────────────────────────────────────────────────────
PROMPT_FILE = Path(__file__).parent / "prompt.txt"
if PROMPT_FILE.exists():
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read()
else:
    SYSTEM_PROMPT = ""


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
    """將程式列表存回 rules.json（呼叫前須持有 _programs_lock）"""
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(programs, f, ensure_ascii=False, indent=2)

# ── 合併新資料至現有 programs 列表 ────────────────────────────────────────────
def merge_into_rules(programs: list[dict], new_data: dict) -> tuple[int, int]:
    """
    將 new_data 合併進 programs 列表。
    回傳 (added_versions, skipped_versions)
    ⚠️  呼叫前須持有 _programs_lock
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

# ── 單一檔案轉換（含 503 / rate-limit 重試邏輯 + 模型備援）──────────────────
def convert_file(
    txt_path: str,
    client: "OpenAI",
    max_retries: int = 5,
    retry_delay: float = 10.0,
    no_retry_deadline: float = 0.0,
) -> dict | None:
    """
    將單一 TXT 檔案透過 LLM API 轉換為 dict。
    - 遇到 503 / rate-limit 錯誤時使用指數退避自動重試
    - 若偵測到 rate-limit 且尚未切換備援 model，立即觸發切換
    - 連續失敗 max_retries 次才放棄
    """
    path = Path(txt_path)
    if not path.exists():
        tprint(f"  ❌ 找不到檔案：{txt_path}")
        return None

    with open(path, encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        tprint(f"  ⚠️  檔案為空：{txt_path}")
        return None

    filename = path.stem
    hint     = extract_program_hint(filename)
    tprint(f"  🔄 轉換中：{filename}")
    tprint(f"  💡 學程名稱提示：{hint}")

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
        # ── 每次呼叫前動態取得當前 model（可能已被其他執行緒切換）──────────
        model = get_current_model()
        tprint(f"  🤖 [{filename}] 使用模型：{model}")

        try:
            _api_rate_limiter.wait()  # 等待直到符合速率限制
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

            # ── 判斷是否為 rate-limit 類型錯誤 ──────────────────────────────
            is_rate_limit = any(k in err_str for k in [
                "rate limit", "too many requests", "429",
            ])
            is_retriable = is_rate_limit or any(k in err_str for k in [
                "503", "service unavailable", "overloaded",
                "connection", "timeout", "timed out",
            ])

            if is_retriable:
                # ── 若是 rate-limit，嘗試觸發模型備援切換 ──────────────────
                if is_rate_limit:
                    switched = trigger_model_fallback(
                        f"{type(e).__name__}: {str(e)[:80]}"
                    )
                    if switched:
                        # 切換後立即重試，不計入 consecutive_errors
                        current_delay = retry_delay  # 重置退避計時
                        continue

                if no_retry_deadline > 0 and time.time() > no_retry_deadline:
                    tprint(f"  ⏳ [{filename}] 執行時間已超過 310 分鐘，不接受重試，直接放棄")
                    return None

                consecutive_errors += 1
                remaining = max_retries - consecutive_errors

                if consecutive_errors >= max_retries:
                    tprint(f"  ❌ [{filename}] 連續失敗 {max_retries} 次，放棄")
                    return None

                tprint(
                    f"  ⚠️  [{filename}] API 忙碌（{type(e).__name__}: {e}），"
                    f"{current_delay:.0f}s 後重試（剩餘 {remaining} 次）"
                )
                time.sleep(current_delay)
                # 指數退避：每次重試等待時間翻倍，上限 120 秒
                current_delay = min(current_delay * 1.2, 120.0)

            else:
                tprint(f"  ❌ [{filename}] 轉換失敗：{type(e).__name__}: {e}")
                if result_text is not None:
                    debug_path = RULES_FILE.parent / f"_debug_{filename}.txt"
                    try:
                        with open(debug_path, "w", encoding="utf-8") as dbg:
                            dbg.write(result_text)
                        tprint(f"  📝 原始回應已儲存至：{debug_path}")
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
    programs: list[dict],
    retry_delay: float,
    counters: dict,
    deadline: float,
    no_retry_deadline: float,
    stagger_delay: float = 0.0,   # 首次啟動錯開延遲（秒）
) -> None:
    """
    單一執行緒的工作函式：
      1. 首次啟動時等待 stagger_delay 秒（錯開各執行緒的首次 API 呼叫）
      2. 呼叫 convert_file 取得結構化資料
      3. 加鎖後合併至 programs 並立即存檔
      4. 更新全域計數器
    """
    # ── 首次啟動錯開延遲（僅在任務開始時等待一次）──────────────────────────
    if stagger_delay > 0.0:
        tprint(f"  ⏱️  [{index}/{total}] {file_path.name} 等待 {stagger_delay:.1f}s 後啟動...")
        time.sleep(stagger_delay)

    tprint(f"\n[{index}/{total}] 🗂  {file_path.name}")

    if deadline > 0 and time.time() > deadline:
        with _programs_lock:
            counters["skipped"] += 1
        tprint(f"  ⏳ [{index}/{total}] 已超過設定的執行時間上限，跳過處理")
        return

    # ── 在呼叫 API 前，利用檔名先判斷是否已存在於 rules.json 中 ──────────
    filename = file_path.stem
    match = re.match(r"^(\d{3})-(\d)-", filename)
    if match:
        year = int(match.group(1))
        sem  = int(match.group(2))
        hint = extract_program_hint(filename)

        is_duplicate = False
        with _programs_lock:
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
            with _programs_lock:
                counters["skipped"] += 1
            tprint(f"  ⏭️  [{index}/{total}] 已存在 {year}-{sem} {hint}，跳過 API 呼叫")
            return

    data = convert_file(
        str(file_path), client,
        max_retries=5, retry_delay=retry_delay,
        no_retry_deadline=no_retry_deadline,
    )

    # 加鎖：合併資料 + 存檔 + 更新計數器
    with _programs_lock:
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
    tprint(f"  ✅ [{index}/{total}] {data['program_name']} — {'、'.join(parts) or '無異動'}")

# ── 主程式 ────────────────────────────────────────────────────────────────────
def main():
    global _current_model, _PRIMARY_MODEL, _FALLBACK_MODEL

    parser = argparse.ArgumentParser(
        description="將學程 TXT 檔案轉換為結構化 JSON 並匯入 rules.json（多執行緒版）"
    )
    parser.add_argument(
        "--base-url", default="https://integrate.api.nvidia.com/v1",
        help="API base URL（預設：https://integrate.api.nvidia.com/v1）"
    )
    parser.add_argument(
        "--model",
        default="moonshotai/kimi-k2.6",
        help="主要模型名稱（預設：moonshotai/kimi-k2.6）"
    )
    parser.add_argument(
        "--fallback-model",
        default="minimaxai/minimax-m2.7",
        help="備援模型名稱，當主要模型持續 rate-limit 時切換（預設：minimaxai/minimax-m2.7）"
    )
    parser.add_argument(
        "--api-key", default=None,
        help="API 金鑰（預設從 .env 的 NVIDIA_API_KEY 讀取）"
    )
    parser.add_argument("--files",       nargs="*", help="指定要轉換的 TXT 檔案")
    parser.add_argument("--all",         action="store_true", help="轉換 data/ 下所有 TXT")
    parser.add_argument(
        "--workers", type=int, default=4,
        help="最大並行執行緒數（預設：4）"
    )
    parser.add_argument(
        "--retry-delay", type=float, default=10.0,
        help="503/rate-limit 首次重試等待秒數（之後指數退避，預設：10）"
    )
    parser.add_argument(
        "--timeout-mins", type=float, default=310.0,
        help="程式執行時間上限（分鐘），預設 310 分鐘 (5 小時)。設為 0 代表不限制。"
    )
    parser.add_argument(
        "--stagger-min", type=float, default=5.0,
        help="首次啟動錯開延遲最小秒數（預設：5）"
    )
    parser.add_argument(
        "--stagger-max", type=float, default=30.0,
        help="首次啟動錯開延遲最大秒數（預設：30）"
    )

    args = parser.parse_args()

    # ── 初始化模型備援狀態 ────────────────────────────────────────────────────
    _PRIMARY_MODEL  = args.model
    _FALLBACK_MODEL = args.fallback_model
    _current_model  = _PRIMARY_MODEL   # 從主要模型開始

    # 取得 API 金鑰
    api_key = args.api_key or os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        print("❌ 未提供 API 金鑰。請在 .env 設定 NVIDIA_API_KEY=<key> 或使用 --api-key。")
        sys.exit(1)

    if not OpenAI:
        print("❌ 未安裝 openai 套件。請執行：pip install openai")
        sys.exit(1)

    client = OpenAI(base_url=args.base_url, api_key=api_key)
    print(f"🤖 主要模型：{_PRIMARY_MODEL}")
    print(f"🔀 備援模型：{_FALLBACK_MODEL}")
    print(f"🌐 Base URL：{args.base_url}")
    print(f"⚡ 並行執行緒：{args.workers}")
    print(f"⏱️  啟動錯開延遲：{args.stagger_min:.0f} ~ {args.stagger_max:.0f} 秒")

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

    print(f"\n🚀 開始多執行緒轉換 → {RULES_FILE}\n")

    # ── 載入現有 rules.json ───────────────────────────────────────────────────
    programs = load_rules()
    print(f"📖 已載入現有 rules.json（共 {len(programs)} 個學程）\n")

    # ── 共享計數器（由 _programs_lock 保護）──────────────────────────────────
    counters = {"added": 0, "skipped": 0, "failed": 0}

    total = len(files_to_process)

    global_start_time = time.time()
    deadline          = global_start_time + args.timeout_mins * 60.0 if args.timeout_mins > 0 else 0.0
    no_retry_deadline = global_start_time + 310.0 * 60.0

    # ── 預先為每個任務產生隨機錯開延遲 ──────────────────────────────────────
    # index 0 的任務不延遲（讓第一個任務立即開始），其餘隨機錯開
    stagger_delays = [0.0] + [
        random.uniform(args.stagger_min, args.stagger_max)
        for _ in range(total - 1)
    ]
    # 打亂順序，讓延遲分佈更均勻（避免全部集中在後段）
    random.shuffle(stagger_delays)
    # 第一個任務固定不延遲，確保至少有一個任務立即開始
    stagger_delays[0] = 0.0

    # ── 多執行緒執行 ──────────────────────────────────────────────────────────
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                process_file,
                file_path,
                idx + 1,          # 1-based index
                total,
                client,
                programs,
                args.retry_delay,
                counters,
                deadline,
                no_retry_deadline,
                stagger_delays[idx],   # 首次啟動錯開延遲
            ): file_path
            for idx, file_path in enumerate(files_to_process)
        }

        # 等待所有任務完成（as_completed 讓我們可以即時捕捉例外）
        for future in as_completed(futures):
            file_path = futures[future]
            try:
                future.result()   # 若 process_file 拋出未捕捉的例外，在此重新拋出
            except Exception as exc:
                tprint(f"  💥 未預期錯誤 [{file_path.name}]：{exc}")
                with _programs_lock:
                    counters["failed"] += 1

    # ── 最終摘要 ──────────────────────────────────────────────────────────────
    succeeded = total - counters["failed"]
    final_model = get_current_model()
    did_fallback = final_model != _PRIMARY_MODEL

    print(f"\n{'='*60}")
    print(f"轉換完成")
    print(f"  ✅ 成功：{succeeded} 個檔案")
    print(f"  ❌ 失敗：{counters['failed']} 個檔案")
    print(f"  ➕ 新增版本：{counters['added']}")
    print(f"  ⏭️  跳過重複：{counters['skipped']}")
    print(f"  📄 輸出：{RULES_FILE}")
    print(f"  📦 學程總數：{len(programs)}")
    if did_fallback:
        print(f"  🔀 模型備援：{_PRIMARY_MODEL} → {_FALLBACK_MODEL}（已切換）")
    else:
        print(f"  🤖 使用模型：{_PRIMARY_MODEL}（未觸發備援）")


if __name__ == "__main__":
    main()