"""服务依赖注入 — FastAPI Depends 工厂。"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth.dependencies import CurrentUser, get_current_user
from ..api.dependencies.storage import MemoryStore, get_memory_store
from ..db.session import get_db
from .review_service import ReviewService
from .storage_service import StorageService


def get_storage_service(
    db: AsyncSession = Depends(get_db),
    memory: MemoryStore = Depends(get_memory_store),
    user: CurrentUser | None = Depends(get_current_user),
) -> StorageService:
    """FastAPI 依赖：创建 StorageService 实例，注入 tenant_id。"""
    tenant_id = user.tenant_id if user else ""
    return StorageService(db=db, memory=memory, tenant_id=tenant_id)


def get_review_service(
    storage: StorageService = Depends(get_storage_service),
) -> ReviewService:
    """FastAPI 依赖：创建 ReviewService 实例。"""
    return ReviewService(storage=storage)
