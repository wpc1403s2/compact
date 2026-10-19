"""审查历史/详情/删除/重跑 API 测试 — 使用内存存储，不依赖真实 LLM/数据库。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.dependencies.storage import MemoryStore
from src.api.main import app
from src.pipeline.state import (
    ComplianceFinding,
    ComplianceStatus,
    ContractReviewState,
    HumanFeedback,
    ReviewStatus,
    RiskFinding,
    RiskLevel,
    Suggestion,
)
from src.services.deps import get_review_service, get_storage_service
from src.services.review_service import ReviewService
from src.services.storage_service import StorageService


@pytest.fixture
def memory_store():
    return MemoryStore()


@pytest.fixture
def storage(memory_store):
    return StorageService(db=None, memory=memory_store, tenant_id="")


@pytest.fixture
def svc(storage):
    return ReviewService(storage=storage)


@pytest_asyncio.fixture
async def client(svc, storage):
    app.dependency_overrides[get_storage_service] = lambda: storage
    app.dependency_overrides[get_review_service] = lambda: svc
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _make_state(rid: str, filename: str = "", contract_type: str = "买卖合同") -> ContractReviewState:
    return ContractReviewState(
        review_id=rid,
        status=ReviewStatus.COMPLETED,
        contract_type=contract_type,
        original_filename=filename,
        clauses=[],
        risk_findings=[
            RiskFinding(
                clause_id="c1",
                risk_level=RiskLevel.HIGH,
                risk_type="无限责任",
                description="违约方承担无限责任",
            )
        ],
        overall_risk_level=RiskLevel.HIGH,
        risk_summary="存在高风险条款",
        compliance_findings=[
            ComplianceFinding(
                clause_id="c1",
                status=ComplianceStatus.NON_COMPLIANT,
                regulation="《民法典》第585条",
                issue="违约金过高",
                recommendation="设置上限",
            )
        ],
        missing_clauses=["争议解决"],
        overall_compliance=ComplianceStatus.NON_COMPLIANT,
        suggestions=[
            Suggestion(
                clause_id="c1",
                original_text="承担无限责任",
                suggested_text="赔偿以合同额20%为限",
                reason="过高",
                priority=RiskLevel.HIGH,
            )
        ],
        version_diff="建议修改",
        needs_human_review=True,
        errors=[],
    )


# ── 列表 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_empty(client):
    resp = await client.get("/api/v1/reviews")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_list_returns_items_with_filename(client, storage):
    s1 = _make_state("r1", filename="合同A.pdf")
    s2 = _make_state("r2", filename="")
    await storage.save(s1)
    await storage.save(s2)

    resp = await client.get("/api/v1/reviews")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    # 倒序
    assert data["items"][0]["review_id"] == "r2"
    assert data["items"][1]["review_id"] == "r1"
    # filename 字段
    r1 = next(i for i in data["items"] if i["review_id"] == "r1")
    assert r1["filename"] == "合同A.pdf"
    assert "created_at" in r1 and r1["created_at"]
    assert "overall_risk_level" in r1
    assert "overall_compliance" in r1


# ── 详情 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detail_returns_new_fields(client, storage):
    await storage.save(_make_state("r1", filename="合同A.pdf"))

    resp = await client.get("/api/v1/review/r1")
    assert resp.status_code == 200
    d = resp.json()
    assert d["review_id"] == "r1"
    assert d["filename"] == "合同A.pdf"
    assert d["created_at"]
    assert d["human_feedback"] is None
    assert d["risk_findings"][0]["risk_type"] == "无限责任"


@pytest.mark.asyncio
async def test_detail_includes_human_feedback(client, storage):
    state = _make_state("r1", filename="合同A.pdf")
    state.human_feedback = HumanFeedback(
        reviewer="张三",
        decision="approve",
        comments="同意签署",
    )
    await storage.save(state)

    resp = await client.get("/api/v1/review/r1")
    assert resp.status_code == 200
    fb = resp.json()["human_feedback"]
    assert fb is not None
    assert fb["reviewer"] == "张三"
    assert fb["decision"] == "approve"
    assert fb["comments"] == "同意签署"


@pytest.mark.asyncio
async def test_detail_not_found(client):
    resp = await client.get("/api/v1/review/nope")
    assert resp.status_code == 404


# ── 删除 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_existing(client, storage):
    await storage.save(_make_state("r1"))

    resp = await client.delete("/api/v1/review/r1")
    assert resp.status_code == 200
    assert resp.json()["review_id"] == "r1"

    # 再查应 404
    assert await storage.load("r1") is None
    resp2 = await client.get("/api/v1/review/r1")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent(client):
    resp = await client.delete("/api/v1/review/nope")
    assert resp.status_code == 404


# ── 重新审查 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rerun_returns_new_review(client, storage, monkeypatch):
    """rerun 使用 _run_pipeline_with_progress；为避免真实跑 LLM，打桩返回一个完成态。"""
    from src.services import review_service as rs_mod

    original = _make_state("r-old", filename="合同A.pdf", contract_type="采购合同")
    original.raw_text = "甲方应在30日内向乙方付款..."
    await storage.save(original)

    async def fake_run_with_progress(self, review_id, initial_state, with_human_review, user_id=""):
        # 复用 initial_state 的输入，返回 completed 状态并持久化
        s = _make_state(review_id, filename=initial_state.original_filename,
                        contract_type="采购合同")
        s.raw_text = initial_state.raw_text
        s.document_path = initial_state.document_path
        s.status = ReviewStatus.COMPLETED
        s.progress = 1.0
        s.updated_at = s.created_at
        await self._persist_state(s, user_id=user_id)
        return s

    monkeypatch.setattr(ReviewService, "_run_pipeline_with_progress", fake_run_with_progress)

    resp = await client.post("/api/v1/review/r-old/rerun")
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "合同A.pdf"
    new_id = data["review_id"]
    assert new_id != "r-old"

    # 新记录能被查到，且旧记录仍然存在
    new_state = await storage.load(new_id)
    assert new_state is not None
    assert new_state.original_filename == "合同A.pdf"
    assert new_state.raw_text.startswith("甲方")

    old_state = await storage.load("r-old")
    assert old_state is not None

    # 列表两条
    listed = await client.get("/api/v1/reviews")
    assert listed.json()["total"] == 2


@pytest.mark.asyncio
async def test_rerun_nonexistent(client):
    resp = await client.post("/api/v1/review/nope/rerun")
    assert resp.status_code == 404
