"""服务层单元测试 — 使用内存存储，不依赖数据库。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.dependencies.storage import MemoryStore
from src.db.models import Base
from src.pipeline.state import (
    ContractReviewState,
    ReviewStatus,
)
from src.services.storage_service import StorageService
from src.services.review_service import ReviewService


# ── Fixtures ──────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def memory_store():
    return MemoryStore()


# ── StorageService 测试 ──────────────────────────────────

class TestStorageServiceMemory:
    """内存存储模式测试。"""

    @pytest.mark.asyncio
    async def test_save_and_load(self, memory_store):
        storage = StorageService(db=None, memory=memory_store)
        state = ContractReviewState(review_id="s1", status=ReviewStatus.COMPLETED)

        await storage.save(state)
        loaded = await storage.load("s1")

        assert loaded is not None
        assert loaded.review_id == "s1"
        assert loaded.status == ReviewStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_load_nonexistent(self, memory_store):
        storage = StorageService(db=None, memory=memory_store)
        loaded = await storage.load("nonexistent")
        assert loaded is None

    @pytest.mark.asyncio
    async def test_is_persistent_false(self, memory_store):
        storage = StorageService(db=None, memory=memory_store)
        assert storage.is_persistent is False

    @pytest.mark.asyncio
    async def test_update_feedback(self, memory_store):
        from src.pipeline.state import HumanFeedback

        storage = StorageService(db=None, memory=memory_store)
        state = ContractReviewState(review_id="fb1", status=ReviewStatus.COMPLETED)
        await storage.save(state)

        feedback = HumanFeedback(reviewer="张三", decision="approve", comments="同意")
        await storage.update_feedback("fb1", feedback, ReviewStatus.APPROVED)

        loaded = await storage.load("fb1")
        assert loaded.status == ReviewStatus.APPROVED
        assert loaded.human_feedback.reviewer == "张三"


class TestStorageServiceDB:
    """数据库存储模式测试。"""

    @pytest.mark.asyncio
    async def test_save_and_load(self, db_session, memory_store):
        storage = StorageService(db=db_session, memory=memory_store)
        state = ContractReviewState(review_id="s2", status=ReviewStatus.COMPLETED)

        await storage.save(state)
        loaded = await storage.load("s2")

        assert loaded is not None
        assert loaded.review_id == "s2"

    @pytest.mark.asyncio
    async def test_is_persistent_true(self, db_session, memory_store):
        storage = StorageService(db=db_session, memory=memory_store)
        assert storage.is_persistent is True


# ── ReviewService 测试 ──────────────────────────────────

class TestReviewService:
    """ReviewService 业务逻辑测试。"""

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, memory_store):
        storage = StorageService(db=None, memory=memory_store)
        svc = ReviewService(storage=storage)

        result = await svc.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_submit_feedback_nonexistent(self, memory_store):
        storage = StorageService(db=None, memory=memory_store)
        svc = ReviewService(storage=storage)

        with pytest.raises(ValueError, match="审查记录不存在"):
            await svc.submit_feedback("nonexistent", "张三", "approve")

    def test_resolve_status_approve(self):
        status = ReviewService._resolve_status("approve")
        assert status == ReviewStatus.APPROVED

    def test_resolve_status_reject(self):
        status = ReviewService._resolve_status("reject")
        assert status == ReviewStatus.REJECTED

    def test_resolve_status_modify(self):
        status = ReviewService._resolve_status("modify")
        assert status == ReviewStatus.COMPLETED

    def test_resolve_status_unknown(self):
        status = ReviewService._resolve_status("unknown")
        assert status == ReviewStatus.COMPLETED
