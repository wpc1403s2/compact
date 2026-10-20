"""数据库层单元测试 — 使用 SQLite 内存数据库。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import session as db_session_module
from src.db.models import Base
from src.db.crud import save_review, get_review, update_feedback
from src.pipeline.state import (
    Clause,
    ComplianceFinding,
    ContractReviewState,
    HumanFeedback,
    RiskFinding,
    ReviewStatus,
    Suggestion,
)


# ── 测试 fixtures ──────────────────────────────────────────

@pytest_asyncio.fixture
async def db_engine():
    """创建 SQLite 内存数据库 engine。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """创建测试用数据库 session。"""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


def _make_state(review_id: str = "test-001") -> ContractReviewState:
    """构造测试用的 ContractReviewState。"""
    return ContractReviewState(
        review_id=review_id,
        status=ReviewStatus.COMPLETED,
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        document_path="/tmp/test.pdf",
        raw_text="合同原文...",
        contract_type="买卖合同",
        clauses=[
            Clause(id="c001", title="付款条款", content="买方应在30日内付款", category="payment"),
            Clause(id="c002", title="违约责任", content="乙方承担无限责任", category="liability"),
        ],
        risk_findings=[
            RiskFinding(
                clause_id="c002",
                risk_level="high",
                risk_type="无限责任",
                description="违约条款构成无限责任",
                buyer_impact="无保障",
                seller_impact="风险极高",
                rationale="违反民法典第585条",
            ),
        ],
        overall_risk_level="high",
        risk_summary="发现1个高风险条款",
        compliance_findings=[
            ComplianceFinding(
                clause_id="c002",
                status="non_compliant",
                regulation="《民法典》第585条",
                issue="违约金超出合理范围",
                recommendation="建议设置上限",
            ),
        ],
        missing_clauses=["争议解决条款"],
        overall_compliance="non_compliant",
        suggestions=[
            Suggestion(
                clause_id="c002",
                original_text="乙方承担无限责任",
                suggested_text="乙方赔偿以合同总额20%为上限",
                reason="原条款构成无限责任",
                priority="high",
            ),
        ],
        version_diff="--- 原文\n+++ 建议",
        needs_human_review=True,
        errors=[],
    )


# ── 测试用例 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_and_get_review(db_session: AsyncSession):
    """保存后读取，验证字段完整。"""
    state = _make_state("save-001")
    await save_review(db_session, state)

    loaded = await get_review(db_session, "save-001")
    assert loaded is not None
    assert loaded.review_id == "save-001"
    assert loaded.contract_type == "买卖合同"
    assert loaded.status == ReviewStatus.COMPLETED
    assert loaded.overall_risk_level == "high"
    assert loaded.risk_summary == "发现1个高风险条款"
    assert loaded.needs_human_review is True


@pytest.mark.asyncio
async def test_save_with_clauses(db_session: AsyncSession):
    """含条款子表数据的保存/读取。"""
    state = _make_state("clauses-001")
    await save_review(db_session, state)

    loaded = await get_review(db_session, "clauses-001")
    assert loaded is not None
    assert len(loaded.clauses) == 2
    assert loaded.clauses[0].title == "付款条款"
    assert loaded.clauses[0].category.value == "payment"
    assert loaded.clauses[1].title == "违约责任"


@pytest.mark.asyncio
async def test_save_with_risk_findings(db_session: AsyncSession):
    """风险发现的保存/读取。"""
    state = _make_state("risk-001")
    await save_review(db_session, state)

    loaded = await get_review(db_session, "risk-001")
    assert loaded is not None
    assert len(loaded.risk_findings) == 1
    assert loaded.risk_findings[0].risk_level == "high"
    assert loaded.risk_findings[0].risk_type == "无限责任"
    assert loaded.risk_findings[0].clause_id == "c002"


@pytest.mark.asyncio
async def test_save_with_compliance_findings(db_session: AsyncSession):
    """合规发现的保存/读取。"""
    state = _make_state("comp-001")
    await save_review(db_session, state)

    loaded = await get_review(db_session, "comp-001")
    assert loaded is not None
    assert len(loaded.compliance_findings) == 1
    assert loaded.compliance_findings[0].status == "non_compliant"
    assert loaded.compliance_findings[0].regulation == "《民法典》第585条"


@pytest.mark.asyncio
async def test_save_with_suggestions(db_session: AsyncSession):
    """修改建议的保存/读取。"""
    state = _make_state("sug-001")
    await save_review(db_session, state)

    loaded = await get_review(db_session, "sug-001")
    assert loaded is not None
    assert len(loaded.suggestions) == 1
    assert loaded.suggestions[0].original_text == "乙方承担无限责任"
    assert loaded.suggestions[0].priority == "high"


@pytest.mark.asyncio
async def test_update_feedback(db_session: AsyncSession):
    """人工反馈更新。"""
    state = _make_state("fb-001")
    await save_review(db_session, state)

    feedback = HumanFeedback(
        reviewer="张三",
        decision="approve",
        comments="风险可控，同意签约",
    )
    success = await update_feedback(db_session, "fb-001", feedback, ReviewStatus.APPROVED)
    assert success is True

    loaded = await get_review(db_session, "fb-001")
    assert loaded is not None
    assert loaded.status == ReviewStatus.APPROVED
    assert loaded.human_feedback is not None
    assert loaded.human_feedback.reviewer == "张三"
    assert loaded.human_feedback.decision == "approve"


@pytest.mark.asyncio
async def test_get_nonexistent(db_session: AsyncSession):
    """查询不存在的记录返回 None。"""
    loaded = await get_review(db_session, "nonexistent")
    assert loaded is None


@pytest.mark.asyncio
async def test_save_missing_clauses(db_session: AsyncSession):
    """缺失条款列表的保存/读取。"""
    state = _make_state("missing-001")
    await save_review(db_session, state)

    loaded = await get_review(db_session, "missing-001")
    assert loaded is not None
    assert loaded.missing_clauses == ["争议解决条款"]


@pytest.mark.asyncio
async def test_save_errors(db_session: AsyncSession):
    """错误列表的保存/读取。"""
    state = _make_state("errors-001")
    state.errors = ["Agent 2 执行超时", "LLM 返回格式错误"]
    await save_review(db_session, state)

    loaded = await get_review(db_session, "errors-001")
    assert loaded is not None
    assert len(loaded.errors) == 2
    assert "Agent 2 执行超时" in loaded.errors


@pytest.mark.asyncio
async def test_update_feedback_replaces_old(db_session: AsyncSession):
    """重复提交反馈时替换旧反馈。"""
    state = _make_state("replace-001")
    await save_review(db_session, state)

    # 第一次反馈
    fb1 = HumanFeedback(reviewer="张三", decision="approve", comments="同意")
    await update_feedback(db_session, "replace-001", fb1, ReviewStatus.APPROVED)

    # 第二次反馈（覆盖）
    fb2 = HumanFeedback(reviewer="李四", decision="reject", comments="不同意")
    await update_feedback(db_session, "replace-001", fb2, ReviewStatus.REJECTED)

    loaded = await get_review(db_session, "replace-001")
    assert loaded is not None
    assert loaded.status == ReviewStatus.REJECTED
    assert loaded.human_feedback.reviewer == "李四"
    assert loaded.human_feedback.decision == "reject"


@pytest.mark.asyncio
async def test_init_db_supports_in_memory_sqlite(monkeypatch):
    """Engine initialization omits queue-pool options unsupported by SQLite StaticPool."""
    monkeypatch.setattr(db_session_module, "engine", None)
    monkeypatch.setattr(db_session_module, "async_session_factory", None)

    db_session_module.init_db("sqlite+aiosqlite:///:memory:")
    try:
        assert db_session_module.engine is not None
        async with db_session_module.engine.connect() as connection:
            result = await connection.exec_driver_sql("SELECT 1")
            assert result.scalar_one() == 1
    finally:
        await db_session_module.close_db()
