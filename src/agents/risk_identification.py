"""
风险识别Agent — 多Agent流水线第二步。

职责：
1. 对每个条款进行风险评分（高/中/低）
2. 识别不合理条款（无限责任、单方解除权等）
3. 买卖方偏向分析
4. 综合风险评估

采用规则引擎 + 大模型双引擎架构：
- 规则引擎：处理确定性风险检查（速度快、100%确定）
- 大模型：处理需要语义理解的风险分析
"""

from __future__ import annotations

import asyncio
import logging
import os
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.agents import ClauseExtractionAgent
from src.config import settings, get_llm
from src.parsers.json_parser import parse_llm_json
from src.pipeline.state import (
    Clause,
    ContractReviewState,
    ReviewStatus,
    RiskFinding,
    RiskLevel,
)
from src.resilience import get_agent_breaker, resilient_call

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


# ── 规则引擎预筛（第一层：确定性检查）──────────────────────────

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

    def __init__(self, rag_retriever=None):
        self.llm = ChatOpenAI(
            model_name=os.getenv("CONTRACT_LLM_MODEL", "deepseek-flash"),
            api_key=os.getenv("CONTRACT_LLM_API_KEY"),
            base_url=os.getenv("CONTRACT_LLM_BASE_URL"),
            temperature=0.0,
        )
        self._rag = rag_retriever
        self._breaker = get_agent_breaker("risk_identification")

    # def _ensure_llm(self):
    #     if self.llm is None:
    #         self.llm = get_llm()

    async def run(self, state: ContractReviewState) -> dict:
        """LangGraph节点函数：执行风险识别。"""
        logger.info("风险识别Agent开始处理 review_id=%s", state.review_id)

        if not state.clauses:
            return {
                "risk_findings": [],
                "overall_risk_level": RiskLevel.NONE,
                "risk_summary": "无条款可供分析",
            }

        try:
            rule_findings = self._rule_based_scan(state.clauses)

            # self._ensure_llm()
            llm_findings = await resilient_call(
                self._llm_analysis,
                state.clauses,
                max_retries=settings.LLM_MAX_RETRIES,
                timeout_seconds=settings.LLM_TIMEOUT,
                circuit_breaker=self._breaker,
            )

            llm_findings= await self._llm_analysis(state.clauses)

            merged = self._merge_findings(rule_findings, llm_findings)
            overall = self._calculate_overall_risk(merged)
            summary = self._generate_summary(merged, overall)

            logger.info(
                "风险识别完成：%d个发现，整体风险=%s",
                len(merged), overall.value,
            )
            print(merged)
            print(overall)
            print(summary)
            return {
                "risk_findings": merged,
                "overall_risk_level": overall,
                "risk_summary": summary,
                "needs_human_review": overall == RiskLevel.HIGH,
            }

        except Exception as e:
            logger.error("风险识别Agent异常: %s", str(e))
            rule_findings = self._rule_based_scan(state.clauses)
            overall = self._calculate_overall_risk(rule_findings)
            return {
                "risk_findings": rule_findings,
                "overall_risk_level": overall,
                "risk_summary": f"大模型分析失败，仅展示规则引擎结果。错误: {str(e)}",
                "errors": state.errors + [f"风险识别LLM分析失败: {str(e)}"],
            }

    def _rule_based_scan(self, clauses: list[Clause]) -> list[RiskFinding]:
        """第一层：规则引擎快速扫描。"""
        findings = []
        for clause in clauses:
            for rule in RISK_RULES:
                for keyword in rule["keywords"]:
                    if keyword in clause.content:
                        findings.append(
                            RiskFinding(
                                clause_id=clause.id,
                                risk_level=rule["risk_level"],
                                risk_type=rule["name"],
                                description=f"{rule['description']}（命中关键词：{keyword}）",
                                rationale=f"规则引擎检测：条款「{clause.title}」中包含「{keyword}」",
                            )
                        )
                        break
        return findings

    async def _llm_analysis(self, clauses: list[Clause]) -> list[RiskFinding]:
        """第三层：大模型深度语义分析。"""
        clauses_text = "\n\n".join(
            f"[条款ID: {c.id}] {c.title}\n{c.content}" for c in clauses
        )

        # RAG 上下文增强
        rag_context = ""
        if self._rag:
            context_parts = []
            for c in clauses:
                ctx = self._rag.retrieve_for_risk(c.content)
                if ctx:
                    context_parts.append(ctx)
            if context_parts:
                rag_context = (
                    "\n\n以下是相似条款的历史风险案例，供参考：\n\n"
                    + "\n\n---\n\n".join(context_parts)
                    + "\n\n"
                )

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"{rag_context}"
                    f"请分析以下合同条款的风险：\n\n{clauses_text}"
                )
            ),
        ]
        response = await self.llm.ainvoke(messages)
        content = response.content.strip()
        result = parse_llm_json(content)
        findings = []
        for item in result.get("findings", []):
            try:
                level = RiskLevel(item.get("risk_level", "none"))
            except ValueError:
                level = RiskLevel.LOW
            findings.append(
                RiskFinding(
                    clause_id=item.get("clause_id", ""),
                    risk_level=level,
                    risk_type=item.get("risk_type", "unknown"),
                    description=item.get("description", ""),
                    buyer_impact=item.get("buyer_impact", ""),
                    seller_impact=item.get("seller_impact", ""),
                    rationale=item.get("rationale", ""),
                )
            )
        return findings

    def _merge_findings(
        self, rule_findings: list[RiskFinding], llm_findings: list[RiskFinding]
    ) -> list[RiskFinding]:
        """合并规则引擎和大模型的结果，去重并取最高风险等级。"""
        seen: dict[tuple[str, str], RiskFinding] = {}

        for f in rule_findings:
            key = (f.clause_id, f.risk_type)
            seen[key] = f

        priority = {RiskLevel.HIGH: 3, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 1, RiskLevel.NONE: 0}
        for f in llm_findings:
            key = (f.clause_id, f.risk_type)
            if key in seen:
                existing = seen[key]
                if priority[f.risk_level] > priority[existing.risk_level]:
                    seen[key] = f
            else:
                seen[key] = f

        return list(seen.values())

    def _calculate_overall_risk(self, findings: list[RiskFinding]) -> RiskLevel:
        if any(f.risk_level == RiskLevel.HIGH for f in findings):
            return RiskLevel.HIGH
        if any(f.risk_level == RiskLevel.MEDIUM for f in findings):
            return RiskLevel.MEDIUM
        if any(f.risk_level == RiskLevel.LOW for f in findings):
            return RiskLevel.LOW
        return RiskLevel.NONE

    def _generate_summary(self, findings: list[RiskFinding], overall: RiskLevel) -> str:
        high = sum(1 for f in findings if f.risk_level == RiskLevel.HIGH)
        medium = sum(1 for f in findings if f.risk_level == RiskLevel.MEDIUM)
        low = sum(1 for f in findings if f.risk_level == RiskLevel.LOW)
        return (
            f"整体风险等级：{overall.value}。"
            f"发现 {high} 个高风险、{medium} 个中风险、{low} 个低风险问题。"
        )

