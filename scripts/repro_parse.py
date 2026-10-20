"""本地复现文件解析逻辑（不启动 Web 服务）。

等价于生产链路 src/agents/clause_extraction.py 中：
    raw_text = DocumentParser().parse(document_path)
    sections = parse_raw_text_to_sections(raw_text)

用法（从项目根目录执行）:
    python scripts/repro_parse.py <文件路径>

说明:
    - 解析 .txt 无需第三方依赖。
    - 解析 .pdf / .docx 需要安装依赖: pip install python-docx PyPDF2
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许从项目根目录直接运行（scripts/ 的上一级即项目根）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.parsers.document_parser import DocumentParser, parse_raw_text_to_sections


def main() -> None:


    # file_path = Path(sys.argv[1])
    file_path = Path(r"D:\work\code\python\sample-contracts\sample_labor_contract.txt")
    if not file_path.exists():
        print(f"[错误] 文件不存在: {file_path}")
        raise SystemExit(1)

    print(f"解析文件: {file_path}  (后缀: {file_path.suffix})")

    parser = DocumentParser()
    try:
        raw_text = parser.parse(file_path)
    except Exception as e:  # noqa: BLE001 - 本地调试需暴露真实异常
        print(f"\n[解析失败] {type(e).__name__}: {e}")
        if file_path.suffix.lower() in (".pdf", ".docx", ".doc"):
            print("提示: 解析 PDF/DOCX 需要先安装依赖 -> pip install python-docx PyPDF2")
        raise SystemExit(1)

    print("\n" + "=" * 60)
    print(f"原始文本长度: {len(raw_text)} 字符")
    print("=" * 60)
    print(raw_text[:2000])

    print("\n" + "=" * 60)
    print("章节切分结果 (parse_raw_text_to_sections)")
    print("=" * 60)
    sections = parse_raw_text_to_sections(raw_text)
    for idx, sec in enumerate(sections, 1):
        content = sec["content"]
        preview = content[:80].replace("\n", " ")
        print(f"[{idx}] {sec['title']}  (内容 {len(content)} 字)")
        print(f"     {preview}...")


if __name__ == "__main__":
    main()
