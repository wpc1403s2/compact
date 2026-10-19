"""审查相关路由 — 薄壳层，只做请求解析和响应格式化。

支持两种模式：
- sync（默认）：同步执行审查，等待结果返回
- async：立即返回 review_id，后台异步执行，通过 GET /review/{id} 轮询状态+进度
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from ...pipeline.state import (
    ComplianceStatus,
    ContractReviewState,
    RiskLevel,
    StepEvent,
)
from ...services.review_service import ReviewService
from ...services.deps import get_review_service
from ..auth.dependencies import CurrentUser, get_current_user, require_role
from ..exceptions import (
    InvalidFileFormatError,
    ReviewNotFoundError,
    ReviewProcessingError,
)
from .metrics import record_review
from ..schemas.review import (
    BatchReviewItem,
    BatchReviewResponse,
    BatchStatusResponse,
    DetailedReviewResponse,
    FeedbackRequest,
    ReviewHistoryItem,
    ReviewHistoryResponse,
    ReviewRequest,
    ReviewResponse,
    StepEventSchema,
    TaskResponse,
)

router = APIRouter(prefix="/api/v1", tags=["review"])


# ── 辅助 ──────────────────────────────────────────────────

def _risk_score(state: ContractReviewState) -> float:
    """根据风险分布粗略计算 0–100 的风险评分。"""
    weights = {RiskLevel.HIGH: 30, RiskLevel.MEDIUM: 15, RiskLevel.LOW: 5}
    score = 0.0
    for f in state.risk_findings:
        score += weights.get(f.risk_level, 0)
    if state.overall_compliance == ComplianceStatus.NON_COMPLIANT:
        score += 10
    if state.missing_clauses:
        score += min(len(state.missing_clauses) * 5, 20)
    return round(min(score, 100), 1)


def _step_log_to_schema(events: list[StepEvent]) -> list[StepEventSchema]:
    return [
        StepEventSchema(
            step=e.step,
            status=e.status,
            message=e.message,
            started_at=e.started_at.isoformat() if e.started_at else "",
            finished_at=e.finished_at.isoformat() if e.finished_at else None,
        )
        for e in events
    ]


# ── 响应映射 ──────────────────────────────────────────────

def _to_summary(state: ContractReviewState) -> ReviewResponse:
    return ReviewResponse(
        review_id=state.review_id,
        status=state.status.value,
        contract_type=state.contract_type,
        clauses_count=len(state.clauses),
        risk_summary=state.risk_summary,
        overall_risk_level=state.overall_risk_level.value,
        overall_compliance=state.overall_compliance.value,
        suggestions_count=len(state.suggestions),
        needs_human_review=state.needs_human_review,
        filename=state.original_filename,
        created_at=state.created_at.isoformat() if state.created_at else "",
        updated_at=state.updated_at.isoformat() if state.updated_at else "",
        current_step=state.current_step,
        progress=state.progress,
        step_log=_step_log_to_schema(state.step_log),
    )


def _to_pending_task(review_id: str, msg: str = "审查任务已提交，正在后台处理") -> TaskResponse:
    return TaskResponse(review_id=review_id, task_id="", status="pending", message=msg)


def _to_detail(state: ContractReviewState) -> DetailedReviewResponse:
    return DetailedReviewResponse(
        review_id=state.review_id,
        status=state.status.value,
        contract_type=state.contract_type,
        filename=state.original_filename,
        created_at=state.created_at.isoformat() if state.created_at else "",
        updated_at=state.updated_at.isoformat() if state.updated_at else "",
        clauses=[c.model_dump(mode="json") for c in state.clauses],
        risk_findings=[r.model_dump(mode="json") for r in state.risk_findings],
        risk_summary=state.risk_summary,
        overall_risk_level=state.overall_risk_level.value,
        risk_score=_risk_score(state),
        compliance_findings=[c.model_dump(mode="json") for c in state.compliance_findings],
        missing_clauses=state.missing_clauses,
        overall_compliance=state.overall_compliance.value,
        suggestions=[s.model_dump(mode="json") for s in state.suggestions],
        version_diff=state.version_diff,
        needs_human_review=state.needs_human_review,
        human_feedback=state.human_feedback.model_dump(mode="json") if state.human_feedback else None,
        errors=state.errors,
        current_step=state.current_step,
        progress=state.progress,
        step_log=_step_log_to_schema(state.step_log),
    )


# ── 路由 ──────────────────────────────────────────────────

@router.get("/reviews", response_model=ReviewHistoryResponse)
async def list_reviews(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    svc: ReviewService = Depends(get_review_service),
    user: CurrentUser | None = Depends(get_current_user),
):
    """查询审查历史列表（按租户隔离）。"""
    offset = (page - 1) * page_size
    total = await svc.count()
    rows = await svc.list(offset=offset, limit=page_size)

    items = [
        ReviewHistoryItem(
            review_id=r["review_id"],
            status=r["status"],
            contract_type=r.get("contract_type", ""),
            overall_risk_level=r.get("overall_risk_level", "none"),
            overall_compliance=r.get("overall_compliance", "needs_review"),
            needs_human_review=r.get("needs_human_review", False),
            risk_summary=r.get("risk_summary", ""),
            filename=r.get("filename", ""),
            created_at=r.get("created_at", ""),
            updated_at=r.get("updated_at", ""),
        )
        for r in rows
    ]

    return ReviewHistoryResponse(total=total, items=items)


@router.post("/review", response_model=ReviewResponse | TaskResponse)
async def create_review(
    review_body: ReviewRequest = Body(..., embed=True),
    mode: str = Query("sync", description="sync=同步等待结果, async=异步返回review_id"),
    svc: ReviewService = Depends(get_review_service),
    user: CurrentUser | None = Depends(require_role("admin", "reviewer")),
):
    """提交合同文本进行审查。"""
    user_id = user.id if user else ""
    if mode == "async":
        try:
            rid = await svc.start_from_text(
                text=review_body.text,
                with_human_review=review_body.with_human_review,
                user_id=user_id,
            )
            return _to_pending_task(rid)
        except Exception as e:
            raise ReviewProcessingError(detail=str(e))

    try:
        state = await svc.create_from_text(
            text=review_body.text,
            with_human_review=review_body.with_human_review,
        )
        record_review(status=state.status.value, risk_level=state.overall_risk_level.value)
        return _to_summary(state)
    except Exception as e:
        raise ReviewProcessingError(detail=str(e))


@router.post("/review/upload", response_model=ReviewResponse | TaskResponse)
async def upload_review(
    file: UploadFile = File(...),
    with_human_review: bool = True,
    mode: str = Query("sync", description="sync=同步等待结果, async=异步返回review_id"),
    svc: ReviewService = Depends(get_review_service),
    user: CurrentUser | None = Depends(require_role("admin", "reviewer")),
):
    """上传合同文件进行审查（支持PDF/DOCX/TXT）。"""
    if not file.filename:
        raise InvalidFileFormatError(format="(空文件名)")

    ext = Path(file.filename).suffix.lower()
    if ext not in {".pdf", ".docx", ".doc", ".txt"}:
        raise InvalidFileFormatError(format=ext)

    user_id = user.id if user else ""
    try:
        content = await file.read()
        if mode == "async":
            rid = await svc.start_from_file(
                filename=file.filename,
                content=content,
                with_human_review=with_human_review,
                user_id=user_id,
            )
            return _to_pending_task(rid)

        state = await svc.create_from_file(
            filename=file.filename,
            content=content,
            with_human_review=with_human_review,
        )
        record_review(status=state.status.value, risk_level=state.overall_risk_level.value)
        return _to_summary(state)
    except ValueError as e:
        raise InvalidFileFormatError(format=str(e))
    except InvalidFileFormatError:
        raise
    except Exception as e:
        raise ReviewProcessingError(detail=str(e))


@router.post("/review/batch", response_model=BatchReviewResponse)
async def batch_upload_review(
    files: list[UploadFile] = File(..., description="最多10个合同文件"),
    with_human_review: bool = True,
    svc: ReviewService = Depends(get_review_service),
    user: CurrentUser | None = Depends(require_role("admin", "reviewer")),
):
    """批量上传合同文件进行审查（最多10个文件，支持PDF/DOCX/TXT）。"""
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="单次最多上传10个文件")

    file_data = []
    for f in files:
        content = await f.read()
        file_data.append((f.filename or "unnamed", content))

    try:
        batch_id, items = await svc.create_batch(
            files=file_data,
            with_human_review=with_human_review,
        )

        completed = sum(1 for i in items if i["status"] not in ("pending", "failed"))
        failed = sum(1 for i in items if i["status"] == "failed")

        return BatchReviewResponse(
            batch_id=batch_id,
            total=len(items),
            completed=completed,
            failed=failed,
            status="completed" if failed == 0 else "partial_failure" if completed > 0 else "failed",
            items=[BatchReviewItem(**i) for i in items],
        )
    except ValueError as e:
        raise InvalidFileFormatError(format=str(e))
    except Exception as e:
        raise ReviewProcessingError(detail=str(e))


@router.get("/review/{review_id}", response_model=DetailedReviewResponse)
async def get_review_detail(
    review_id: str,
    svc: ReviewService = Depends(get_review_service),
    user: CurrentUser | None = Depends(get_current_user),
):
    """查询审查详情（含当前进度，用于前端轮询）。"""
    state = await svc.get(review_id)
    if not state:
        raise ReviewNotFoundError(review_id=review_id)
    return _to_detail(state)


@router.post("/review/{review_id}/feedback")
async def submit_feedback(
    review_id: str,
    feedback_body: FeedbackRequest = Body(..., embed=True),
    svc: ReviewService = Depends(get_review_service),
    user: CurrentUser | None = Depends(require_role("admin", "reviewer")),
):
    """提交人工审核反馈。"""
    try:
        new_status = await svc.submit_feedback(
            review_id=review_id,
            reviewer=feedback_body.reviewer,
            decision=feedback_body.decision,
            comments=feedback_body.comments,
        )
        return {"message": "反馈提交成功", "status": new_status.value}
    except ValueError as e:
        raise ReviewNotFoundError(review_id=review_id)


@router.delete("/review/{review_id}")
async def delete_review(
    review_id: str,
    svc: ReviewService = Depends(get_review_service),
    user: CurrentUser | None = Depends(require_role("admin", "reviewer")),
):
    """删除审查记录及其所有关联数据。"""
    ok = await svc.delete(review_id)
    if not ok:
        raise ReviewNotFoundError(review_id=review_id)
    return {"message": "删除成功", "review_id": review_id}


@router.post("/review/{review_id}/rerun", response_model=ReviewResponse | TaskResponse)
async def rerun_review(
    review_id: str,
    with_human_review: bool = Query(True, description="是否启用人工审核"),
    mode: str = Query("sync", description="sync=同步等待, async=异步返回"),
    svc: ReviewService = Depends(get_review_service),
    user: CurrentUser | None = Depends(require_role("admin", "reviewer")),
):
    """基于已有审查的原文/原文件重新执行审查，生成新记录。"""
    user_id = user.id if user else ""
    try:
        result = await svc.rerun(
            review_id,
            with_human_review=with_human_review,
            async_mode=(mode == "async"),
            user_id=user_id,
        )
    except ValueError:
        raise ReviewNotFoundError(review_id=review_id)

    if isinstance(result, str):
        return _to_pending_task(result, "重新审查任务已提交")
    record_review(status=result.status.value, risk_level=result.overall_risk_level.value)
    return _to_summary(result)


@router.get("/review/{review_id}/report")
async def export_report(
    review_id: str,
    svc: ReviewService = Depends(get_review_service),
    user: CurrentUser | None = Depends(get_current_user),
):
    """导出审查报告为 PDF。"""
    import io

    from ...services.report_service import generate_report

    state = await svc.get(review_id)
    if not state:
        raise ReviewNotFoundError(review_id=review_id)

    pdf_bytes = generate_report(state)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="review_{review_id}.pdf"',
        },
    )


@router.get("/review/{review_id}/word")
async def export_word(
    review_id: str,
    svc: ReviewService = Depends(get_review_service),
    user: CurrentUser | None = Depends(get_current_user),
):
    """导出审查报告为 Word (.docx)。"""
    import io

    from ...services.report_service import generate_word

    state = await svc.get(review_id)
    if not state:
        raise ReviewNotFoundError(review_id=review_id)

    docx_bytes = generate_word(state)
    return StreamingResponse(
        io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="review_{review_id}.docx"',
        },
    )
