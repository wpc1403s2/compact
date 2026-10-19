"""CRUD 操作 — Pydantic ContractReviewState ↔ ORM 模型的序列化桥接。"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..pipeline.state import (
    Clause,
    ComplianceFinding,
    ContractEntity,
    ContractReviewState,
    HumanFeedback,
    RiskFinding,
    ReviewStatus,
    StepEvent,
    Suggestion,
)
from .models import (
    ClauseRecord,
    ComplianceFindingRecord,
    HumanFeedbackRecord,
    ReviewRecord,
    RiskFindingRecord,
    SuggestionRecord,
)

logger = logging.getLogger(__name__)


# ── Pydantic → ORM ──────────────────────────────────────────

def _save_clauses(db: AsyncSession, review_id: str, clauses: list[Clause]) -> None:
    for c in clauses:
        db.add(ClauseRecord(
            id=c.id,
            review_id=review_id,
            title=c.title,
            content=c.content,
            category=c.category.value,
            section_number=c.section_number,
            entities=[e.model_dump() for e in c.entities],
        ))


def _save_risk_findings(db: AsyncSession, review_id: str, findings: list[RiskFinding]) -> None:
    for f in findings:
        db.add(RiskFindingRecord(
            review_id=review_id,
            clause_id=f.clause_id,
            risk_level=f.risk_level.value,
            risk_type=f.risk_type,
            description=f.description,
            buyer_impact=f.buyer_impact,
            seller_impact=f.seller_impact,
            rationale=f.rationale,
        ))


def _save_compliance_findings(db: AsyncSession, review_id: str, findings: list[ComplianceFinding]) -> None:
    for f in findings:
        db.add(ComplianceFindingRecord(
            review_id=review_id,
            clause_id=f.clause_id,
            status=f.status.value,
            regulation=f.regulation,
            issue=f.issue,
            recommendation=f.recommendation,
        ))


def _save_suggestions(db: AsyncSession, review_id: str, items: list[Suggestion]) -> None:
    for s in items:
        db.add(SuggestionRecord(
            review_id=review_id,
            clause_id=s.clause_id,
            original_text=s.original_text,
            suggested_text=s.suggested_text,
            reason=s.reason,
            priority=s.priority.value,
        ))


def _save_human_feedback(db: AsyncSession, review_id: str, feedback: HumanFeedback) -> None:
    db.add(HumanFeedbackRecord(
        review_id=review_id,
        reviewer=feedback.reviewer,
        decision=feedback.decision,
        comments=feedback.comments,
        modified_suggestions=[s.model_dump() for s in feedback.modified_suggestions],
        timestamp=feedback.timestamp,
    ))


# ── ORM → Pydantic ──────────────────────────────────────────

def _load_clauses(records: list[ClauseRecord]) -> list[Clause]:
    return [
        Clause(
            id=r.id,
            title=r.title,
            content=r.content,
            category=r.category,
            section_number=r.section_number,
            entities=[ContractEntity(**e) for e in (r.entities or [])],
        )
        for r in records
    ]


def _load_risk_findings(records: list[RiskFindingRecord]) -> list[RiskFinding]:
    return [
        RiskFinding(
            clause_id=r.clause_id,
            risk_level=r.risk_level,
            risk_type=r.risk_type,
            description=r.description,
            buyer_impact=r.buyer_impact,
            seller_impact=r.seller_impact,
            rationale=r.rationale,
        )
        for r in records
    ]


def _load_compliance_findings(records: list[ComplianceFindingRecord]) -> list[ComplianceFinding]:
    return [
        ComplianceFinding(
            clause_id=r.clause_id,
            status=r.status,
            regulation=r.regulation,
            issue=r.issue,
            recommendation=r.recommendation,
        )
        for r in records
    ]


def _load_suggestions(records: list[SuggestionRecord]) -> list[Suggestion]:
    return [
        Suggestion(
            clause_id=r.clause_id,
            original_text=r.original_text,
            suggested_text=r.suggested_text,
            reason=r.reason,
            priority=r.priority,
        )
        for r in records
    ]


def _load_human_feedback(record: HumanFeedbackRecord | None) -> HumanFeedback | None:
    if record is None:
        return None
    return HumanFeedback(
        reviewer=record.reviewer,
        decision=record.decision,
        comments=record.comments,
        modified_suggestions=[Suggestion(**s) for s in (record.modified_suggestions or [])],
        timestamp=record.timestamp,
    )


# ── 公开 CRUD 接口 ──────────────────────────────────────────

async def save_review(db: AsyncSession, state: ContractReviewState, tenant_id: str = "", user_id: str = "") -> str:
    """将 ContractReviewState 保存到数据库（upsert 语义），返回 review_id。

    若记录已存在则先删除（级联清理子表）再插入新记录，保证中间进度也能持久化。
    """
    existing = await db.execute(
        select(ReviewRecord).where(ReviewRecord.id == state.review_id)
    )
    old = existing.scalar_one_or_none()
    if old is not None:
        await db.delete(old)
        await db.flush()

    review = ReviewRecord(
        id=state.review_id,
        tenant_id=tenant_id,
        user_id=user_id,
        status=state.status.value,
        created_at=state.created_at,
        updated_at=state.updated_at,
        document_path=state.document_path,
        original_filename=state.original_filename,
        raw_text=state.raw_text,
        contract_type=state.contract_type,
        overall_risk_level=state.overall_risk_level.value,
        risk_summary=state.risk_summary,
        overall_compliance=state.overall_compliance.value,
        missing_clauses=state.missing_clauses,
        version_diff=state.version_diff,
        needs_human_review=state.needs_human_review,
        errors=state.errors,
        current_step=state.current_step,
        step_log=[e.model_dump(mode="json") for e in state.step_log],
        progress=state.progress,
    )
    db.add(review)

    _save_clauses(db, state.review_id, state.clauses)
    _save_risk_findings(db, state.review_id, state.risk_findings)
    _save_compliance_findings(db, state.review_id, state.compliance_findings)
    _save_suggestions(db, state.review_id, state.suggestions)

    if state.human_feedback:
        _save_human_feedback(db, state.review_id, state.human_feedback)

    logger.debug("审查记录已保存 review_id=%s status=%s progress=%.0f%%",
                 state.review_id, state.status.value, state.progress * 100)
    return state.review_id


async def get_review(db: AsyncSession, review_id: str, tenant_id: str = "") -> ContractReviewState | None:
    """从数据库加载审查记录，返回 ContractReviewState。"""
    stmt = select(ReviewRecord).where(ReviewRecord.id == review_id)
    if tenant_id:
        stmt = stmt.where(ReviewRecord.tenant_id == tenant_id)
    stmt = stmt.options(
        selectinload(ReviewRecord.clauses),
        selectinload(ReviewRecord.risk_findings),
        selectinload(ReviewRecord.compliance_findings),
        selectinload(ReviewRecord.suggestions),
        selectinload(ReviewRecord.human_feedback),
    )
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()

    if record is None:
        return None

    return ContractReviewState(
        review_id=record.id,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        document_path=record.document_path,
        original_filename=record.original_filename,
        raw_text=record.raw_text,
        contract_type=record.contract_type,
        clauses=_load_clauses(record.clauses),
        risk_findings=_load_risk_findings(record.risk_findings),
        overall_risk_level=record.overall_risk_level,
        risk_summary=record.risk_summary,
        compliance_findings=_load_compliance_findings(record.compliance_findings),
        missing_clauses=record.missing_clauses or [],
        overall_compliance=record.overall_compliance,
        suggestions=_load_suggestions(record.suggestions),
        version_diff=record.version_diff,
        needs_human_review=record.needs_human_review,
        human_feedback=_load_human_feedback(record.human_feedback),
        errors=record.errors or [],
        current_step=getattr(record, "current_step", "") or "",
        step_log=[StepEvent(**e) for e in (getattr(record, "step_log", None) or [])],
        progress=getattr(record, "progress", 0.0) or 0.0,
    )


async def update_feedback(
    db: AsyncSession,
    review_id: str,
    feedback: HumanFeedback,
    status: ReviewStatus,
) -> bool:
    """更新人工反馈和审查状态，返回是否成功。"""
    stmt = select(ReviewRecord).where(ReviewRecord.id == review_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()

    if record is None:
        return False

    record.status = status.value

    # 删除旧反馈（如果有）
    old_stmt = select(HumanFeedbackRecord).where(HumanFeedbackRecord.review_id == review_id)
    old_result = await db.execute(old_stmt)
    old_record = old_result.scalar_one_or_none()
    if old_record:
        await db.delete(old_record)
        await db.flush()  # 先刷新删除操作，避免唯一约束冲突

    _save_human_feedback(db, review_id, feedback)

    logger.info("人工反馈已更新 review_id=%s status=%s", review_id, status.value)
    return True


async def list_reviews(
    db: AsyncSession,
    tenant_id: str = "",
    offset: int = 0,
    limit: int = 20,
) -> list[ReviewRecord]:
    """列出审查记录（按 tenant_id 过滤）。"""
    stmt = select(ReviewRecord)
    if tenant_id:
        stmt = stmt.where(ReviewRecord.tenant_id == tenant_id)
    stmt = stmt.order_by(ReviewRecord.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def count_reviews(db: AsyncSession, tenant_id: str = "") -> int:
    """统计审查记录总数。"""
    from sqlalchemy import func
    stmt = select(func.count(ReviewRecord.id))
    if tenant_id:
        stmt = stmt.where(ReviewRecord.tenant_id == tenant_id)
    result = await db.execute(stmt)
    return result.scalar()


async def delete_review(db: AsyncSession, review_id: str, tenant_id: str = "") -> bool:
    """删除审查记录（级联删除子表），返回是否找到并删除。"""
    stmt = select(ReviewRecord).where(ReviewRecord.id == review_id)
    if tenant_id:
        stmt = stmt.where(ReviewRecord.tenant_id == tenant_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if record is None:
        return False
    await db.delete(record)
    logger.info("审查记录已删除 review_id=%s", review_id)
    return True
