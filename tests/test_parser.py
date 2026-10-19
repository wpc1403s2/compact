"""文档解析器单元测试。"""

from src.parsers.document_parser import parse_raw_text_to_sections


class TestParseRawText:
    def test_chinese_section_format(self):
        text = """第一条 标的物
甲方向乙方采购软件一套。

第二条 价款
合同总价为100万元。

第三条 违约责任
违约方应赔偿损失。"""
        sections = parse_raw_text_to_sections(text)
        assert len(sections) >= 3
        assert "标的物" in sections[0]["title"]

    def test_numbered_format(self):
        text = """1. 总则
本合同约定双方权利义务。

2. 价款
合同金额为50万元。"""
        sections = parse_raw_text_to_sections(text)
        assert len(sections) >= 2

    def test_empty_text(self):
        sections = parse_raw_text_to_sections("")
        assert len(sections) == 0

    def test_no_sections(self):
        text = "这是一段没有标题的纯文本内容。"
        sections = parse_raw_text_to_sections(text)
        assert len(sections) == 1
        assert sections[0]["title"] == "全文"
