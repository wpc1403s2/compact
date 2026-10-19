"""合同模板管理请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TemplateCreateRequest(BaseModel):
    """创建模板请求。"""
    name: str = Field(description="模板名称，如：标准采购合同")
    category: str = Field(description="模板分类：purchase/labor/service/lease/other")
    description: str = Field(default="", description="模板描述")
    required_clauses: list[str] = Field(default_factory=list, description="必要条款列表")
    content: str = Field(default="", description="模板正文内容（纯文本）")
    metadata: dict = Field(default_factory=dict, description="附加元数据")


class TemplateUpdateRequest(BaseModel):
    """更新模板请求。"""
    name: str | None = None
    description: str | None = None
    required_clauses: list[str] | None = None
    content: str | None = None
    enabled: bool | None = None
    metadata: dict | None = None


class TemplateResponse(BaseModel):
    """模板响应。"""
    id: str
    name: str
    category: str
    description: str
    required_clauses: list[str]
    content_preview: str = ""
    enabled: bool = True
    metadata: dict = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class TemplateListResponse(BaseModel):
    """模板列表响应。"""
    total: int
    templates: list[TemplateResponse]


class TemplateCheckRequest(BaseModel):
    """模板比对请求。"""
    template_id: str = Field(description="模板ID")
    clause_texts: list[str] = Field(description="待比对的条款文本列表")


class TemplateCheckResult(BaseModel):
    """模板比对结果。"""
    template_id: str
    template_name: str
    matched: list[str] = Field(default_factory=list, description="匹配的必要条款")
    missing: list[str] = Field(default_factory=list, description="缺失的必要条款")
    extra: list[str] = Field(default_factory=list, description="模板外的额外条款")
    score: float = Field(description="匹配度（0-1）")
