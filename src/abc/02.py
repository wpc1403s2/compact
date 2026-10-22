from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..config import settings, get_llm
from ..parsers.json_parser import parse_llm_json
from ..pipeline.state import (
    Clause,
    ContractReviewState,
    ReviewStatus,
    RiskFinding,
    RiskLevel,
)
from ..resilience import get_agent_breaker, resilient_call
import os
import dotenv
dotenv.load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一名资深的法律风险评估专家。你的任务是分析合同条款中的潜在风险。

请对每个条款进行风险评估，输出严格的JSON格式（不要输出其他内容）：

{
  "findings": [
    {
      "clause_id": "条款ID",
      "risk_level": "high/medium/low/none",
      "risk_type": "风险类型",
      "description": "风险描述",
      "buyer_impact": "对买方/甲方的影响",
      "seller_impact": "对卖方/乙方的影响",
      "rationale": "判断依据"
    }
  ],
  "overall_risk_level": "high/medium/low/none",
  "risk_summary": "风险总结（50字以内）"
}

常见风险类型包括：
- unlimited_liability: 无限责任
- unilateral_termination: 单方解除权
- unfair_penalty: 不合理违约金
- vague_terms: 模糊条款
- missing_protection: 缺少保护条款
- ip_risk: 知识产权风险
- payment_risk: 付款风险
- confidentiality_risk: 保密风险
- jurisdiction_risk: 管辖权风险
- auto_renewal_risk: 自动续约风险

风险等级判断标准：
- high: 可能导致重大经济损失或法律纠纷
- medium: 存在潜在风险，建议修改
- low: 轻微风险，建议关注
- none: 无明显风险"""

RISK_RULES = [
    {
        "name": "unlimited_liability",
        "keywords": ["无限责任", "不限于", "承担全部", "无上限", "不设限"],
        "risk_level": RiskLevel.HIGH,
        "description": "条款包含无限责任相关表述",
    },
    {
        "name": "unilateral_termination",
        "keywords": ["单方解除", "单方终止", "有权随时", "任意解除"],
        "risk_level": RiskLevel.HIGH,
        "description": "条款包含单方解除权",
    },
    {
        "name": "unfair_penalty",
        "keywords": ["违约金不低于", "双倍赔偿", "三倍赔偿", "全额赔偿"],
        "risk_level": RiskLevel.MEDIUM,
        "description": "违约金条款可能不合理",
    },
    {
        "name": "auto_renewal",
        "keywords": ["自动续约", "自动续期", "默认续签"],
        "risk_level": RiskLevel.MEDIUM,
        "description": "包含自动续约条款",
    },
    {
        "name": "vague_terms",
        "keywords": ["酌情处理", "另行协商", "视情况而定", "合理范围"],
        "risk_level": RiskLevel.LOW,
        "description": "条款表述模糊，存在歧义风险",
    },
    {
        "name": "exclusive_jurisdiction",
        "keywords": ["仅由.*法院管辖", "排他性管辖"],
        "risk_level": RiskLevel.MEDIUM,
        "description": "管辖权条款可能对一方不利",
    },
]

class RiskIdentificationAgent:
    """风险识别Agent：规则预筛 + 大模型深度分析。"""
    def __init__(self):
        self.llm = ChatOpenAI(
            model_name=os.getenv("CONTRACT_LLM_MODEL", "deepseek-flash"),
            api_key=os.getenv("CONTRACT_LLM_API_KEY"),
            base_url=os.getenv("CONTRACT_LLM_BASE_URL"),
            temperature=0.0,
        )

    async def run(self,state: ContractReviewState)->dict:
