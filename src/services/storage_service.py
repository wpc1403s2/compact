"""存储服务 — 统一 DB / 内存存储的抽象层，支持多租户隔离。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.crud import (
    get_review as db_get_review,
    list_reviews as db_list_reviews,
    count_reviews as db_count_reviews,
    delete_review as db_delete_review,
    save_review,
    update_feedback,
)
from ..pipeline.state import ContractReviewState, HumanFeedback, ReviewStatus

if TYPE_CHECKING:
    from ..api.dependencies.storage import MemoryStore

logger = logging.getLogger(__name__)


class StorageService:
    """存储服务：根据是否有数据库 session 自动选择存储后端。

    - 有 db session → 持久化到 PostgreSQL（按 tenant_id 隔离）
    - 无 db session → 降级到内存存储（开发/测试用）
    """

    def __init__(self, db: AsyncSession | None, memory: MemoryStore, tenant_id: str = "") -> None:
        self._db = db
        self._memory = memory
        self._tenant_id = tenant_id

    @property
    def is_persistent(self) -> bool:
        """是否使用持久化存储。"""
        return self._db is not None

    async def save(self, state: ContractReviewState, user_id: str = "") -> None:
        """保存审查状态。"""
        if self._db is not None:
            await save_review(self._db, state, tenant_id=self._tenant_id, user_id=user_id)
            logger.debug("审查记录已保存到数据库 review_id=%s tenant=%s", state.review_id, self._tenant_id)
        else:
            self._memory.set(state.review_id, state.model_dump())
            logger.debug("审查记录已保存到内存 review_id=%s", state.review_id)

    async def load(self, review_id: str) -> ContractReviewState | None:
        """加载审查状态。"""
        if self._db is not None:
            return await db_get_review(self._db, review_id, tenant_id=self._tenant_id)
        data = self._memory.get(review_id)
        return ContractReviewState(**data) if data else None

    async def update_feedback(
        self,
        review_id: str,
        feedback: HumanFeedback,
        status: ReviewStatus,
    ) -> None:
        """更新人工反馈。"""
        if self._db is not None:
            await update_feedback(self._db, review_id, feedback, status)
        else:
            state = await self.load(review_id)
            if state:
                state.human_feedback = feedback
                state.status = status
                self._memory.set(review_id, state.model_dump())

    async def list_reviews(self, offset: int = 0, limit: int = 20) -> list[dict]:
        """列出当前租户的审查记录。"""
        if self._db is not None:
            records = await db_list_reviews(self._db, tenant_id=self._tenant_id, offset=offset, limit=limit)
            return [
                {
                    "review_id": r.id,
                    "status": r.status,
                    "contract_type": r.contract_type or "",
                    "overall_risk_level": r.overall_risk_level,
                    "overall_compliance": r.overall_compliance,
                    "needs_human_review": r.needs_human_review,
                    "filename": r.original_filename or "",
                    "risk_summary": r.risk_summary or "",
                    "created_at": r.created_at.isoformat() if r.created_at else "",
                    "updated_at": r.updated_at.isoformat() if r.updated_at else "",
                }
                for r in records
            ]
        def _fmt_dt(v) -> str:
            if not v:
                return ""
            if hasattr(v, "isoformat"):
                return v.isoformat()
            return str(v)

        items: list[dict] = []
        for k in self._memory.list_keys():
            data = self._memory.get(k)
            if not data:
                continue
            items.append({
                "review_id": data.get("review_id", k),
                "status": data.get("status", "completed"),
                "contract_type": data.get("contract_type", ""),
                "overall_risk_level": (data.get("overall_risk_level") or "none").value if hasattr(data.get("overall_risk_level"), "value") else str(data.get("overall_risk_level") or "none"),
                "overall_compliance": (data.get("overall_compliance") or "needs_review").value if hasattr(data.get("overall_compliance"), "value") else str(data.get("overall_compliance") or "needs_review"),
                "needs_human_review": bool(data.get("needs_human_review", False)),
                "filename": data.get("original_filename", "") or "",
                "risk_summary": data.get("risk_summary", "") or "",
                "created_at": _fmt_dt(data.get("created_at")),
                "updated_at": _fmt_dt(data.get("updated_at")),
            })
        items.sort(key=lambda x: x["created_at"], reverse=True)
        return items[offset:offset + limit]

    async def count_reviews(self) -> int:
        """统计当前租户的审查记录总数。"""
        if self._db is not None:
            return await db_count_reviews(self._db, tenant_id=self._tenant_id)
        return self._memory.size

    async def delete(self, review_id: str) -> bool:
        """删除审查记录。"""
        if self._db is not None:
            return await db_delete_review(self._db, review_id, tenant_id=self._tenant_id)
        return self._memory.delete(review_id)
