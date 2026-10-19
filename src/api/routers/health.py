"""健康检查路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db.session import get_db
from ..schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/api/v1/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    """健康检查 — 返回服务状态和数据库连接状态。"""
    db_status = "connected" if db is not None else "disconnected"
    return HealthResponse(
        status="healthy",
        service="contract-review",
        version="1.2.0",
        database=db_status,
    )
