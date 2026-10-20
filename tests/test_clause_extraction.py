import pytest

from src.agents import clause_extraction
from src.agents.clause_extraction import ClauseExtractionAgent
from src.pipeline.state import ContractReviewState


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_parsed_document_text(monkeypatch):
    raw_text = "第一条 服务内容\n乙方应向甲方提供技术支持服务。"
    agent = ClauseExtractionAgent()
    monkeypatch.setattr(agent.parser, "parse", lambda _: raw_text)
    monkeypatch.setattr(agent, "_ensure_llm", lambda: None)

    async def fail_llm(*args, **kwargs):
        raise RuntimeError("调用超时（60秒）")

    monkeypatch.setattr(clause_extraction, "resilient_call", fail_llm)

    result = await agent.run(ContractReviewState(document_path="contract.txt"))

    print(result["raw_text"])
    assert result["raw_text"] == raw_text
    assert len(result["clauses"]) == 1
    assert result["clauses"][0].content == "乙方应向甲方提供技术支持服务。"
    assert result["errors"] == ["条款提取失败: 调用超时（60秒）"]
