import asyncio

import pytest

from src.pipeline import graph as graph_module
from src.pipeline.state import (
    ComplianceStatus,
    ContractReviewState,
    ReviewStatus,
    RiskLevel,
)


@pytest.mark.asyncio
async def test_risk_and_compliance_agents_run_concurrently(monkeypatch):
    barrier = asyncio.Barrier(2)

    class FakeClauseAgent:
        async def run(self, state):
            return {"clauses": [], "contract_type": "测试合同"}

    class FakeRiskAgent:
        def __init__(self, rag_retriever=None):
            pass

        async def run(self, state):
            await barrier.wait()
            return {
                "risk_findings": [],
                "overall_risk_level": RiskLevel.NONE,
                "risk_summary": "未发现风险",
            }

    class FakeComplianceAgent:
        def __init__(self, rag_retriever=None):
            pass

        async def run(self, state):
            await barrier.wait()
            return {
                "compliance_findings": [],
                "overall_compliance": ComplianceStatus.COMPLIANT,
                "missing_clauses": [],
            }

    class FakeSuggestionAgent:
        def __init__(self, rag_retriever=None):
            pass

        async def run(self, state):
            assert state.risk_summary == "未发现风险"
            assert state.overall_compliance == ComplianceStatus.COMPLIANT
            return {
                "suggestions": [],
                "version_diff": "无需修改",
                "status": ReviewStatus.COMPLETED,
            }

    monkeypatch.setattr(graph_module, "ClauseExtractionAgent", FakeClauseAgent)
    monkeypatch.setattr(graph_module, "RiskIdentificationAgent", FakeRiskAgent)
    monkeypatch.setattr(graph_module, "ComplianceCheckAgent", FakeComplianceAgent)
    monkeypatch.setattr(graph_module, "SuggestionAgent", FakeSuggestionAgent)

    pipeline = graph_module.create_review_pipeline(
        with_human_review=False,
        with_checkpointing=False,
    )
    result = await asyncio.wait_for(
        pipeline.ainvoke(
            ContractReviewState(raw_text="合同文本").model_dump(),
            config={"configurable": {"thread_id": "parallel-test"}},
        ),
        timeout=3,
    )

    assert result["risk_summary"] == "未发现风险"
    assert result["overall_compliance"] == ComplianceStatus.COMPLIANT
    assert result["status"] == ReviewStatus.COMPLETED
    step_statuses = {event.step: event.status for event in result["step_log"]}
    assert step_statuses["risk_identification"] == "done"
    assert step_statuses["compliance_check"] == "done"
    assert result["progress"] == 1.0
