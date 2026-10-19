"""
规则引擎 — 基于YAML/DSL的法律规则检查系统。

设计思路：
1. 规则定义：YAML格式，支持条件组合和优先级
2. 规则执行：按优先级顺序匹配，支持短路评估
3. 规则结果：返回匹配的规则列表和处理建议
4. 可扩展：支持自定义规则加载和热更新
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import yaml

logger = logging.getLogger(__name__)


class RuleOperator(str, Enum):
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    MATCHES = "matches"
    GREATER_THAN = "gt"
    LESS_THAN = "lt"
    EQUALS = "eq"
    IN = "in"
    AND = "and"
    OR = "or"


class RuleSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Condition:
    """规则条件"""
    field: str
    operator: RuleOperator
    value: Any
    sub_conditions: list["Condition"] = field(default_factory=list)

    def evaluate(self, context: dict) -> bool:
        if self.operator == RuleOperator.AND:
            return all(c.evaluate(context) for c in self.sub_conditions)
        if self.operator == RuleOperator.OR:
            return any(c.evaluate(context) for c in self.sub_conditions)

        actual = context.get(self.field, "")
        if isinstance(actual, str):
            actual_lower = actual.lower()
        else:
            actual_lower = actual

        if self.operator == RuleOperator.CONTAINS:
            return self.value in str(actual)
        if self.operator == RuleOperator.NOT_CONTAINS:
            return self.value not in str(actual)
        if self.operator == RuleOperator.MATCHES:
            return bool(re.search(self.value, str(actual)))
        if self.operator == RuleOperator.GREATER_THAN:
            return float(actual) > float(self.value)
        if self.operator == RuleOperator.LESS_THAN:
            return float(actual) < float(self.value)
        if self.operator == RuleOperator.EQUALS:
            return str(actual) == str(self.value)
        if self.operator == RuleOperator.IN:
            return str(actual) in self.value

        return False


@dataclass
class Rule:
    """单条规则"""
    id: str
    name: str
    description: str
    condition: Condition
    severity: RuleSeverity
    category: str
    action: str
    priority: int = 0
    enabled: bool = True
    metadata: dict = field(default_factory=dict)


@dataclass
class RuleResult:
    """规则执行结果"""
    rule_id: str
    rule_name: str
    matched: bool
    severity: RuleSeverity
    message: str
    action: str
    metadata: dict = field(default_factory=dict)


class RuleEngine:
    """
    合同审查规则引擎。

    支持：
    - 规则注册与管理
    - 条件组合（AND/OR）
    - 优先级排序
    - 分类执行
    - 自定义动作
    """

    def __init__(self, rules_dir: Path | None = None):
        self._rules: dict[str, Rule] = {}
        self._actions: dict[str, Callable] = {}
        self._rules_dir = rules_dir
        self._load_rules()

    def register_rule(self, rule: Rule) -> None:
        self._rules[rule.id] = rule
        logger.debug("注册规则: %s (%s)", rule.id, rule.name)

    def register_action(self, name: str, func: Callable) -> None:
        self._actions[name] = func

    def remove_rule(self, rule_id: str) -> None:
        self._rules.pop(rule_id, None)

    def evaluate(
        self,
        context: dict,
        category: str | None = None,
    ) -> list[RuleResult]:
        """
        执行规则引擎评估。

        Args:
            context: 待检查的上下文（通常包含条款内容）
            category: 可选的规则类别过滤
        """
        rules = sorted(self._rules.values(), key=lambda r: -r.priority)

        if category:
            rules = [r for r in rules if r.category == category]

        rules = [r for r in rules if r.enabled]

        results = []
        for rule in rules:
            matched = rule.condition.evaluate(context)
            if matched:
                results.append(
                    RuleResult(
                        rule_id=rule.id,
                        rule_name=rule.name,
                        matched=True,
                        severity=rule.severity,
                        message=rule.description,
                        action=rule.action,
                        metadata=rule.metadata,
                    )
                )
        return results

    def evaluate_clause(self, clause_content: str, clause_title: str = "") -> list[RuleResult]:
        """便捷方法：评估单个条款。"""
        context = {
            "content": clause_content,
            "title": clause_title,
            "full_text": f"{clause_title} {clause_content}",
        }
        return self.evaluate(context)

    def get_rules_by_category(self, category: str) -> list[Rule]:
        return [r for r in self._rules.values() if r.category == category]

    def get_statistics(self) -> dict:
        total = len(self._rules)
        by_severity = {}
        by_category = {}
        for r in self._rules.values():
            by_severity[r.severity.value] = by_severity.get(r.severity.value, 0) + 1
            by_category[r.category] = by_category.get(r.category, 0) + 1
        return {
            "total_rules": total,
            "by_severity": by_severity,
            "by_category": by_category,
        }

    def _load_rules(self) -> None:
        """加载规则：优先从 YAML 文件加载，回退到内置默认规则。"""
        if self._rules_dir and self._rules_dir.exists():
            loaded = self._load_from_yaml(self._rules_dir)
            if loaded:
                logger.info("从 YAML 加载了 %d 条规则", len(loaded))
                return

        self._load_builtin_rules()

    def _load_from_yaml(self, rules_dir: Path) -> list[Rule]:
        """从 YAML 目录加载规则。"""
        rules = []
        for yaml_file in sorted(rules_dir.glob("*.yaml")):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not data or "rules" not in data:
                    continue
                for rule_data in data["rules"]:
                    rule = self._parse_rule(rule_data)
                    if rule:
                        self.register_rule(rule)
                        rules.append(rule)
            except Exception as e:
                logger.warning("加载规则文件失败 %s: %s", yaml_file, e)
        return rules

    def _parse_rule(self, data: dict) -> Rule | None:
        """从 YAML dict 解析单条规则。"""
        try:
            condition = self._parse_condition(data.get("condition", {}))
            return Rule(
                id=data["id"],
                name=data["name"],
                description=data.get("description", ""),
                condition=condition,
                severity=RuleSeverity(data.get("severity", "info")),
                category=data.get("category", "general"),
                action=data.get("action", "flag_attention"),
                priority=data.get("priority", 0),
                enabled=data.get("enabled", True),
                metadata=data.get("metadata", {}),
            )
        except Exception as e:
            logger.warning("解析规则失败 %s: %s", data.get("id", "?"), e)
            return None

    def _parse_condition(self, data: dict) -> Condition:
        """从 YAML dict 解析条件。"""
        op = RuleOperator(data["operator"])
        if op in (RuleOperator.AND, RuleOperator.OR):
            sub_conditions = [
                self._parse_condition(c) for c in data.get("conditions", [])
            ]
            return Condition(field="", operator=op, value=None, sub_conditions=sub_conditions)
        return Condition(
            field=data.get("field", ""),
            operator=op,
            value=data.get("value"),
        )

    def load_from_yaml(self, rules_dir: Path) -> int:
        """热加载：从指定目录重新加载规则。"""
        self._rules.clear()
        self._rules_dir = rules_dir
        loaded = self._load_from_yaml(rules_dir)
        if not loaded:
            self._load_builtin_rules()
        return len(self._rules)

    def _load_builtin_rules(self) -> None:
        """加载内置默认规则（YAML 不可用时的回退）。"""
        default_rules = [
            Rule(
                id="R001", name="无限责任检查",
                description="条款中包含可能导致无限责任的表述",
                condition=Condition(field="", operator=RuleOperator.OR, value=None, sub_conditions=[
                    Condition("content", RuleOperator.CONTAINS, "无限责任"),
                    Condition("content", RuleOperator.CONTAINS, "承担全部损失"),
                    Condition("content", RuleOperator.CONTAINS, "不设上限"),
                    Condition("content", RuleOperator.CONTAINS, "无上限赔偿"),
                ]),
                severity=RuleSeverity.CRITICAL, category="liability",
                action="flag_high_risk", priority=100,
            ),
            Rule(
                id="R002", name="单方解除权检查",
                description="一方拥有不对等的单方解除权利",
                condition=Condition(field="", operator=RuleOperator.OR, value=None, sub_conditions=[
                    Condition("content", RuleOperator.CONTAINS, "单方解除"),
                    Condition("content", RuleOperator.CONTAINS, "单方终止"),
                    Condition("content", RuleOperator.CONTAINS, "有权随时终止"),
                    Condition("content", RuleOperator.CONTAINS, "任意解除"),
                ]),
                severity=RuleSeverity.CRITICAL, category="termination",
                action="flag_high_risk", priority=95,
            ),
            Rule(
                id="R003", name="违约金合理性检查",
                description="违约金条款可能超出法律允许的合理范围",
                condition=Condition(field="", operator=RuleOperator.OR, value=None, sub_conditions=[
                    Condition("content", RuleOperator.MATCHES, r"违约金.*[三3]倍"),
                    Condition("content", RuleOperator.MATCHES, r"赔偿.*全部.*损失"),
                    Condition("content", RuleOperator.CONTAINS, "双倍赔偿"),
                ]),
                severity=RuleSeverity.WARNING, category="penalty",
                action="flag_medium_risk", priority=80,
            ),
            Rule(
                id="R004", name="格式条款无效性检查",
                description="格式条款中可能包含无效条款",
                condition=Condition(field="", operator=RuleOperator.OR, value=None, sub_conditions=[
                    Condition("content", RuleOperator.CONTAINS, "免除己方责任"),
                    Condition("content", RuleOperator.CONTAINS, "排除对方主要权利"),
                    Condition("content", RuleOperator.CONTAINS, "加重对方责任"),
                ]),
                severity=RuleSeverity.CRITICAL, category="validity",
                action="flag_invalid", priority=100,
                metadata={"regulation": "《民法典》第497条"},
            ),
            Rule(
                id="R005", name="知识产权归属检查",
                description="知识产权归属条款可能不明确或不合理",
                condition=Condition(field="", operator=RuleOperator.OR, value=None, sub_conditions=[
                    Condition("content", RuleOperator.CONTAINS, "全部知识产权归"),
                    Condition("content", RuleOperator.MATCHES, r"知识产权.*不可撤销.*转让"),
                    Condition("content", RuleOperator.CONTAINS, "放弃全部知识产权"),
                ]),
                severity=RuleSeverity.WARNING, category="ip",
                action="flag_medium_risk", priority=85,
            ),
            Rule(
                id="R006", name="保密期限检查",
                description="保密义务期限过长或无期限限制",
                condition=Condition(field="", operator=RuleOperator.OR, value=None, sub_conditions=[
                    Condition("content", RuleOperator.CONTAINS, "永久保密"),
                    Condition("content", RuleOperator.CONTAINS, "保密义务不因合同终止而终止"),
                    Condition("content", RuleOperator.MATCHES, r"保密.*[十1][零0]年以上"),
                ]),
                severity=RuleSeverity.WARNING, category="confidentiality",
                action="flag_medium_risk", priority=70,
            ),
            Rule(
                id="R007", name="自动续约检查",
                description="合同包含自动续约条款",
                condition=Condition(field="", operator=RuleOperator.OR, value=None, sub_conditions=[
                    Condition("content", RuleOperator.CONTAINS, "自动续约"),
                    Condition("content", RuleOperator.CONTAINS, "自动续期"),
                    Condition("content", RuleOperator.CONTAINS, "默认续签"),
                ]),
                severity=RuleSeverity.INFO, category="renewal",
                action="flag_attention", priority=50,
            ),
            Rule(
                id="R008", name="模糊条款检查",
                description="条款表述模糊，缺乏确定性",
                condition=Condition(field="", operator=RuleOperator.OR, value=None, sub_conditions=[
                    Condition("content", RuleOperator.CONTAINS, "酌情处理"),
                    Condition("content", RuleOperator.CONTAINS, "另行协商"),
                    Condition("content", RuleOperator.CONTAINS, "视情况而定"),
                    Condition("content", RuleOperator.CONTAINS, "适当的"),
                ]),
                severity=RuleSeverity.INFO, category="clarity",
                action="flag_attention", priority=40,
            ),
        ]
        for rule in default_rules:
            self.register_rule(rule)
        logger.info("已加载 %d 条内置默认规则", len(default_rules))
