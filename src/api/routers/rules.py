"""法规库管理路由 — 规则 CRUD + 版本管理。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import CurrentUser, require_role
from ..schemas.rules import (
    RuleCreateRequest,
    RuleListResponse,
    RuleResponse,
    RuleStatsResponse,
    RuleUpdateRequest,
    RuleVersionResponse,
)

router = APIRouter(prefix="/api/v1/rules", tags=["rules"])


def _get_service():
    """延迟导入避免循环依赖。"""
    from ...services.rules_service import RulesService
    return RulesService()


# ── 查询 ──────────────────────────────────────────────────

@router.get("", response_model=RuleListResponse)
async def list_rules(
    category: str | None = Query(None, description="按分类过滤"),
    severity: str | None = Query(None, description="按严重程度过滤"),
    enabled_only: bool = Query(False, description="仅显示启用的规则"),
    user: CurrentUser | None = Depends(require_role("admin", "reviewer")),
):
    """列出所有规则。"""
    svc = _get_service()
    rules = await svc.list_rules(category=category, severity=severity, enabled_only=enabled_only)
    return RuleListResponse(total=len(rules), rules=[RuleResponse(**r) for r in rules])


@router.get("/stats", response_model=RuleStatsResponse)
async def get_rule_stats(
    user: CurrentUser | None = Depends(require_role("admin", "reviewer")),
):
    """获取规则统计。"""
    svc = _get_service()
    stats = await svc.get_stats()
    return RuleStatsResponse(
        total=stats.get("total_rules", 0),
        enabled=stats.get("enabled", 0),
        disabled=stats.get("disabled", 0),
        by_severity=stats.get("by_severity", {}),
        by_category=stats.get("by_category", {}),
    )


@router.get("/{rule_id}", response_model=RuleResponse)
async def get_rule(
    rule_id: str,
    user: CurrentUser | None = Depends(require_role("admin", "reviewer")),
):
    """获取单条规则详情。"""
    svc = _get_service()
    rule = svc.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"规则不存在: {rule_id}")
    return RuleResponse(**rule)


@router.get("/{rule_id}/versions", response_model=RuleVersionResponse)
async def get_rule_versions(
    rule_id: str,
    user: CurrentUser | None = Depends(require_role("admin")),
):
    """获取规则版本历史。"""
    svc = _get_service()
    versions = svc.get_versions(rule_id)
    return RuleVersionResponse(rule_id=rule_id, versions=versions)


# ── 创建 ──────────────────────────────────────────────────

@router.post("", response_model=RuleResponse, status_code=201)
async def create_rule(
    body: RuleCreateRequest,
    user: CurrentUser | None = Depends(require_role("admin")),
):
    """创建新规则。"""
    svc = _get_service()
    try:
        rule = await svc.create_rule(body.model_dump())
        return RuleResponse(**rule)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ── 更新 ──────────────────────────────────────────────────

@router.put("/{rule_id}", response_model=RuleResponse)
async def update_rule(
    rule_id: str,
    body: RuleUpdateRequest,
    user: CurrentUser | None = Depends(require_role("admin")),
):
    """更新规则。"""
    svc = _get_service()
    try:
        rule = await svc.update_rule(rule_id, body.model_dump(exclude_unset=True))
        return RuleResponse(**rule)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{rule_id}/toggle")
async def toggle_rule(
    rule_id: str,
    enabled: bool = Query(description="启用或禁用"),
    user: CurrentUser | None = Depends(require_role("admin")),
):
    """启用/禁用规则。"""
    svc = _get_service()
    rule = await svc.toggle_rule(rule_id, enabled)
    if not rule:
        raise HTTPException(status_code=404, detail=f"规则不存在: {rule_id}")
    return {"message": f"规则已{'启用' if enabled else '禁用'}", "rule_id": rule_id}


# ── 删除 ──────────────────────────────────────────────────

@router.delete("/{rule_id}")
async def delete_rule(
    rule_id: str,
    user: CurrentUser | None = Depends(require_role("admin")),
):
    """删除规则。"""
    svc = _get_service()
    if not await svc.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail=f"规则不存在: {rule_id}")
    return {"message": "规则已删除", "rule_id": rule_id}


# ── 热加载 ────────────────────────────────────────────────

@router.post("/reload")
async def reload_rules(
    user: CurrentUser | None = Depends(require_role("admin")),
):
    """重新加载规则（热更新）。"""
    from ...services.rules_service import reload_rules
    count = reload_rules()
    return {"message": f"已重新加载 {count} 条规则", "count": count}
