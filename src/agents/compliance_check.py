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

from __future__ import annotations  # ← 启用延迟求值类型注解

import logging  # ← 导入日志

from langchain_core.messages import HumanMessage, SystemMessage  # ← 消息类型

from ..config import settings, get_llm  # ← 配置 + LLM 工厂
from ..parsers.json_parser import parse_llm_json  # ← JSON 容错解析
from ..pipeline.state import (  # ← 状态模型
    Clause,  # ← 条款模型
    ClauseCategory,  # ← 条款类别枚举
    ComplianceFinding,  # ← 合规发现模型
    ComplianceStatus,  # ← 合规状态枚举
    ContractReviewState,  # ← 共享状态
)
from ..resilience import get_agent_breaker, resilient_call  # ← 弹性机制

logger = logging.getLogger(__name__)  # ← 模块 logger

# ← 合规检查系统提示词（三引号字符串，内部行不可加注释）
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
- needs_review: 需要进一步审查"""  # ← 系统提示词定义结束


# ── 合规规则定义 ──────────────────────────────────────────────

REQUIRED_CLAUSES = {  # ← 必要条款清单（按合同类型分组）
    "general": [  # ← 通用必要条款
        "当事人信息",  # ← 当事人信息
        "标的/服务内容",  # ← 标的/服务内容
        "价款/报酬",  # ← 价款/报酬
        "履行期限",  # ← 履行期限
        "违约责任",  # ← 违约责任
        "争议解决",  # ← 争议解决
    ],
    "labor": [  # ← 劳动合同必要条款
        "劳动合同期限",  # ← 合同期限
        "工作内容和工作地点",  # ← 工作内容/地点
        "工作时间和休息休假",  # ← 工时/休假
        "劳动报酬",  # ← 劳动报酬
        "社会保险",  # ← 社保
        "劳动保护",  # ← 劳动保护
    ],
    "sale": [  # ← 买卖合同必要条款
        "标的物描述",  # ← 标的物描述
        "数量",  # ← 数量
        "价款",  # ← 价款
        "交付方式",  # ← 交付方式
        "质量标准",  # ← 质量标准
        "验收条款",  # ← 验收条款
    ],
}  # ← 必要条款清单定义结束

COMPLIANCE_RULES = [  # ← 合规规则列表（规则引擎用）
    {
        "name": "invalid_exclusion",  # ← 规则名：无效格式条款
        "keywords": ["免除己方责任", "排除对方主要权利", "加重对方责任"],  # ← 命中关键词
        "regulation": "《民法典》第497条",  # ← 相关法规
        "issue": "格式条款中免除己方责任、排除对方主要权利的条款无效",  # ← 问题描述
    },
    {
        "name": "oral_modification",  # ← 规则名：口头变更
        "keywords": ["口头变更有效", "口头修改"],  # ← 命中关键词
        "regulation": "《民法典》第490条",  # ← 相关法规
        "issue": "合同变更应采用书面形式，口头变更可能产生争议",  # ← 问题描述
    },
    {
        "name": "excessive_penalty",  # ← 规则名：过高违约金
        "keywords": ["违约金超过损失的30%", "违约金超过百分之三十"],  # ← 命中关键词
        "regulation": "《民法典》第585条",  # ← 相关法规
        "issue": "约定的违约金过分高于造成的损失，当事人可以请求减少",  # ← 问题描述
    },
    {
        "name": "labor_probation",  # ← 规则名：试用期超长
        "keywords": ["试用期超过6个月", "试用期超过六个月"],  # ← 命中关键词
        "regulation": "《劳动合同法》第19条",  # ← 相关法规
        "issue": "劳动合同期限三年以上的，试用期不得超过六个月",  # ← 问题描述
    },
]  # ← 合规规则列表定义结束


class ComplianceCheckAgent:  # ← 定义合规检查 Agent 类
    """合规检查Agent：规则合规检查 + 大模型合规分析。"""

    def __init__(self, rag_retriever=None):  # ← 构造函数
        self.llm = None  # ← LLM 延迟初始化（惰性加载）
        self._rag = rag_retriever  # ← RAG 检索器
        self._breaker = get_agent_breaker("compliance_check")  # ← 专属熔断器

    def _ensure_llm(self):  # ← 惰性初始化 LLM
        if self.llm is None:  # ← 未初始化才创建
            self.llm = get_llm()  # ← 用工厂方法创建

    async def run(self, state: ContractReviewState) -> dict:  # ← 节点入口
        """LangGraph节点函数：执行合规检查。"""
        logger.info("合规检查Agent开始处理 review_id=%s", state.review_id)  # ← 开始日志

        if not state.clauses:  # ← 无条款则返回空结果
            return {
                "compliance_findings": [],  # ← 空合规发现
                "overall_compliance": ComplianceStatus.NEEDS_REVIEW,  # ← 待审
                "missing_clauses": [],  # ← 空缺失条款
            }

        try:
            rule_findings = self._rule_based_check(state.clauses)  # ← 第一层：规则合规检查
            missing = self._check_missing_clauses(  # ← 检查缺失条款
                state.clauses, state.contract_type
            )

            self._ensure_llm()  # ← 确保 LLM 已初始化
            llm_findings = await resilient_call(  # ← 带弹性机制调用 LLM（超时/重试/熔断）
                self._llm_compliance_check,  # ← 被调用的函数
                state.clauses,  # ← 参数：条款
                state.contract_type,  # ← 参数：合同类型
                max_retries=settings.LLM_MAX_RETRIES,  # ← 重试次数
                timeout_seconds=settings.LLM_TIMEOUT,  # ← 单次超时
                circuit_breaker=self._breaker,  # ← 熔断器
            )

            all_findings = rule_findings + llm_findings  # ← 合并规则 + LLM 发现
            overall = self._calculate_overall_compliance(all_findings, missing)  # ← 计算整体合规状态

            logger.info(  # ← 完成日志
                "合规检查完成：%d个发现，%d个缺失条款，整体合规=%s",
                len(all_findings), len(missing), overall.value,
            )

            return {  # ← 返回状态更新
                "compliance_findings": all_findings,  # ← 合规发现
                "missing_clauses": missing,  # ← 缺失条款
                "overall_compliance": overall,  # ← 整体合规状态
            }

        except Exception as e:  # ← 异常兜底
            logger.error("合规检查Agent异常: %s", str(e))  # ← 记录错误
            rule_findings = self._rule_based_check(state.clauses)  # ← 仅规则结果
            missing = self._check_missing_clauses(state.clauses, state.contract_type)  # ← 重算缺失条款
            return {  # ← 返回降级结果
                "compliance_findings": rule_findings,  # ← 规则发现
                "missing_clauses": missing,  # ← 缺失条款
                "overall_compliance": ComplianceStatus.NEEDS_REVIEW,  # ← 待审
                "errors": state.errors + [f"合规检查LLM分析失败: {str(e)}"],  # ← 错误信息
            }

    def _rule_based_check(self, clauses: list[Clause]) -> list[ComplianceFinding]:  # ← 规则合规检查
        """基于规则引擎的合规检查。"""
        findings = []  # ← 初始化发现列表
        for clause in clauses:  # ← 遍历条款
            for rule in COMPLIANCE_RULES:  # ← 遍历规则
                for keyword in rule["keywords"]:  # ← 遍历关键词
                    if keyword in clause.content:  # ← 命中关键词
                        findings.append(  # ← 生成合规发现
                            ComplianceFinding(
                                clause_id=clause.id,  # ← 条款 ID
                                status=ComplianceStatus.NON_COMPLIANT,  # ← 状态=不合规
                                regulation=rule["regulation"],  # ← 相关法规
                                issue=rule["issue"],  # ← 问题描述
                                recommendation=f"建议修改条款「{clause.title}」中的相关表述",  # ← 建议
                            )
                        )
                        break  # ← 命中即跳出关键词循环
        return findings  # ← 返回规则发现

    def _check_missing_clauses(  # ← 检测缺失必要条款
        self, clauses: list[Clause], contract_type: str
    ) -> list[str]:
        """检查是否缺少必要条款。"""
        required = REQUIRED_CLAUSES.get("general", [])  # ← 取通用必要条款

        ct = contract_type.lower()  # ← 合同类型转小写
        if "劳动" in ct or "labor" in ct:  # ← 劳动合同
            required = required + REQUIRED_CLAUSES.get("labor", [])  # ← 追加劳动条款
        elif "买卖" in ct or "sale" in ct or "采购" in ct:  # ← 买卖/采购合同
            required = required + REQUIRED_CLAUSES.get("sale", [])  # ← 追加买卖条款

        existing_titles = " ".join(c.title + " " + c.content[:50] for c in clauses)  # ← 拼接现有标题+内容片段
        missing = []  # ← 缺失列表

        category_map = {  # ← 必要条款 → 关键词映射（用于判定"存在"）
            "当事人信息": ["甲方", "乙方", "party", "当事人"],  # ← 当事人信息关键词
            "标的/服务内容": ["标的", "服务内容", "工作内容", "商品"],  # ← 标的/服务内容关键词
            "价款/报酬": ["价款", "报酬", "费用", "金额", "价格"],  # ← 价款/报酬关键词
            "履行期限": ["期限", "交付", "完成时间", "交货"],  # ← 履行期限关键词
            "违约责任": ["违约", "责任", "赔偿"],  # ← 违约责任关键词
            "争议解决": ["争议", "仲裁", "诉讼", "管辖"],  # ← 争议解决关键词
        }

        for req in required:  # ← 遍历必要条款
            keywords = category_map.get(req, [req])  # ← 取对应关键词
            found = any(kw in existing_titles for kw in keywords)  # ← 关键词是否出现
            if not found:  # ← 未出现
                missing.append(req)  # ← 记为缺失

        return missing  # ← 返回缺失列表

    async def _llm_compliance_check(  # ← LLM 合规检查
        self, clauses: list[Clause], contract_type: str
    ) -> list[ComplianceFinding]:
        """大模型合规检查。"""
        clauses_text = "\n\n".join(  # ← 拼接条款文本
            f"[条款ID: {c.id}] {c.title}\n{c.content}" for c in clauses
        )

        # RAG 上下文增强
        rag_context = ""  # ← RAG 上下文
        if self._rag:  # ← 若配置 RAG
            context_parts = []  # ← 收集检索结果
            for c in clauses:  # ← 遍历条款
                ctx = self._rag.retrieve_for_compliance(c.content)  # ← 检索合规案例
                if ctx:  # ← 有结果才加入
                    context_parts.append(ctx)
            if context_parts:  # ← 有结果则拼成上下文
                rag_context = (
                    "\n\n以下是相似条款的历史审查案例，供参考：\n\n"
                    + "\n\n---\n\n".join(context_parts)
                    + "\n\n"
                )

        messages = [  # ← 构造消息
            SystemMessage(content=SYSTEM_PROMPT),  # ← 系统消息
            HumanMessage(  # ← 用户消息
                content=(
                    f"合同类型：{contract_type}\n\n"  # ← 合同类型
                    f"{rag_context}"  # ← RAG 上下文
                    f"请检查以下条款的合规性：\n\n{clauses_text}"  # ← 条款文本
                )
            ),
        ]
        response = await self.llm.ainvoke(messages)  # ← 调用 LLM
        content = response.content.strip()  # ← 取出文本
        result = parse_llm_json(content)  # ← 解析 JSON
        findings = []  # ← 初始化发现列表
        for item in result.get("findings", []):  # ← 遍历 LLM findings
            try:
                status = ComplianceStatus(item.get("status", "needs_review"))  # ← 解析状态
            except ValueError:
                status = ComplianceStatus.NEEDS_REVIEW  # ← 非法状态回退
            if status != ComplianceStatus.COMPLIANT:  # ← 只保留非合规项
                findings.append(  # ← 构造发现
                    ComplianceFinding(
                        clause_id=item.get("clause_id", ""),  # ← 条款 ID
                        status=status,  # ← 状态
                        regulation=item.get("regulation", ""),  # ← 法规
                        issue=item.get("issue", ""),  # ← 问题
                        recommendation=item.get("recommendation", ""),  # ← 建议
                    )
                )
        return findings  # ← 返回 LLM 发现

    def _calculate_overall_compliance(  # ← 计算整体合规状态
        self,
        findings: list[ComplianceFinding],
        missing: list[str],
    ) -> ComplianceStatus:
        if any(f.status == ComplianceStatus.NON_COMPLIANT for f in findings):  # ← 有不合规
            return ComplianceStatus.NON_COMPLIANT  # ← 不合规
        if missing or any(f.status == ComplianceStatus.NEEDS_REVIEW for f in findings):  # ← 有缺失或待审
            return ComplianceStatus.NEEDS_REVIEW  # ← 待审
        return ComplianceStatus.COMPLIANT  # ← 否则合规
