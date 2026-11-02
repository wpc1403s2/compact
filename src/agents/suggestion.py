"""
修改建议Agent — 多Agent流水线第四步。

职责：
1. 针对风险点和合规问题生成修改建议
2. 生成条款级语义diff（版本对比）
3. 按优先级排序建议
4. 输出Track Changes格式的修改建议
"""

from __future__ import annotations  # ← 启用延迟求值类型注解

import difflib  # ← 标准库 diff，用于生成版本对比
import logging  # ← 导入日志

from langchain_core.messages import HumanMessage, SystemMessage  # ← 消息类型

from ..config import settings, get_llm  # ← 配置 + LLM 工厂
from ..parsers.json_parser import parse_llm_json  # ← JSON 容错解析
from ..pipeline.state import (  # ← 状态模型
    ComplianceFinding,  # ← 合规发现模型
    ComplianceStatus,  # ← 合规状态枚举
    ContractReviewState,  # ← 共享状态
    ReviewStatus,  # ← 审查状态枚举
    RiskFinding,  # ← 风险发现模型
    RiskLevel,  # ← 风险等级枚举
    Suggestion,  # ← 修改建议模型
)
from ..resilience import get_agent_breaker, resilient_call  # ← 弹性机制

logger = logging.getLogger(__name__)  # ← 模块 logger

# ← 修改建议系统提示词（三引号字符串，内部行不可加注释）
SYSTEM_PROMPT = """你是一名资深的合同修改顾问。请根据风险评估和合规检查结果，为合同条款提供具体的修改建议。

要求：
1. 每个修改建议必须包含原始文本和建议修改后的文本
2. 修改建议应该具体、可操作，而不是笼统的方向性建议
3. 保持法律语言的严谨性
4. 优先处理高风险和不合规问题

请输出严格的JSON格式（不要输出其他内容）：

{
  "suggestions": [
    {
      "clause_id": "条款ID",
      "original_text": "原始条款文本",
      "suggested_text": "建议修改后的文本",
      "reason": "修改原因",
      "priority": "high/medium/low"
    }
  ]
}"""  # ← 系统提示词定义结束


