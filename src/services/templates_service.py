"""合同模板管理服务 — 模板 CRUD + 条款比对。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from ..config import settings

logger = logging.getLogger(__name__)

# 模板存储（内存，生产环境应用数据库）
_templates: dict[str, dict] = {}
_templates_path = settings.RULES_DIR / "templates.yaml"


def _load_templates_from_yaml() -> None:
    """从 YAML 加载模板。"""
    if not _templates_path.exists():
        return
    try:
        with open(_templates_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for t in data.get("templates", []):
            _templates[t["id"]] = t
        logger.info("从 YAML 加载了 %d 个模板", len(_templates))
    except Exception as e:
        logger.warning("加载模板失败: %s", e)


def _save_templates_to_yaml() -> None:
    """保存模板到 YAML。"""
    data = {"templates": list(_templates.values())}
    with open(_templates_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    logger.debug("模板已保存到 %s", _templates_path)


class TemplatesService:
    """合同模板管理服务。"""

    def __init__(self) -> None:
        if not _templates:
            _load_templates_from_yaml()
        # 加载内置默认模板
        self._ensure_defaults()

    def _ensure_defaults(self) -> None:
        """确保有内置默认模板。"""
        defaults = [
            {
                "id": "tpl-purchase",
                "name": "标准采购合同",
                "category": "purchase",
                "description": "适用于商品采购的标准合同模板",
                "required_clauses": [
                    "当事人信息", "标的物描述", "数量", "价款",
                    "交付方式", "质量标准", "验收条款", "付款方式",
                    "违约责任", "争议解决", "合同生效",
                ],
                "enabled": True,
            },
            {
                "id": "tpl-labor",
                "name": "劳动合同",
                "category": "labor",
                "description": "适用于企业与员工签订的劳动合同",
                "required_clauses": [
                    "劳动合同期限", "工作内容和工作地点",
                    "工作时间和休息休假", "劳动报酬",
                    "社会保险", "劳动保护", "劳动纪律",
                    "合同解除条件", "违约责任",
                ],
                "enabled": True,
            },
            {
                "id": "tpl-service",
                "name": "技术服务合同",
                "category": "service",
                "description": "适用于技术服务/咨询类合同",
                "required_clauses": [
                    "服务内容", "服务期限", "服务费用",
                    "验收标准", "知识产权归属", "保密条款",
                    "违约责任", "争议解决",
                ],
                "enabled": True,
            },
        ]
        for t in defaults:
            if t["id"] not in _templates:
                _templates[t["id"]] = t

    # ── 查询 ──────────────────────────────────────────────

    def list_templates(self, category: str | None = None) -> list[dict]:
        """列出所有模板。"""
        templates = list(_templates.values())
        if category:
            templates = [t for t in templates if t.get("category") == category]
        return templates

    def get_template(self, template_id: str) -> dict | None:
        """获取单个模板。"""
        return _templates.get(template_id)

    # ── 创建 ──────────────────────────────────────────────

    def create_template(self, data: dict) -> dict:
        """创建新模板。"""
        template_id = data.get("id") or f"tpl-{uuid.uuid4().hex[:8]}"
        if template_id in _templates:
            raise ValueError(f"模板已存在: {template_id}")

        template = {
            "id": template_id,
            "name": data["name"],
            "category": data.get("category", "other"),
            "description": data.get("description", ""),
            "required_clauses": data.get("required_clauses", []),
            "content": data.get("content", ""),
            "enabled": data.get("enabled", True),
            "metadata": data.get("metadata", {}),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        _templates[template_id] = template
        _save_templates_to_yaml()
        return template

    # ── 更新 ──────────────────────────────────────────────

    def update_template(self, template_id: str, data: dict) -> dict:
        """更新模板。"""
        template = _templates.get(template_id)
        if template is None:
            raise ValueError(f"模板不存在: {template_id}")

        for key in ("name", "description", "required_clauses", "content", "enabled", "metadata"):
            if key in data and data[key] is not None:
                template[key] = data[key]
        template["updated_at"] = datetime.now().isoformat()

        _save_templates_to_yaml()
        return template

    # ── 删除 ──────────────────────────────────────────────

    def delete_template(self, template_id: str) -> bool:
        """删除模板。"""
        if template_id not in _templates:
            return False
        del _templates[template_id]
        _save_templates_to_yaml()
        return True

    # ── 条款比对 ──────────────────────────────────────────

    def check_clauses(self, template_id: str, clause_texts: list[str]) -> dict:
        """将条款与模板必要条款进行比对。"""
        template = _templates.get(template_id)
        if template is None:
            raise ValueError(f"模板不存在: {template_id}")

        required = set(template.get("required_clauses", []))
        if not required:
            return {
                "template_id": template_id,
                "template_name": template["name"],
                "matched": [],
                "missing": [],
                "extra": [],
                "score": 1.0,
            }

        # 简单关键词匹配：检查条款文本是否包含必要条款的关键词
        matched = set()
        all_text = " ".join(clause_texts).lower()

        for clause in required:
            # 将必要条款拆分为关键词
            keywords = [kw.strip() for kw in clause.replace("/", " ").replace("、", " ").split() if kw.strip()]
            if any(kw.lower() in all_text for kw in keywords):
                matched.add(clause)

        missing = list(required - matched)
        score = len(matched) / len(required) if required else 1.0

        return {
            "template_id": template_id,
            "template_name": template["name"],
            "matched": sorted(matched),
            "missing": sorted(missing),
            "extra": [],
            "score": round(score, 2),
        }
