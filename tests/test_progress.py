"""进度追踪功能测试：step_log、upsert、embed body、async flow。"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base
from src.db.session import get_db
from src.db.crud import save_review, get_review
from src.pipeline.state import (
    ContractReviewState,
    ReviewStatus,
    RiskLevel,
    ComplianceStatus,
    ClauseCategory,
    StepEvent,
    PIPELINE_STEPS,
    STEP_LABELS,
)
from src.pipeline import graph as graph_mod
from src.services.storage_service import StorageService
from src.services.review_service import ReviewService
from src.api.dependencies.storage import MemoryStore, get_memory_store
from src.services.deps import get_review_service
from src.api.main import app


# ── 纯单元：StepEvent 模型 ──────────────────────────────────

def test_pipeline_steps_constants():
    assert PIPELINE_STEPS == ["clause_extraction", "risk_identification", "compliance_check", "suggestion"]
    assert STEP_LABELS["compliance_check"] == "合规检查"
    assert STEP_LABELS["suggestion"] == "修改建议"


def test_step_event_defaults():
    ev = StepEvent(step="clause_extraction", status="running")
    assert ev.status == "running"
    assert ev.message == ""
    assert isinstance(ev.started_at, datetime)
    assert ev.finished_at is None


def test_state_default_progress_fields():
    s = ContractReviewState(review_id="x")
    assert s.current_step == ""
    assert s.step_log == []
    assert s.progress == 0.0


# ── Upsert：同一个 review_id 多次 save 不产生重复 ─────────────

@pytest.mark.asyncio
async def test_db_save_is_upsert():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        s = ContractReviewState(review_id="u1", status=ReviewStatus.PENDING)
        await save_review(session, s)
        await session.commit()

    # 第二次保存（更新进度）
    async with factory() as session:
        s.current_step = "clause_extraction"
        s.progress = 0.25
        s.step_log.append(StepEvent(step="clause_extraction", status="done", message="提取 3 条"))
        await save_review(session, s)
        await session.commit()

    async with factory() as session:
        loaded = await get_review(session, "u1")
        assert loaded is not None
        assert loaded.current_step == "clause_extraction"
        assert loaded.progress == 0.25
        assert len(loaded.step_log) == 1
        assert loaded.step_log[0].message == "提取 3 条"

    await engine.dispose()


# ── 内存模式 upsert：覆盖保存 ────────────────────────────────

@pytest.mark.asyncio
async def test_memory_storage_upsert():
    mem = MemoryStore()
    storage = StorageService(db=None, memory=mem)
    s = ContractReviewState(review_id="m1", status=ReviewStatus.PENDING, progress=0.0)
    await storage.save(s)
    s.progress = 0.5
    s.current_step = "risk_identification"
    await storage.save(s)
    loaded = await storage.load("m1")
    assert loaded is not None
    assert loaded.progress == 0.5
    assert loaded.current_step == "risk_identification"


# ── 进度包装：节点运行时 step_log 记录 running/done 事件 ─────

@pytest.mark.asyncio
async def test_progress_wrapper_records_events():
    from src.agents.clause_extraction import ClauseExtractionAgent
    from src.pipeline.graph import _with_progress, set_progress_callback

    events: list[ContractReviewState] = []

    async def cb(state: ContractReviewState):
        # 拷贝当前 step_log，避免后续 mutation 影响
        events.append(state.model_copy(deep=True))

    # 打桩一个极简 agent：返回一个 clause
    class FakeAgent:
        async def __call__(self, state):
            return {"contract_type": "采购合同", "clauses": []}

    set_progress_callback("rid-1", cb)
    try:
        wrapped = _with_progress("clause_extraction", FakeAgent())
        state = ContractReviewState(review_id="rid-1", raw_text="demo")
        await wrapped(state)

        # 应该有 2 次回调：running 一次，done 一次
        assert len(events) >= 2
        running_ev = next((e for e in events if any(x.status == "running" for x in e.step_log)), None)
        done_ev = next((e for e in events if any(x.status == "done" for x in e.step_log)), None)
        assert running_ev is not None
        assert done_ev is not None
        assert done_ev.progress == 0.25  # 1/4
        assert done_ev.current_step == ""
    finally:
        set_progress_callback("rid-1", None)


# ── API：embed body + mode=async 返回 TaskResponse ──────────

@pytest.fixture
def client():
    mem = MemoryStore()
    storage = StorageService(db=None, memory=mem)

    def override_memory():
        return mem

    def override_svc():
        return ReviewService(storage=storage)

    # 打桩 _run_pipeline_with_progress 让它立即完成
    async def fake_run(self, review_id, initial_state, with_human_review, user_id=""):
        from src.pipeline.state import Clause, ClauseCategory
        initial_state.status = ReviewStatus.COMPLETED
        initial_state.progress = 1.0
        initial_state.clauses = [
            Clause(id="c1", title="付款条款", content="30日内付款", category=ClauseCategory.PAYMENT)
        ]
        initial_state.step_log = [
            StepEvent(step=k, status="done", message=f"{STEP_LABELS[k]}完成")
            for k in PIPELINE_STEPS
        ]
        await self._persist_state(initial_state, user_id=user_id)
        return initial_state

    old_run = ReviewService._run_pipeline_with_progress
    ReviewService._run_pipeline_with_progress = fake_run
    try:
        app.dependency_overrides[get_memory_store] = override_memory
        app.dependency_overrides[get_review_service] = override_svc
        # 覆盖 get_db 返回 None（强制走内存）
        async def _no_db():
            yield None
        app.dependency_overrides[get_db] = _no_db

        from fastapi.testclient import TestClient
        c = TestClient(app)
        # 注册并登录 admin 用户，以便通过 require_role
        try:
            c.post("/api/v1/auth/register", json={
                "username": "reviewer", "email": "r@x.com", "password": "secret123", "role": "admin",
            })
        except Exception:
            pass
        login = c.post("/api/v1/auth/login", json={"username": "reviewer", "password": "secret123"})
        token = login.json().get("access_token", "")
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c
    finally:
        ReviewService._run_pipeline_with_progress = old_run
        app.dependency_overrides.clear()


def test_async_text_submit_returns_task_response(client):
    """POST /review?mode=async 使用 embed body 并返回 TaskResponse。"""
    resp = client.post(
        "/api/v1/review?mode=async",
        json={"review_body": {"text": "甲方应在30日内向乙方付款", "with_human_review": False}},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["review_id"]
    assert data["status"] in ("pending", "completed")


def test_detail_includes_progress_fields(client):
    """提交任务后轮询详情，应包含 current_step/step_log/progress。"""
    r = client.post(
        "/api/v1/review?mode=async",
        json={"review_body": {"text": "甲方应在30日内向乙方付款", "with_human_review": False}},
    )
    rid = r.json()["review_id"]
    detail = client.get(f"/api/v1/review/{rid}")
    assert detail.status_code == 200
    d = detail.json()
    assert "step_log" in d
    assert "progress" in d
    assert "current_step" in d
    # 桩数据设置了 4 个 done 事件
    assert len(d["step_log"]) == 4
    assert d["progress"] == 1.0
    assert d["status"] == "completed"


def test_feedback_embed_body(client):
    """POST /review/{id}/feedback 使用 feedback_body 嵌套。"""
    r = client.post(
        "/api/v1/review?mode=async",
        json={"review_body": {"text": "demo", "with_human_review": True}}
    )
    rid = r.json()["review_id"]
    # 等完成
    import time
    time.sleep(0.2)
    resp = client.post(
        f"/api/v1/review/{rid}/feedback",
        json={"feedback_body": {"reviewer": "审核员", "decision": "approve", "comments": "ok"}},
    )
    assert resp.status_code == 200, resp.text
