"""数据库会话管理 — engine、session 工厂、FastAPI 依赖注入。"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""
    pass


engine = None
async_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db(database_url: str) -> None:
    """初始化数据库连接（应用启动时调用一次）。

    连接池配置：
    - pool_size: 常驻连接数（默认 10，生产环境根据并发量调整）
    - max_overflow: 溢出连接数（高峰期额外创建）
    - pool_pre_ping: 每次取连接前 ping 检测有效性（防断连）
    - pool_recycle: 连接回收时间（防 PostgreSQL idle 超时断连）
    """
    global engine, async_session_factory

    pool_size = int(settings.DB_POOL_SIZE)
    max_overflow = int(settings.DB_MAX_OVERFLOW)
    backend = make_url(database_url).get_backend_name()
    engine_options = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }
    if backend != "sqlite":
        engine_options.update(
            pool_size=pool_size,
            max_overflow=max_overflow,
        )

    new_engine = create_async_engine(
        database_url,
        echo=False,
        **engine_options,
    )
    engine = new_engine
    async_session_factory = async_sessionmaker(
        new_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    logger.info(
        "数据库连接已初始化: backend=%s pool_size=%s max_overflow=%s",
        backend,
        pool_size if backend != "sqlite" else "default",
        max_overflow if backend != "sqlite" else "default",
    )


async def get_db() -> AsyncGenerator[AsyncSession | None, None]:
    """FastAPI 依赖注入：获取数据库 session。"""
    if async_session_factory is None:
        yield None
        return

    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db() -> None:
    """关闭数据库连接。"""
    global engine
    if engine:
        await engine.dispose()
        logger.info("数据库连接已关闭")
