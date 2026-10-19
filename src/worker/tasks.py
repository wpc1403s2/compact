"""Celery 任务定义 — 合同审查异步任务。"""

from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path

from .app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="worker.review_text",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def review_text(
    self,
    review_id: str,
    text: str,
    with_human_review: bool = True,
) -> dict:
    """异步审查合同文本。

    返回: {"review_id": str, "status": str, "error": str | None}
    """
    import asyncio

    logger.info("开始异步审查 review_id=%s", review_id)

    try:
        state = asyncio.get_event_loop().run_until_complete(
            _run_review_pipeline(review_id=review_id, raw_text=text, with_human_review=with_human_review)
        )
        logger.info("异步审查完成 review_id=%s status=%s", review_id, state.status.value)
        return {
            "review_id": review_id,
            "status": state.status.value,
            "error": None,
        }
    except Exception as exc:
        logger.error("异步审查失败 review_id=%s: %s", review_id, exc)
        # 重试一次
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {
            "review_id": review_id,
            "status": "failed",
            "error": str(exc),
        }


@celery_app.task(
    name="worker.review_file",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def review_file(
    self,
    review_id: str,
    file_path: str,
    with_human_review: bool = True,
) -> dict:
    """异步审查合同文件。

    返回: {"review_id": str, "status": str, "error": str | None}
    """
    import asyncio

    logger.info("开始异步文件审查 review_id=%s file=%s", review_id, file_path)

    try:
        state = asyncio.get_event_loop().run_until_complete(
            _run_review_pipeline(review_id=review_id, document_path=file_path, with_human_review=with_human_review)
        )
        logger.info("异步文件审查完成 review_id=%s status=%s", review_id, state.status.value)
        return {
            "review_id": review_id,
            "status": state.status.value,
            "error": None,
        }
    except Exception as exc:
        logger.error("异步文件审查失败 review_id=%s: %s", review_id, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {
            "review_id": review_id,
            "status": "failed",
            "error": str(exc),
        }


async def _run_review_pipeline(
    review_id: str,
    raw_text: str = "",
    document_path: str = "",
    with_human_review: bool = True,
):
    """执行审查流水线并保存结果到数据库。"""
    from ..db import session as db_session
    from ..db.crud import save_review
    from ..db.models import Base
    from ..pipeline.graph import create_review_pipeline
    from ..pipeline.state import ContractReviewState, ReviewStatus

    # 初始化数据库连接（Worker 进程独立）
    db_session.init_db(settings.DATABASE_URL)
    async with db_session.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 创建初始状态
    initial_state = ContractReviewState(
        review_id=review_id,
        raw_text=raw_text,
        document_path=document_path,
        status=ReviewStatus.IN_PROGRESS,
    )

    # 执行流水线
    pipeline = create_review_pipeline(
        with_human_review=with_human_review,
        with_checkpointing=False,
    )
    config = {"configurable": {"thread_id": review_id}}
    result = await pipeline.ainvoke(initial_state.model_dump(), config=config)
    state = ContractReviewState(**result)

    # 保存到数据库
    if db_session.async_session_factory:
        async with db_session.async_session_factory() as session:
            await save_review(session, state)
            await session.commit()

    await db_session.engine.dispose()
    return state


# 延迟导入 settings（避免循环依赖）
from ..config import settings