async def main() -> None:
    """串联第一步与第二步：条款提取 → 风险识别。"""
    state = ContractReviewState()

    # 1. 条款提取，把返回值写回共享状态
    extraction = await ClauseExtractionAgent().run(state)
    state.raw_text = extraction.get("raw_text", state.raw_text)
    state.clauses = extraction.get("clauses", [])
    state.entities = extraction.get("entities", [])
    state.contract_type = extraction.get("contract_type", state.contract_type)
    state.status = extraction.get("status", state.status)

    logger.info(
        "条款提取完成：合同类型=%s，条款数=%d，实体数=%d",
        state.contract_type, len(state.clauses), len(state.entities),
    )

    # 2. 风险识别，结果同样写回共享状态
    risk = await RiskIdentificationAgent().run(state)
    state.risk_findings = risk.get("risk_findings", [])
    state.overall_risk_level = risk.get("overall_risk_level", state.overall_risk_level)
    state.risk_summary = risk.get("risk_summary", "")
    state.needs_human_review = risk.get("needs_human_review", state.needs_human_review)
    state.errors = state.errors + risk.get("errors", [])

    print("=" * 20, "风险识别结果", "=" * 20)
    print(f"整体风险等级: {state.overall_risk_level.value}")
    print(f"风险总结: {state.risk_summary}")
    for finding in state.risk_findings:
        print(
            f"- [{finding.risk_level.value}] {finding.risk_type} "
            f"(条款 {finding.clause_id}): {finding.description}"
        )
    print(f"发现总数: {len(state.risk_findings)}")


if __name__ == '__main__':
    asyncio.run(main())
