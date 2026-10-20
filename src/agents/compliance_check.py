"""
合规检查Agent — 多Agent流水线第三步。

职责：
1. 基于规则引擎检查法律合规性
2. 与标准合同模板进行条款比对
3. 检测缺失的必要条款
4. 输出合规检查报告

检查维度：
- 《中华人民共和国民法典》合同编
- 《中华人民共和国劳动合同法》
- 行业监管要求
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import settings, get_llm
from ..parsers.json_parser import parse_llm_json
from ..pipeline.state import (
    Clause,
    ClauseCategory,
    ComplianceFinding,
    ComplianceStatus,
    ContractReviewState,
)
from ..resilience import get_agent_breaker, resilient_call

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一名中国法律合规检查专家。请对合同条款进行合规性检查。

检查依据：
1. 《中华人民共和国民法典》合同编
2. 《中华人民共和国劳动合同法》（如适用）
3. 行业监管要求
4. 合同基本要素完整性

请输出严格的JSON格式（不要输出其他内容）：

{
  "findings": [
    {
      "clause_id": "条款ID",
      "status": "compliant/non_compliant/needs_review",
      "regulation": "相关法规条文",
      "issue": "合规问题描述",
      "recommendation": "改进建议"
    }
  ],
  "missing_clauses": ["缺失的必要条款名称列表"],
  "overall_compliance": "compliant/non_compliant/needs_review"
}

合规状态说明：
- compliant: 符合法律法规要求
- non_compliant: 违反法律法规
- needs_review: 需要进一步审查"""


# ── 合规规则定义 ──────────────────────────────────────────────

REQUIRED_CLAUSES = {
    "general": [
        "当事人信息",
        "标的/服务内容",
        "价款/报酬",
        "履行期限",
        "违约责任",
        "争议解决",
    ],
    "labor": [
        "劳动合同期限",
        "工作内容和工作地点",
        "工作时间和休息休假",
        "劳动报酬",
        "社会保险",
        "劳动保护",
    ],
    "sale": [
        "标的物描述",
        "数量",
        "价款",
        "交付方式",
        "质量标准",
        "验收条款",
    ],
}

COMPLIANCE_RULES = [
    {
        "name": "invalid_exclusion",
        "keywords": ["免除己方责任", "排除对方主要权利", "加重对方责任"],
        "regulation": "《民法典》第497条",
        "issue": "格式条款中免除己方责任、排除对方主要权利的条款无效",
    },
    {
        "name": "oral_modification",
        "keywords": ["口头变更有效", "口头修改"],
        "regulation": "《民法典》第490条",
        "issue": "合同变更应采用书面形式，口头变更可能产生争议",
    },
    {
        "name": "excessive_penalty",
        "keywords": ["违约金超过损失的30%", "违约金超过百分之三十"],
        "regulation": "《民法典》第585条",
        "issue": "约定的违约金过分高于造成的损失，当事人可以请求减少",
    },
    {
        "name": "labor_probation",
        "keywords": ["试用期超过6个月", "试用期超过六个月"],
        "regulation": "《劳动合同法》第19条",
        "issue": "劳动合同期限三年以上的，试用期不得超过六个月",
    },
]


