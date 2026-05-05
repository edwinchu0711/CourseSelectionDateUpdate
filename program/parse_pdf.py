import os
import sys
import asyncio
from pathlib import Path

# ✅ 新版 SDK：使用 AsyncLlamaCloud（需要 llama-cloud >= 1.0.0）
from llama_cloud import AsyncLlamaCloud


async def parse_pdf_to_markdown(pdf_path: str, output_path: str = None) -> str:
    """
    使用 LlamaCloud SDK v2.x 解析 PDF 並輸出 Markdown 格式

    新版 API 流程：
      Step 1: client.files.create()   → 上傳檔案，取得 file_id
      Step 2: client.parsing.parse()  → 用 file_id 解析，等待完成
      Step 3: 從 result 提取 markdown 內容

    Args:
        pdf_path:    輸入的 PDF 檔案路徑
        output_path: 輸出的 .md 檔案路徑（可選）

    Returns:
        解析後的 Markdown 字串
    """

    # ── 1. 取得 API Key ──────────────────────────────────────────────
    api_key = os.environ.get("LLAMA_CLOUD_API_KEY")
    if not api_key:
        raise ValueError(
            "找不到 LLAMA_CLOUD_API_KEY 環境變數，"
            "請至 GitHub Repo → Settings → Secrets 設定"
        )

    # ── 2. 確認 PDF 檔案存在 ─────────────────────────────────────────
    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        raise FileNotFoundError(f"找不到 PDF 檔案：{pdf_path}")

    print(f"📄 正在處理：{pdf_file.name}")

    # ── 3. 初始化 AsyncLlamaCloud 客戶端 ────────────────────────────
    client = AsyncLlamaCloud(api_key=api_key)

    # ── 4. 上傳 PDF 檔案，取得 file_id ──────────────────────────────
    print("⬆️  上傳 PDF 至 LlamaCloud...")
    with open(pdf_path, "rb") as f:
        file_obj = await client.files.create(
            file=(pdf_file.name, f, "application/pdf"),  # (檔名, 內容, MIME type)
            purpose="parse",                              # 用途：解析
        )

    file_id = file_obj.id
    print(f"✅ 上傳完成，file_id：{file_id}")

    # ── 5. 呼叫 parsing.parse()，等待解析完成 ───────────────────────
    print("🤖 解析中（agentic_plus tier，繁體中文 OCR）...")
    result = await client.parsing.parse(
        file_id=file_id,

        # 解析品質層級
        tier="cost_effective",
        version="latest",

        # 要求回傳的輸出格式
        expand=["markdown", "text", "metadata"],

        # ── OCR 語言設定 ─────────────────────────────────────────
        processing_options={
            "ocr_parameters": {
                "languages": ["en", "ch_tra"]   # 繁體中文 + 英文
            }
        },

        # ── 輸出格式設定 ─────────────────────────────────────────
        output_options={
            "markdown": {
                "tables": {
                    "output_tables_as_markdown": False,   # 表格輸出為 Markdown
                    "merge_continued_tables":    True,   # 合併跨頁表格
                    "compact_markdown_tables":    True   # 合併跨頁表格
                }
            },
            "spatial_text": {
                "preserve_very_small_text": True         # 保留小字（頁尾等）
            }
        }
    )

    # ── 6. 提取 Markdown 內容 ────────────────────────────────────────
    markdown_pages = []

    if hasattr(result, "markdown") and result.markdown:
        # 逐頁提取 Markdown 內容
        for i, page in enumerate(result.markdown.pages, start=1):
            # 新版 SDK：頁面內容在 page.markdown 或 page.md
            page_content = (
                getattr(page, "markdown", None)
                or getattr(page, "md", None)
                or str(page)
            )
            markdown_pages.append(f"<!-- Page {i} -->\n{page_content}")
        markdown_output = "\n\n---\n\n".join(markdown_pages)
    else:
        # fallback：使用純文字
        print("⚠️  未取得 markdown 格式，改用純文字輸出")
        markdown_output = str(result)

    print(f"✅ 解析完成，共 {len(markdown_pages)} 頁")

    # ── 7. 顯示 Metadata ─────────────────────────────────────────────
    if hasattr(result, "metadata") and result.metadata:
        pages = getattr(result.metadata, "pages", [])
        if pages:
            print("\n📊 解析 Metadata：")
            for i, meta in enumerate(pages, start=1):
                confidence = getattr(meta, "confidence", "N/A")
                print(f"   第 {i} 頁 - 信心分數：{confidence}")

    # ── 8. 儲存輸出檔案 ──────────────────────────────────────────────
    if output_path:
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(markdown_output, encoding="utf-8")
        print(f"💾 已儲存至：{output_path}")

    return markdown_output


def main():
    """主程式入口：從環境變數或命令列讀取 PDF 路徑"""

    # 優先從環境變數讀取（GitHub Actions 由 fetch_pdf.py 寫入）
    pdf_path = os.environ.get("PDF_PATH")

    # 若無環境變數，從命令列讀取
    if not pdf_path and len(sys.argv) >= 2:
        pdf_path = sys.argv[1]

    if not pdf_path:
        print("❌ 未指定 PDF 路徑！")
        print("   方式 1：設定環境變數 PDF_PATH")
        print("   方式 2：python parse_pdf.py <PDF路徑> [輸出路徑]")
        sys.exit(1)

    # ✅ 固定輸出為 data/rules.md，不跟著 PDF 檔名走
    base_dir = Path(__file__).parent
    if base_dir.name == "program":
        output_path = str(base_dir / "data" / "rules.md")
    else:
        output_path = str(base_dir / "program" / "data" / "rules.md")

    # 若有手動指定輸出路徑（命令列第二個參數），則優先使用
    if len(sys.argv) >= 3:
        output_path = sys.argv[2]

    print(f"📂 PDF 路徑：{pdf_path}")
    print(f"📂 輸出路徑：{output_path}（固定為 data/rules.md）")

    try:
        markdown_result = asyncio.run(
            parse_pdf_to_markdown(
                pdf_path=pdf_path,
                output_path=output_path
            )
        )

        # ✅ 把輸出路徑也寫入 GITHUB_ENV，供後續步驟使用
        github_env = os.environ.get("GITHUB_ENV")
        if github_env:
            with open(github_env, "a", encoding="utf-8") as f:
                f.write(f"RULES_PATH={output_path}\n")

        # 預覽前 500 字
        print("\n📝 內容預覽（前 500 字）：")
        print("=" * 60)
        print(markdown_result[:500])
        print("=" * 60)

    except FileNotFoundError as e:
        print(f"❌ 錯誤：{e}")
        sys.exit(1)
    except ValueError as e:
        print(f"❌ 設定錯誤：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 解析失敗：{e}")
        raise



if __name__ == "__main__":
    main()
