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

from __future__ import annotations  # ← 启用延迟求值类型注解

import asyncio  # ← 导入异步 IO
import logging  # ← 导入日志
import os  # ← 导入环境变量

import json
from langchain_core.messages import HumanMessage, SystemMessage  # ← 消息类型
from langchain_openai import ChatOpenAI  # ← LLM 客户端

from src.agents import ClauseExtractionAgent  # ← 引入条款提取 Agent（main 串联用）
from src.config import settings, get_llm  # ← 配置 + LLM 工厂
from src.parsers.json_parser import parse_llm_json  # ← JSON 容错解析
from src.pipeline.state import (  # ← 状态模型
    Clause,  # ← 条款模型
    ContractReviewState,  # ← 共享状态
    ReviewStatus,  # ← 审查状态枚举
    RiskFinding,  # ← 风险发现模型
    RiskLevel,  # ← 风险等级枚举
)
from src.resilience import get_agent_breaker, resilient_call  # ← 弹性机制

logger = logging.getLogger(__name__)  # ← 模块 logger

# ← 风险分析系统提示词（三引号字符串，内部行不可加注释）
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
- none: 无明显风险"""  # ← 系统提示词定义结束


# ── 规则引擎预筛（第一层：确定性检查）──────────────────────────

RISK_RULES = [  # ← 规则引擎预筛规则列表（第一层确定性检查）
    {
        "name": "unlimited_liability",  # ← 规则名：无限责任
        "keywords": ["无限责任", "不限于", "承担全部", "无上限", "不设限"],  # ← 命中关键词
        "risk_level": RiskLevel.HIGH,  # ← 风险等级：高
        "description": "条款包含无限责任相关表述",  # ← 规则描述
    },
    {
        "name": "unilateral_termination",  # ← 规则名：单方解除权
        "keywords": ["单方解除", "单方终止", "有权随时", "任意解除"],  # ← 命中关键词
        "risk_level": RiskLevel.HIGH,  # ← 风险等级：高
        "description": "条款包含单方解除权",  # ← 规则描述
    },
    {
        "name": "unfair_penalty",  # ← 规则名：不合理违约金
        "keywords": ["违约金不低于", "双倍赔偿", "三倍赔偿", "全额赔偿"],  # ← 命中关键词
        "risk_level": RiskLevel.MEDIUM,  # ← 风险等级：中
        "description": "违约金条款可能不合理",  # ← 规则描述
    },
    {
        "name": "auto_renewal",  # ← 规则名：自动续约
        "keywords": ["自动续约", "自动续期", "默认续签"],  # ← 命中关键词
        "risk_level": RiskLevel.MEDIUM,  # ← 风险等级：中
        "description": "包含自动续约条款",  # ← 规则描述
    },
    {
        "name": "vague_terms",  # ← 规则名：模糊条款
        "keywords": ["酌情处理", "另行协商", "视情况而定", "合理范围"],  # ← 命中关键词
        "risk_level": RiskLevel.LOW,  # ← 风险等级：低
        "description": "条款表述模糊，存在歧义风险",  # ← 规则描述
    },
    {
        "name": "exclusive_jurisdiction",  # ← 规则名：排他性管辖
        "keywords": ["仅由.*法院管辖", "排他性管辖"],  # ← 命中关键词（支持正则）
        "risk_level": RiskLevel.MEDIUM,  # ← 风险等级：中
        "description": "管辖权条款可能对一方不利",  # ← 规则描述
    },
]  # ← 规则表定义结束


class RiskIdentificationAgent:  # ← 定义风险识别 Agent 类
    """风险识别Agent：规则预筛 + 大模型深度分析。"""

    def __init__(self, rag_retriever=None):  # ← 构造函数，可注入 RAG 检索器
        self.llm = ChatOpenAI(  # ← 从环境变量构造 LLM
            model_name=os.getenv("CONTRACT_LLM_MODEL", "deepseek-flash"),  # ← 模型名
            api_key=os.getenv("CONTRACT_LLM_API_KEY"),  # ← API Key
            base_url=os.getenv("CONTRACT_LLM_BASE_URL"),  # ← 服务端点
            temperature=0.0,  # ← 温度 0
        )
        self._rag = rag_retriever  # ← 保存 RAG 检索器引用（可选）
        self._breaker = get_agent_breaker("risk_identification")  # ← 本 Agent 专属熔断器

    # def _ensure_llm(self):
    #     if self.llm is None:
    #         self.llm = get_llm()

    async def run(self, state: ContractReviewState) -> dict:  # ← 节点入口
        """LangGraph节点函数：执行风险识别。"""
        logger.info("风险识别Agent开始处理 review_id=%s", state.review_id)  # ← 开始日志

        if not state.clauses:  # ← 若没有条款
            return {  # ← 返回空结果
                "risk_findings": [],  # ← 空风险发现
                "overall_risk_level": RiskLevel.NONE,  # ← 无风险
                "risk_summary": "无条款可供分析",  # ← 提示无条款
            }

        try:
            rule_findings = self._rule_based_scan(state.clauses)  # ← 第一层：规则引擎扫描

            # self._ensure_llm()
            # llm_findings = await resilient_call(
            #     self._llm_analysis,
            #     state.clauses,
            #     max_retries=settings.LLM_MAX_RETRIES,
            #     timeout_seconds=settings.LLM_TIMEOUT,
            #     circuit_breaker=self._breaker,
            # )

            llm_findings = await self._llm_analysis(state.clauses)  # ← 第二层：大模型深度分析

            merged = self._merge_findings(rule_findings, llm_findings)  # ← 合并两引擎结果（去重取最高等级）
            overall = self._calculate_overall_risk(merged)  # ← 计算整体风险等级
            summary = self._generate_summary(merged, overall)  # ← 生成风险总结

            logger.info(  # ← 完成日志
                "风险识别完成：%d个发现，整体风险=%s",
                len(merged), overall.value,
            )
            print(merged)  # ← 调试输出：打印合并结果
            print(overall)  # ← 调试输出：打印整体等级
            print(summary)  # ← 调试输出：打印总结
            return {  # ← 返回状态更新
                "risk_findings": merged,  # ← 合并后的风险发现
                "overall_risk_level": overall,  # ← 整体风险等级
                "risk_summary": summary,  # ← 风险总结
                "needs_human_review": overall == RiskLevel.HIGH,  # ← 高风险则需人工审核
            }

        except Exception as e:  # ← 异常兜底（LLM 失败时）
            logger.error("风险识别Agent异常: %s", str(e))  # ← 记录错误
            rule_findings = self._rule_based_scan(state.clauses)  # ← 仅用规则引擎结果
            overall = self._calculate_overall_risk(rule_findings)  # ← 重新计算整体风险
            return {  # ← 返回降级结果
                "risk_findings": rule_findings,  # ← 规则发现
                "overall_risk_level": overall,  # ← 整体等级
                "risk_summary": f"大模型分析失败，仅展示规则引擎结果。错误: {str(e)}",  # ← 降级说明
                "errors": state.errors + [f"风险识别LLM分析失败: {str(e)}"],  # ← 记录错误信息
            }

    def _rule_based_scan(self, clauses: list[Clause]) -> list[RiskFinding]:  # ← 第一层规则扫描
        """第一层：规则引擎快速扫描。"""
        findings = []  # ← 初始化发现列表
        for clause in clauses:  # ← 遍历每个条款
            for rule in RISK_RULES:  # ← 遍历每条规则
                for keyword in rule["keywords"]:  # ← 遍历规则关键词
                    if keyword in clause.content:  # ← 条款内容命中关键词
                        findings.append(  # ← 生成风险发现
                            RiskFinding(
                                clause_id=clause.id,  # ← 关联条款 ID
                                risk_level=rule["risk_level"],  # ← 规则对应等级
                                risk_type=rule["name"],  # ← 风险类型
                                description=f"{rule['description']}（命中关键词：{keyword}）",  # ← 描述
                                rationale=f"规则引擎检测：条款「{clause.title}」中包含「{keyword}」",  # ← 判断依据
                            )
                        )
                        break  # ← 命中即跳出关键词循环（一条规则最多命中一次）
        return findings  # ← 返回规则发现

    async def _llm_analysis(self, clauses: list[Clause]) -> list[RiskFinding]:  # ← 第二层 LLM 深度分析
        """第三层：大模型深度语义分析。"""
        clauses_text = "\n\n".join(  # ← 拼接所有条款为文本
            f"[条款ID: {c.id}] {c.title}\n{c.content}" for c in clauses
        )

        # RAG 上下文增强
        rag_context = ""  # ← 初始化 RAG 上下文
        if self._rag:  # ← 若配置了 RAG
            context_parts = []  # ← 收集检索结果
            for c in clauses:  # ← 遍历条款
                ctx = self._rag.retrieve_for_risk(c.content)  # ← 检索相似风险案例
                if ctx:  # ← 有结果才加入
                    context_parts.append(ctx)
            if context_parts:  # ← 有检索结果则拼成上下文
                rag_context = (
                    "\n\n以下是相似条款的历史风险案例，供参考：\n\n"
                    + "\n\n---\n\n".join(context_parts)
                    + "\n\n"
                )

        messages = [  # ← 构造消息
            SystemMessage(content=SYSTEM_PROMPT),  # ← 系统消息
            HumanMessage(  # ← 用户消息
                content=(
                    f"{rag_context}"  # ← RAG 上下文
                    f"请分析以下合同条款的风险：\n\n{clauses_text}"  # ← 条款文本
                )
            ),
        ]
        response = await self.llm.ainvoke(messages)  # ← 异步调用 LLM
        content = response.content.strip()  # ← 取出文本
        result = parse_llm_json(content)  # ← 解析为 dict
        findings = []  # ← 初始化发现列表
        for item in result.get("findings", []):  # ← 遍历 LLM 返回的 findings
            try:
                level = RiskLevel(item.get("risk_level", "none"))  # ← 解析风险等级
            except ValueError:
                level = RiskLevel.LOW  # ← 非法等级回退为 LOW
            findings.append(  # ← 构造 RiskFinding
                RiskFinding(
                    clause_id=item.get("clause_id", ""),  # ← 条款 ID
                    risk_level=level,  # ← 风险等级
                    risk_type=item.get("risk_type", "unknown"),  # ← 风险类型
                    description=item.get("description", ""),  # ← 描述
                    buyer_impact=item.get("buyer_impact", ""),  # ← 买方影响
                    seller_impact=item.get("seller_impact", ""),  # ← 卖方影响
                    rationale=item.get("rationale", ""),  # ← 判断依据
                )
            )
        return findings  # ← 返回 LLM 发现

    def _merge_findings(  # ← 合并规则与 LLM 结果
        self, rule_findings: list[RiskFinding], llm_findings: list[RiskFinding]
    ) -> list[RiskFinding]:
        """合并规则引擎和大模型的结果，去重并取最高风险等级。"""
        seen: dict[tuple[str, str], RiskFinding] = {}  # ← 以 (clause_id, risk_type) 为键去重

        for f in rule_findings:  # ← 先写入规则发现
            key = (f.clause_id, f.risk_type)  # ← 生成去重键
            seen[key] = f  # ← 存入字典

        priority = {RiskLevel.HIGH: 3, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 1, RiskLevel.NONE: 0}  # ← 等级优先级
        for f in llm_findings:  # ← 再合并 LLM 发现
            key = (f.clause_id, f.risk_type)  # ← 生成去重键
            if key in seen:  # ← 键已存在
                existing = seen[key]  # ← 取已有项
                if priority[f.risk_level] > priority[existing.risk_level]:  # ← 新等级更高才覆盖
                    seen[key] = f
            else:
                seen[key] = f  # ← 新键直接写入

        return list(seen.values())  # ← 返回去重后的列表

    def _calculate_overall_risk(self, findings: list[RiskFinding]) -> RiskLevel:  # ← 计算整体风险等级（取最高）
        if any(f.risk_level == RiskLevel.HIGH for f in findings):  # ← 有 HIGH
            return RiskLevel.HIGH
        if any(f.risk_level == RiskLevel.MEDIUM for f in findings):  # ← 有 MEDIUM
            return RiskLevel.MEDIUM
        if any(f.risk_level == RiskLevel.LOW for f in findings):  # ← 有 LOW
            return RiskLevel.LOW
        return RiskLevel.NONE  # ← 否则 NONE

    def _generate_summary(self, findings: list[RiskFinding], overall: RiskLevel) -> str:  # ← 生成风险总结文本
        high = sum(1 for f in findings if f.risk_level == RiskLevel.HIGH)  # ← 统计高风险数
        medium = sum(1 for f in findings if f.risk_level == RiskLevel.MEDIUM)  # ← 统计中风险数
        low = sum(1 for f in findings if f.risk_level == RiskLevel.LOW)  # ← 统计低风险数
        return (  # ← 拼接总结
            f"整体风险等级：{overall.value}。"
            f"发现 {high} 个高风险、{medium} 个中风险、{low} 个低风险问题。"
        )


async def main() -> None:  # ← 本地调试串联函数：条款提取 → 风险识别
    """串联第一步与第二步：条款提取 → 风险识别。"""
    state = ContractReviewState()  # ← 初始化空共享状态

    # 1. 条款提取，把返回值写回共享状态
    extraction = await ClauseExtractionAgent().run(state)  # ← 第一步：条款提取
    state.raw_text = extraction.get("raw_text", state.raw_text)  # ← 回写原始文本
    state.clauses = extraction.get("clauses", [])  # ← 回写条款
    state.entities = extraction.get("entities", [])  # ← 回写实体
    state.contract_type = extraction.get("contract_type", state.contract_type)  # ← 回写合同类型
    state.status = extraction.get("status", state.status)  # ← 回写状态



    logger.info(  # ← 提取完成日志
        "条款提取完成：合同类型=%s，条款数=%d，实体数=%d",
        state.contract_type, len(state.clauses), len(state.entities),
    )

    # 2. 风险识别，结果同样写回共享状态
    risk = await RiskIdentificationAgent().run(state)  # ← 第二步：风险识别
    state.risk_findings = risk.get("risk_findings", [])  # ← 回写风险发现
    state.overall_risk_level = risk.get("overall_risk_level", state.overall_risk_level)  # ← 回写整体等级
    state.risk_summary = risk.get("risk_summary", "")  # ← 回写总结
    state.needs_human_review = risk.get("needs_human_review", state.needs_human_review)  # ← 回写人工审核标记
    state.errors = state.errors + risk.get("errors", [])  # ← 追加错误

    print("=" * 20, "风险识别结果", "=" * 20)  # ← 打印结果标题
    print(f"整体风险等级: {state.overall_risk_level.value}")  # ← 打印整体等级
    print(f"风险总结: {state.risk_summary}")  # ← 打印总结
    for finding in state.risk_findings:  # ← 遍历打印每条发现
        print(
            f"- [{finding.risk_level.value}] {finding.risk_type} "
            f"(条款 {finding.clause_id}): {finding.description}"
        )
    print(f"发现总数: {len(state.risk_findings)}")  # ← 打印总数
    # 将 state 中的数据写入文件
    # 如果文件存在先删除
    if os.path.exists("risk_state.json"):
        os.remove("risk_state.json")

    with open("risk_state.json", "w", encoding="utf-8") as f:
        # ← Pydantic v2 用 model_dump 而非 to_dict；mode="json" 会把 datetime/枚举转成可 JSON 序列化的类型
        json.dump(state.model_dump(mode="json"), f, ensure_ascii=False, indent=2)

if __name__ == '__main__':  # ← 脚本入口
    asyncio.run(main())  # ← 异步执行 main