class ComplianceCheckAgent:
    """合规检查Agent：规则合规检查 + 大模型合规分析。"""

    def __init__(self, rag_retriever=None):
        self.llm = None
        self._rag = rag_retriever
        self._breaker = get_agent_breaker("compliance_check")

    def _ensure_llm(self):
        if self.llm is None:
            self.llm = get_llm()

    async def run(self, state: ContractReviewState) -> dict:
        """LangGraph节点函数：执行合规检查。"""
        logger.info("合规检查Agent开始处理 review_id=%s", state.review_id)

        if not state.clauses:
            return {
                "compliance_findings": [],
                "overall_compliance": ComplianceStatus.NEEDS_REVIEW,
                "missing_clauses": [],
            }

        try:
            rule_findings = self._rule_based_check(state.clauses)
            missing = self._check_missing_clauses(
                state.clauses, state.contract_type
            )

            self._ensure_llm()
            llm_findings = await resilient_call(
                self._llm_compliance_check,
                state.clauses,
                state.contract_type,
                max_retries=settings.LLM_MAX_RETRIES,
                timeout_seconds=settings.LLM_TIMEOUT,
                circuit_breaker=self._breaker,
            )

            all_findings = rule_findings + llm_findings
            overall = self._calculate_overall_compliance(all_findings, missing)

            logger.info(
                "合规检查完成：%d个发现，%d个缺失条款，整体合规=%s",
                len(all_findings), len(missing), overall.value,
            )

            return {
                "compliance_findings": all_findings,
                "missing_clauses": missing,
                "overall_compliance": overall,
            }

        except Exception as e:
            logger.error("合规检查Agent异常: %s", str(e))
            rule_findings = self._rule_based_check(state.clauses)
            missing = self._check_missing_clauses(state.clauses, state.contract_type)
            return {
                "compliance_findings": rule_findings,
                "missing_clauses": missing,
                "overall_compliance": ComplianceStatus.NEEDS_REVIEW,
                "errors": state.errors + [f"合规检查LLM分析失败: {str(e)}"],
            }

    def _rule_based_check(self, clauses: list[Clause]) -> list[ComplianceFinding]:
        """基于规则引擎的合规检查。"""
        findings = []
        for clause in clauses:
            for rule in COMPLIANCE_RULES:
                for keyword in rule["keywords"]:
                    if keyword in clause.content:
                        findings.append(
                            ComplianceFinding(
                                clause_id=clause.id,
                                status=ComplianceStatus.NON_COMPLIANT,
                                regulation=rule["regulation"],
                                issue=rule["issue"],
                                recommendation=f"建议修改条款「{clause.title}」中的相关表述",
                            )
                        )
                        break
        return findings

    def _check_missing_clauses(
        self, clauses: list[Clause], contract_type: str
    ) -> list[str]:
        """检查是否缺少必要条款。"""
        required = REQUIRED_CLAUSES.get("general", [])

        ct = contract_type.lower()
        if "劳动" in ct or "labor" in ct:
            required = required + REQUIRED_CLAUSES.get("labor", [])
        elif "买卖" in ct or "sale" in ct or "采购" in ct:
            required = required + REQUIRED_CLAUSES.get("sale", [])

        existing_titles = " ".join(c.title + " " + c.content[:50] for c in clauses)
        missing = []

        category_map = {
            "当事人信息": ["甲方", "乙方", "party", "当事人"],
            "标的/服务内容": ["标的", "服务内容", "工作内容", "商品"],
            "价款/报酬": ["价款", "报酬", "费用", "金额", "价格"],
            "履行期限": ["期限", "交付", "完成时间", "交货"],
            "违约责任": ["违约", "责任", "赔偿"],
            "争议解决": ["争议", "仲裁", "诉讼", "管辖"],
        }

        for req in required:
            keywords = category_map.get(req, [req])
            found = any(kw in existing_titles for kw in keywords)
            if not found:
                missing.append(req)

        return missing

    async def _llm_compliance_check(
        self, clauses: list[Clause], contract_type: str
    ) -> list[ComplianceFinding]:
        """大模型合规检查。"""
        clauses_text = "\n\n".join(
            f"[条款ID: {c.id}] {c.title}\n{c.content}" for c in clauses
        )

        # RAG 上下文增强
        rag_context = ""
        if self._rag:
            context_parts = []
            for c in clauses:
                ctx = self._rag.retrieve_for_compliance(c.content)
                if ctx:
                    context_parts.append(ctx)
            if context_parts:
                rag_context = (
                    "\n\n以下是相似条款的历史审查案例，供参考：\n\n"
                    + "\n\n---\n\n".join(context_parts)
                    + "\n\n"
                )

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"合同类型：{contract_type}\n\n"
                    f"{rag_context}"
                    f"请检查以下条款的合规性：\n\n{clauses_text}"
                )
            ),
        ]
        response = await self.llm.ainvoke(messages)
        content = response.content.strip()
        result = parse_llm_json(content)
        findings = []
        for item in result.get("findings", []):
            try:
                status = ComplianceStatus(item.get("status", "needs_review"))
            except ValueError:
                status = ComplianceStatus.NEEDS_REVIEW
            if status != ComplianceStatus.COMPLIANT:
                findings.append(
                    ComplianceFinding(
                        clause_id=item.get("clause_id", ""),
                        status=status,
                        regulation=item.get("regulation", ""),
                        issue=item.get("issue", ""),
                        recommendation=item.get("recommendation", ""),
                    )
                )
        return findings

    def _calculate_overall_compliance(
        self,
        findings: list[ComplianceFinding],
        missing: list[str],
    ) -> ComplianceStatus:
        if any(f.status == ComplianceStatus.NON_COMPLIANT for f in findings):
            return ComplianceStatus.NON_COMPLIANT
        if missing or any(f.status == ComplianceStatus.NEEDS_REVIEW for f in findings):
            return ComplianceStatus.NEEDS_REVIEW
        return ComplianceStatus.COMPLIANT
