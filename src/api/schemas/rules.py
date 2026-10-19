"""法规库管理请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RuleConditionSchema(BaseModel):
    """规则条件。"""
    field: str = ""
    operator: str = "contains"
    value: str | list | None = None
    conditions: list["RuleConditionSchema"] = Field(default_factory=list)


class RuleCreateRequest(BaseModel):
    """创建规则请求。"""
    id: str = Field(description="规则ID，如 R009")
    name: str = Field(description="规则名称")
    description: str = Field(default="", description="规则描述")
    severity: str = Field(default="info", description="severity: critical/warning/info")
    category: str = Field(default="general", description="规则分类")
    action: str = Field(default="flag_attention", description="触发动作")
    priority: int = Field(default=0, description="优先级（越大越先执行）")
    enabled: bool = Field(default=True, description="是否启用")
    condition: RuleConditionSchema = Field(description="规则条件")
    metadata: dict = Field(default_factory=dict, description="附加元数据")


class RuleUpdateRequest(BaseModel):
    """更新规则请求。"""
    name: str | None = None
    description: str | None = None
    severity: str | None = None
    category: str | None = None
    action: str | None = None
    priority: int | None = None
    enabled: bool | None = None
    condition: RuleConditionSchema | None = None
    metadata: dict | None = None


class RuleResponse(BaseModel):
    """规则响应。"""
    id: str
    name: str
    description: str
    severity: str
    category: str
    action: str
    priority: int
    enabled: bool
    condition: dict
    metadata: dict = Field(default_factory=dict)
    version: int = 1
    created_at: str = ""
    updated_at: str = ""


class RuleListResponse(BaseModel):
    """规则列表响应。"""
    total: int
    rules: list[RuleResponse]


class RuleVersionResponse(BaseModel):
    """规则版本历史响应。"""
    rule_id: str
    versions: list[dict]


class RuleStatsResponse(BaseModel):
    """规则统计响应。"""
    total: int
    enabled: int
    disabled: int
    by_severity: dict[str, int]
    by_category: dict[str, int]
