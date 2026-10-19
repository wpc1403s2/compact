"""法规库管理服务 — 规则 CRUD + 版本管理 + 热加载 + 缓存。"""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime
from pathlib import Path

import yaml

from ..cache import get_cache
from ..config import settings
from ..rules.engine import Rule, RuleEngine, RuleOperator, RuleSeverity

logger = logging.getLogger(__name__)

# 规则版本存储（内存，生产环境应用数据库）
_rule_versions: dict[str, list[dict]] = {}
_rule_engine: RuleEngine | None = None

CACHE_KEY_RULES = "rules:list"
CACHE_KEY_STATS = "rules:stats"
CACHE_TTL = 60  # 60 秒缓存


def get_rule_engine() -> RuleEngine:
    """获取全局规则引擎实例（懒加载）。"""
    global _rule_engine
    if _rule_engine is None:
        _rule_engine = RuleEngine(rules_dir=settings.RULES_DIR)
    return _rule_engine


async def _invalidate_rules_cache() -> None:
    """规则变更时清除缓存。"""
    cache = get_cache()
    await cache.clear_prefix("rules:")


def reload_rules() -> int:
    """重新加载规则（热更新）。"""
    global _rule_engine
    _rule_engine = None
    engine = get_rule_engine()
    return len(engine._rules)


class RulesService:
    """法规库管理服务。"""

    def __init__(self) -> None:
        self._engine = get_rule_engine()
        self._yaml_path = settings.RULES_DIR / "risk_rules.yaml"

    # ── 查询 ──────────────────────────────────────────────

    async def list_rules(
        self,
        category: str | None = None,
        severity: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict]:
        """列出所有规则（带缓存）。"""
        cache = get_cache()
        cache_key = f"{CACHE_KEY_RULES}:{category}:{severity}:{enabled_only}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached

        rules = list(self._engine._rules.values())
        if category:
            rules = [r for r in rules if r.category == category]
        if severity:
            rules = [r for r in rules if r.severity.value == severity]
        if enabled_only:
            rules = [r for r in rules if r.enabled]

        result = [self._rule_to_dict(r) for r in rules]
        await cache.set(cache_key, result, ttl=CACHE_TTL)
        return result

    def get_rule(self, rule_id: str) -> dict | None:
        """获取单条规则。"""
        rule = self._engine._rules.get(rule_id)
        if rule is None:
            return None
        return self._rule_to_dict(rule)

    async def get_stats(self) -> dict:
        """获取规则统计（带缓存）。"""
        cache = get_cache()
        cached = await cache.get(CACHE_KEY_STATS)
        if cached is not None:
            return cached

        stats = self._engine.get_statistics()
        rules = list(self._engine._rules.values())
        stats["enabled"] = sum(1 for r in rules if r.enabled)
        stats["disabled"] = sum(1 for r in rules if not r.enabled)
        await cache.set(CACHE_KEY_STATS, stats, ttl=CACHE_TTL)
        return stats

    def get_versions(self, rule_id: str) -> list[dict]:
        """获取规则版本历史。"""
        return _rule_versions.get(rule_id, [])

    # ── 创建 ──────────────────────────────────────────────

    async def create_rule(self, data: dict) -> dict:
        """创建新规则。"""
        rule_id = data["id"]
        if rule_id in self._engine._rules:
            raise ValueError(f"规则已存在: {rule_id}")

        rule = self._parse_rule_from_dict(data)
        self._engine.register_rule(rule)

        self._record_version(rule_id, data, action="create")
        self._save_to_yaml()
        await _invalidate_rules_cache()

        logger.info("规则已创建: %s (%s)", rule_id, rule.name)
        return self._rule_to_dict(rule)

    # ── 更新 ──────────────────────────────────────────────

    async def update_rule(self, rule_id: str, data: dict) -> dict:
        """更新规则。"""
        rule = self._engine._rules.get(rule_id)
        if rule is None:
            raise ValueError(f"规则不存在: {rule_id}")

        if "name" in data and data["name"] is not None:
            rule.name = data["name"]
        if "description" in data and data["description"] is not None:
            rule.description = data["description"]
        if "severity" in data and data["severity"] is not None:
            rule.severity = RuleSeverity(data["severity"])
        if "category" in data and data["category"] is not None:
            rule.category = data["category"]
        if "action" in data and data["action"] is not None:
            rule.action = data["action"]
        if "priority" in data and data["priority"] is not None:
            rule.priority = data["priority"]
        if "enabled" in data and data["enabled"] is not None:
            rule.enabled = data["enabled"]
        if "condition" in data and data["condition"] is not None:
            rule.condition = self._parse_condition(data["condition"])
        if "metadata" in data and data["metadata"] is not None:
            rule.metadata = data["metadata"]

        self._record_version(rule_id, data, action="update")
        self._save_to_yaml()
        await _invalidate_rules_cache()

        logger.info("规则已更新: %s", rule_id)
        return self._rule_to_dict(rule)

    # ── 删除 ──────────────────────────────────────────────

    async def delete_rule(self, rule_id: str) -> bool:
        """删除规则。"""
        if rule_id not in self._engine._rules:
            return False

        self._engine.remove_rule(rule_id)
        self._record_version(rule_id, {}, action="delete")
        self._save_to_yaml()
        await _invalidate_rules_cache()

        logger.info("规则已删除: %s", rule_id)
        return True

    # ── 启用/禁用 ──────────────────────────────────────────

    async def toggle_rule(self, rule_id: str, enabled: bool) -> dict | None:
        """启用/禁用规则。"""
        rule = self._engine._rules.get(rule_id)
        if rule is None:
            return None

        rule.enabled = enabled
        self._record_version(rule_id, {"enabled": enabled}, action="toggle")
        self._save_to_yaml()
        await _invalidate_rules_cache()

        logger.info("规则 %s 已%s", rule_id, "启用" if enabled else "禁用")
        return self._rule_to_dict(rule)

    # ── 内部方法 ──────────────────────────────────────────

    def _rule_to_dict(self, rule: Rule) -> dict:
        """Rule 对象转字典。"""
        return {
            "id": rule.id,
            "name": rule.name,
            "description": rule.description,
            "severity": rule.severity.value,
            "category": rule.category,
            "action": rule.action,
            "priority": rule.priority,
            "enabled": rule.enabled,
            "condition": self._condition_to_dict(rule.condition),
            "metadata": rule.metadata,
        }

    def _condition_to_dict(self, condition) -> dict:
        """Condition 对象转字典。"""
        if condition.sub_conditions:
            return {
                "operator": condition.operator.value,
                "conditions": [self._condition_to_dict(c) for c in condition.sub_conditions],
            }
        return {
            "field": condition.field,
            "operator": condition.operator.value,
            "value": condition.value,
        }

    def _parse_rule_from_dict(self, data: dict) -> Rule:
        """从字典解析规则。"""
        return Rule(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            condition=self._parse_condition(data.get("condition", {})),
            severity=RuleSeverity(data.get("severity", "info")),
            category=data.get("category", "general"),
            action=data.get("action", "flag_attention"),
            priority=data.get("priority", 0),
            enabled=data.get("enabled", True),
            metadata=data.get("metadata", {}),
        )

    def _parse_condition(self, data: dict):
        """从字典解析条件。"""
        from ..rules.engine import Condition
        op = RuleOperator(data.get("operator", "contains"))
        if op in (RuleOperator.AND, RuleOperator.OR):
            sub = [self._parse_condition(c) for c in data.get("conditions", [])]
            return Condition(field="", operator=op, value=None, sub_conditions=sub)
        return Condition(
            field=data.get("field", ""),
            operator=op,
            value=data.get("value"),
        )

    def _record_version(self, rule_id: str, data: dict, action: str) -> None:
        """记录规则版本。"""
        if rule_id not in _rule_versions:
            _rule_versions[rule_id] = []

        versions = _rule_versions[rule_id]
        version_num = len(versions) + 1

        versions.append({
            "version": version_num,
            "action": action,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        })

    def _save_to_yaml(self) -> None:
        """将当前规则保存到 YAML 文件。"""
        rules_data = []
        for rule in self._engine._rules.values():
            rules_data.append({
                "id": rule.id,
                "name": rule.name,
                "description": rule.description,
                "severity": rule.severity.value,
                "category": rule.category,
                "action": rule.action,
                "priority": rule.priority,
                "enabled": rule.enabled,
                "condition": self._condition_to_dict(rule.condition),
                "metadata": rule.metadata if rule.metadata else {},
            })

        # 读取现有 YAML 保留其他部分
        existing = {}
        if self._yaml_path.exists():
            with open(self._yaml_path, "r", encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}

        existing["rules"] = rules_data

        with open(self._yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        logger.info("规则已保存到 %s (%d 条)", self._yaml_path, len(rules_data))
