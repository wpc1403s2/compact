"""
文档解析器 — 支持PDF和DOCX格式的合同文档解析。

将合同文档转换为纯文本，保留结构信息（标题层级、段落分隔）。
"""

from __future__ import annotations

import re
from pathlib import Path


class DocumentParser:
    """统一的文档解析入口，根据文件后缀自动选择解析策略。"""

    SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"}

    def parse(self, file_path: str | Path) -> str:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        ext = path.suffix.lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"不支持的文件格式: {ext}")

        if ext == ".pdf":
            return self._parse_pdf(path)
        elif ext in (".docx", ".doc"):
            return self._parse_docx(path)
        else:
            return path.read_text(encoding="utf-8")

    def _parse_pdf(self, path: Path) -> str:
        from PyPDF2 import PdfReader

        reader = PdfReader(str(path))
        pages: list[str] = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text.strip())
        return "\n\n".join(pages)

    def _parse_docx(self, path: Path) -> str:
        from docx import Document as DocxDocument

        doc = DocxDocument(str(path))
        paragraphs: list[str] = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            style = para.style.name if para.style else ""
            if "Heading" in style:
                paragraphs.append(f"\n{'#' * self._heading_level(style)} {text}")
            else:
                paragraphs.append(text)
        return "\n\n".join(paragraphs)

    @staticmethod
    def _heading_level(style_name: str) -> int:
        match = re.search(r"(\d+)", style_name)
        return int(match.group(1)) if match else 1


def parse_raw_text_to_sections(text: str) -> list[dict]:
    """
    将原始合同文本按章节切分。
    识别常见的中文合同章节格式：
    - 第一条、第二条...
    - 一、二、三...
    - 1. 2. 3. / 1、2、3
    """
    patterns = [
        r"(第[一二三四五六七八九十百]+条\s*.+)",
        r"(第\d+条\s*.+)",
        r"([一二三四五六七八九十]+、\s*.+)",
        r"(\d+[\.、]\s*.+)",
        r"(#+\s*.+)",
    ]

    combined = "|".join(patterns)
    sections: list[dict] = []
    last_pos = 0
    last_title = "前言"

    for match in re.finditer(combined, text):
        if last_pos > 0 or match.start() > 0:
            content = text[last_pos : match.start()].strip()
            if content:
                sections.append({"title": last_title, "content": content})

        last_title = match.group(0).strip()
        last_pos = match.end()

    remaining = text[last_pos:].strip()
    if remaining:
        sections.append({"title": last_title, "content": remaining})

    if not sections and text.strip():
        sections.append({"title": "全文", "content": text.strip()})

    return sections

if __name__ == '__main__':
    # 生成文档解析测试代码
    import tempfile

    # 1) 章节切分测试（基于纯文本，无需第三方依赖）
    sample_contract = """
    甲方：示例科技有限公司
    乙方：测试服务有限公司

    第一条 服务内容
    乙方应向甲方提供合同约定的技术支持服务，保证服务质量符合行业标准。

    第二条 合同金额
    本合同总金额为人民币壹拾万元整（¥100,000.00）。

    一、付款方式
    甲方应在合同签订后七个工作日内支付首付款。

    二、违约责任
    任何一方违约应承担相应法律责任并赔偿对方损失。
    """

    print("=" * 60)
    print("测试1: parse_raw_text_to_sections 章节切分")
    print("=" * 60)
    sections = parse_raw_text_to_sections(sample_contract)
    for idx, sec in enumerate(sections, 1):
        print(f"[{idx}] {sec['title']}")
        print(f"    {sec['content'][:60]}...")

    # 2) 真实文件解析测试（txt / docx / pdf）
    parser = DocumentParser()

    with tempfile.TemporaryDirectory() as tmp:
        # 2.1 TXT 解析
        txt_path = Path(tmp) / "contract.txt"
        txt_path.write_text(sample_contract, encoding="utf-8")
        print("\n" + "=" * 60)
        print("测试2: 解析 TXT 文件")
        print("=" * 60)
        txt_text = parser.parse(txt_path)
        print(f"解析长度: {len(txt_text)} 字符")
        print(txt_text[:120])

        # 2.2 DOCX 解析（需要 python-docx，未安装时跳过）
        try:
            from docx import Document as DocxDocument
            docx_path = Path(tmp) / "contract.docx"
            doc = DocxDocument()
            doc.add_heading("服务合同", level=1)
            doc.add_paragraph("第一条 服务内容")
            doc.add_paragraph("乙方提供技术支持服务。")
            doc.add_heading("付款条款", level=2)
            doc.add_paragraph("甲方支付合同金额。")
            doc.save(str(docx_path))

            print("\n" + "=" * 60)
            print("测试3: 解析 DOCX 文件")
            print("=" * 60)
            docx_text = parser.parse(docx_path)
            print(docx_text)
        except ImportError:
            print("\n[跳过] 未安装 python-docx，跳过 DOCX 解析测试")

        # 2.3 异常路径测试
        print("\n" + "=" * 60)
        print("测试4: 异常路径")
        print("=" * 60)
        try:
            parser.parse(Path(tmp) / "not_exist.pdf")
        except FileNotFoundError as e:
            print(f"捕获预期异常: {e}")

        try:
            parser.parse(Path(tmp) / "data.csv")
        except ValueError as e:
            print(f"捕获预期异常: {e}")

    print("\n所有测试完成。")

