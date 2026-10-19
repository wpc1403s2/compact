"""合同模板管理路由 — 模板 CRUD + 条款比对。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import CurrentUser, require_role
from ..schemas.templates import (
    TemplateCheckRequest,
    TemplateCheckResult,
    TemplateCreateRequest,
    TemplateListResponse,
    TemplateResponse,
    TemplateUpdateRequest,
)

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


def _get_service():
    from ...services.templates_service import TemplatesService
    return TemplatesService()


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    category: str | None = Query(None, description="按分类过滤"),
    user: CurrentUser | None = Depends(require_role("admin", "reviewer")),
):
    """列出所有模板。"""
    svc = _get_service()
    templates = svc.list_templates(category=category)
    return TemplateListResponse(
        total=len(templates),
        templates=[TemplateResponse(**t) for t in templates],
    )


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: str,
    user: CurrentUser | None = Depends(require_role("admin", "reviewer")),
):
    """获取单个模板。"""
    svc = _get_service()
    template = svc.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail=f"模板不存在: {template_id}")
    return TemplateResponse(**template)


@router.post("", response_model=TemplateResponse, status_code=201)
async def create_template(
    body: TemplateCreateRequest,
    user: CurrentUser | None = Depends(require_role("admin")),
):
    """创建新模板。"""
    svc = _get_service()
    try:
        template = svc.create_template(body.model_dump())
        return TemplateResponse(**template)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: str,
    body: TemplateUpdateRequest,
    user: CurrentUser | None = Depends(require_role("admin")),
):
    """更新模板。"""
    svc = _get_service()
    try:
        template = svc.update_template(template_id, body.model_dump(exclude_unset=True))
        return TemplateResponse(**template)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    user: CurrentUser | None = Depends(require_role("admin")),
):
    """删除模板。"""
    svc = _get_service()
    if not svc.delete_template(template_id):
        raise HTTPException(status_code=404, detail=f"模板不存在: {template_id}")
    return {"message": "模板已删除", "template_id": template_id}


@router.post("/check", response_model=TemplateCheckResult)
async def check_template(
    body: TemplateCheckRequest,
    user: CurrentUser | None = Depends(require_role("admin", "reviewer")),
):
    """将条款与模板必要条款进行比对。"""
    svc = _get_service()
    try:
        result = svc.check_clauses(body.template_id, body.clause_texts)
        return TemplateCheckResult(**result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