class SuggestionAgent:  # ← 定义修改建议 Agent 类
    """修改建议Agent：生成修改建议 + 版本对比 + 优先级排序。"""

    def __init__(self, rag_retriever=None):  # ← 构造函数
        self.llm = None  # ← LLM 惰性初始化
        self._rag = rag_retriever  # ← RAG 检索器
        self._breaker = get_agent_breaker("suggestion")  # ← 专属熔断器

    def _ensure_llm(self):  # ← 惰性初始化 LLM
        if self.llm is None:  # ← 未初始化才创建
            self.llm = get_llm()  # ← 用工厂方法创建

    async def run(self, state: ContractReviewState) -> dict:  # ← 节点入口
        """LangGraph节点函数：生成修改建议。"""
        logger.info("修改建议Agent开始处理 review_id=%s", state.review_id)  # ← 开始日志

        if not state.risk_findings and not state.compliance_findings:  # ← 既无风险也无合规问题
            return {  # ← 直接完成
                "suggestions": [],  # ← 空建议
                "version_diff": "无需修改",  # ← 无修改
                "status": ReviewStatus.COMPLETED,  # ← 完成
            }

        try:
            self._ensure_llm()  # ← 确保 LLM 就绪
            suggestions = await resilient_call(  # ← 带弹性机制调用 LLM 生成建议
                self._generate_suggestions,  # ← 被调用的函数
                state.clauses,  # ← 参数：条款
                state.risk_findings,  # ← 参数：风险发现
                state.compliance_findings,  # ← 参数：合规发现
                max_retries=settings.LLM_MAX_RETRIES,  # ← 重试次数
                timeout_seconds=settings.LLM_TIMEOUT,  # ← 单次超时
                circuit_breaker=self._breaker,  # ← 熔断器
            )

            for missing in state.missing_clauses:  # ← 对每个缺失必要条款
                suggestions.append(  # ← 追加"新增条款"建议
                    Suggestion(
                        clause_id="new",  # ← 新条款标记
                        original_text="（缺失）",  # ← 原文为空
                        suggested_text=f"建议添加{missing}相关条款",  # ← 建议添加
                        reason=f"合同缺少必要的「{missing}」条款",  # ← 原因
                        priority=RiskLevel.HIGH,  # ← 缺失条款定为高优先级
                    )
                )

            suggestions.sort(  # ← 按优先级排序（高 → 低）
                key=lambda s: {  # ← 排序键映射
                    RiskLevel.HIGH: 0,  # ← 高=0（最前）
                    RiskLevel.MEDIUM: 1,  # ← 中=1
                    RiskLevel.LOW: 2,  # ← 低=2
                    RiskLevel.NONE: 3,  # ← 无=3（最后）
                }[s.priority]
            )

            version_diff = self._generate_version_diff(state.clauses, suggestions)  # ← 生成版本对比

            needs_human = state.needs_human_review or any(  # ← 是否需人工审核
                s.priority == RiskLevel.HIGH for s in suggestions  # ← 有高优先级建议即需人工
            )

            logger.info("修改建议生成完成：%d条建议", len(suggestions))  # ← 完成日志

            return {  # ← 返回状态更新
                "suggestions": suggestions,  # ← 建议列表
                "version_diff": version_diff,  # ← 版本对比
                "needs_human_review": needs_human,  # ← 是否需人工审核
                "status": (  # ← 状态
                    ReviewStatus.AWAITING_HUMAN  # ← 需人工→待人工
                    if needs_human
                    else ReviewStatus.COMPLETED  # ← 否则→完成
                ),
            }

        except Exception as e:  # ← 异常兜底
            logger.error("修改建议Agent异常: %s", str(e))  # ← 记录错误
            fallback = self._fallback_suggestions(  # ← 回退建议
                state.risk_findings, state.compliance_findings
            )
            return {  # ← 返回降级结果
                "suggestions": fallback,  # ← 回退建议
                "version_diff": "生成失败",  # ← 对比失败
                "status": ReviewStatus.COMPLETED,  # ← 完成
                "errors": state.errors + [f"修改建议生成失败: {str(e)}"],  # ← 错误信息
            }

    async def _generate_suggestions(  # ← LLM 生成建议
        self,
        clauses: list,
        risk_findings: list[RiskFinding],
        compliance_findings: list[ComplianceFinding],
    ) -> list[Suggestion]:
        """调用大模型生成修改建议。"""
        clause_map = {c.id: c for c in clauses}  # ← 条款 ID → Clause 映射

        context_parts = []  # ← 上下文片段列表

        for rf in risk_findings:  # ← 遍历风险发现
            if rf.risk_level in (RiskLevel.HIGH, RiskLevel.MEDIUM):  # ← 只处理高中风险
                clause = clause_map.get(rf.clause_id)  # ← 找对应条款
                clause_text = f"{clause.title}: {clause.content}" if clause else "未找到对应条款"  # ← 条款文本
                context_parts.append(  # ← 拼风险上下文
                    f"[风险-{rf.risk_level.value}] 条款ID={rf.clause_id}\n"
                    f"条款内容: {clause_text}\n"
                    f"风险: {rf.description}\n"
                    f"依据: {rf.rationale}"
                )

        for cf in compliance_findings:  # ← 遍历合规发现
            if cf.status != ComplianceStatus.COMPLIANT:  # ← 只处理不合规项
                clause = clause_map.get(cf.clause_id)  # ← 找对应条款
                clause_text = f"{clause.title}: {clause.content}" if clause else "未找到对应条款"  # ← 条款文本
                context_parts.append(  # ← 拼合规上下文
                    f"[合规-{cf.status.value}] 条款ID={cf.clause_id}\n"
                    f"条款内容: {clause_text}\n"
                    f"问题: {cf.issue}\n"
                    f"法规: {cf.regulation}"
                )

        if not context_parts:  # ← 无上下文则直接返回空
            return []

        # RAG 上下文增强
        rag_context = ""  # ← RAG 上下文
        if self._rag:  # ← 若配置 RAG
            rag_parts = []  # ← 收集检索结果
            for rf in risk_findings:  # ← 遍历风险发现
                clause = clause_map.get(rf.clause_id)  # ← 找对应条款
                if clause:  # ← 有条款才检索
                    ctx = self._rag.retrieve_for_suggestion(clause.content)  # ← 检索修改案例
                    if ctx:  # ← 有结果才加入
                        rag_parts.append(ctx)
            if rag_parts:  # ← 有结果则拼成上下文
                rag_context = (
                    "\n\n以下是相似条款的修改案例，供参考：\n\n"
                    + "\n\n---\n\n".join(rag_parts)
                    + "\n\n"
                )

        messages = [  # ← 构造消息
            SystemMessage(content=SYSTEM_PROMPT),  # ← 系统消息
            HumanMessage(  # ← 用户消息
                content=(
                    f"{rag_context}"  # ← RAG 上下文
                    "请根据以下风险和合规问题生成修改建议：\n\n"
                    + "\n\n---\n\n".join(context_parts)  # ← 拼接上下文
                )
            ),
        ]
        response = await self.llm.ainvoke(messages)  # ← 调用 LLM
        content = response.content.strip()  # ← 取出文本
        result = parse_llm_json(content)  # ← 解析 JSON
        suggestions = []  # ← 初始化建议列表
        for item in result.get("suggestions", []):  # ← 遍历 LLM suggestions
            try:
                priority = RiskLevel(item.get("priority", "medium"))  # ← 解析优先级
            except ValueError:
                priority = RiskLevel.MEDIUM  # ← 非法优先级回退 medium
            suggestions.append(  # ← 构造 Suggestion
                Suggestion(
                    clause_id=item.get("clause_id", ""),  # ← 条款 ID
                    original_text=item.get("original_text", ""),  # ← 原始文本
                    suggested_text=item.get("suggested_text", ""),  # ← 建议文本
                    reason=item.get("reason", ""),  # ← 原因
                    priority=priority,  # ← 优先级
                )
            )
        return suggestions  # ← 返回建议列表

    def _generate_version_diff(  # ← 生成条款级版本对比
        self, clauses: list, suggestions: list[Suggestion]
    ) -> str:
        """生成条款级的版本对比（语义diff）。"""
        clause_map = {c.id: c for c in clauses}  # ← 条款 ID 映射
        diff_parts = []  # ← diff 片段列表

        for s in suggestions:  # ← 遍历每条建议
            if s.clause_id == "new":  # ← 新增条款建议
                diff_parts.append(f"+ [新增] {s.suggested_text}")  # ← 直接标记为新增
                continue  # ← 跳过后续 diff 处理

            clause = clause_map.get(s.clause_id)  # ← 找对应条款
            title = clause.title if clause else f"条款{s.clause_id}"  # ← 条款标题

            original_lines = s.original_text.splitlines()  # ← 原文按行拆分
            suggested_lines = s.suggested_text.splitlines()  # ← 建议文按行拆分

            diff = difflib.unified_diff(  # ← 用 difflib 生成统一 diff
                original_lines,  # ← 原文行
                suggested_lines,  # ← 建议行
                fromfile=f"{title} (原始)",  # ← 源文件标签
                tofile=f"{title} (建议)",  # ← 目标文件标签
                lineterm="",  # ← 行终止符为空
            )
            diff_text = "\n".join(diff)  # ← 拼接 diff 文本
            if diff_text:  # ← 非空才加入
                diff_parts.append(diff_text)

        return "\n\n".join(diff_parts) if diff_parts else "无修改"  # ← 返回完整 diff 或"无修改"

    def _fallback_suggestions(  # ← LLM 失败时的回退建议
        self,
        risk_findings: list[RiskFinding],
        compliance_findings: list[ComplianceFinding],
    ) -> list[Suggestion]:
        """LLM失败时的回退方案。"""
        suggestions = []  # ← 初始化建议列表
        for rf in risk_findings:  # ← 遍历风险发现
            if rf.risk_level in (RiskLevel.HIGH, RiskLevel.MEDIUM):  # ← 高中风险才处理
                suggestions.append(  # ← 由风险直接生成建议
                    Suggestion(
                        clause_id=rf.clause_id,  # ← 条款 ID
                        original_text="",  # ← 原文为空
                        suggested_text=f"建议修改：{rf.description}",  # ← 建议文本
                        reason=rf.rationale or rf.description,  # ← 原因
                        priority=rf.risk_level,  # ← 优先级=风险等级
                    )
                )
        for cf in compliance_findings:  # ← 遍历合规发现
            if cf.status == ComplianceStatus.NON_COMPLIANT:  # ← 仅不合规项
                suggestions.append(  # ← 由合规问题生成建议
                    Suggestion(
                        clause_id=cf.clause_id,  # ← 条款 ID
                        original_text="",  # ← 原文为空
                        suggested_text=cf.recommendation or f"需修正：{cf.issue}",  # ← 建议文本
                        reason=f"{cf.regulation}: {cf.issue}",  # ← 原因
                        priority=RiskLevel.HIGH,  # ← 高优先级
                    )
                )
        return suggestions  # ← 返回回退建议
