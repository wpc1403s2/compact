"""
条款提取Agent — 多Agent流水线第一步。

职责：
1. 解析合同文档 (PDF/DOCX → 纯文本)
2. 按条款切分文本
3. 识别条款类别 (付款、违约、保密等)
4. 提取关键实体 (甲乙方、金额、日期、标的物)
5. 输出结构化条款数据

弹性机制：
- LLM 调用超时和重试次数由配置控制
- LLM 失败时使用规则切分回退
"""

from __future__ import annotations

import logging
import asyncio
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI


from src.parsers.document_parser import DocumentParser, parse_raw_text_to_sections
from src.parsers.json_parser import parse_llm_json
from src.resilience import CircuitBreaker, get_agent_breaker, resilient_call
from src.pipeline.state import (
    Clause,
    ClauseCategory,
    ContractEntity,
    ContractReviewState,
    ReviewStatus,
)
import os
import dotenv
# 加载.env 配置

dotenv.load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个专业的法律合同条款提取专家。你的任务是从合同文本中提取结构化条款信息。

请严格按照以下JSON格式输出（不要输出其他内容）：

{
  "contract_type": "合同类型（如：买卖合同、劳动合同、租赁合同等）",
  "clauses": [
    {
      "title": "条款标题",
      "content": "条款完整内容",
      "category": "条款类别",
      "section_number": "章节编号"
    }
  ],
  "entities": [
    {
      "entity_type": "实体类型",
      "value": "实体值",
      "location": "出现位置"
    }
  ]
}

条款类别(category)只能是以下之一：
- payment: 付款条款
- liability: 责任条款
- confidentiality: 保密条款
- termination: 终止/解除条款
- intellectual_property: 知识产权条款
- dispute_resolution: 争议解决条款
- force_majeure: 不可抗力条款
- warranty: 保证/担保条款
- indemnification: 赔偿条款
- governing_law: 适用法律条款
- other: 其他条款

实体类型(entity_type)包括：
- party_a: 甲方
- party_b: 乙方
- amount: 金额
- date: 日期
- subject: 标的物/服务内容
- duration: 期限
- address: 地址"""


class ClauseExtractionAgent:
    """条款提取Agent：合同文档解析 + 条款分割 + 实体识别 + 条款分类。"""

    def __init__(self):
        self.parser = DocumentParser()
        self.llm = ChatOpenAI(
            model_name=os.getenv("CONTRACT_LLM_MODEL", "deepseek-v4.1-flash"),
            api_key=os.getenv("CONTRACT_LLM_API_KEY"),
            base_url=os.getenv("CONTRACT_LLM_BASE_URL"),
            temperature=0.0,
        )
        self._breaker = get_agent_breaker("clause_extraction")

    # def _ensure_llm(self):
    #     if self.llm is None:
    #         self.llm = get_llm()

    async def run(self, state: ContractReviewState) -> dict:
        """LangGraph节点函数：执行条款提取。"""
        logger.info("条款提取Agent开始处理 review_id=%s", state.review_id)

        # 构建 Prompt
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=SYSTEM_PROMPT),
            ("user", "以下是需要提取信息的合同文本：\n\n{text}")
        ])
        # 使用 JsonOutputParser 确保输出被解析为 Python 字典
        parser = JsonOutputParser()
        # 构建执行链
        chain = prompt | self.llm | parser
        text_content = read_file_node()['text_content']
        # 在 async 函数内使用 ainvoke，避免同步调用阻塞事件循环
        result = await chain.ainvoke({"text": text_content})

        clauses = self._parse_clauses(result)
        entities = self._parse_entities(result)

        print(clauses)
        print("=" * 10)
        print(entities)

        contract_type = result.get("contract_type", "未识别")

        return {
            "raw_text": text_content,
            "clauses": clauses,
            "entities": entities,
            "contract_type": contract_type,
            "status": ReviewStatus.IN_PROGRESS,
        }

    def _prepare_text(self, raw_text: str, sections: list[dict]) -> str:
        if len(raw_text) > 15000:
            return raw_text[:15000] + "\n...(文本已截断)"
        return raw_text

    async def _extract_with_llm(self, text: str) -> dict:
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"请提取以下合同的条款信息：\n\n{text}"),
        ]
        response = await self.llm.ainvoke(messages)
        content = response.content.strip()
        return parse_llm_json(content)

    def _parse_clauses(self, result: dict) -> list[Clause]:
        clauses = []
        for item in result.get("clauses", []):
            try:
                category = ClauseCategory(item.get("category", "other"))
            except ValueError:
                category = ClauseCategory.OTHER
            clauses.append(
                Clause(
                    title=item.get("title", "未命名条款"),
                    content=item.get("content", ""),
                    category=category,
                    section_number=item.get("section_number", ""),
                )
            )
        return clauses

    def _parse_entities(self, result: dict) -> list[ContractEntity]:
        entities = []
        for item in result.get("entities", []):
            entities.append(
                ContractEntity(
                    entity_type=item.get("entity_type", "other"),
                    value=item.get("value", ""),
                    location=item.get("location", ""),
                )
            )
        return entities

    def _fallback_extraction(self, raw_text: str) -> list[Clause]:
        """LLM调用失败时的回退方案：基于规则切分条款。"""
        sections = parse_raw_text_to_sections(raw_text)
        return [
            Clause(
                title=s["title"],
                content=s["content"],
                category=ClauseCategory.OTHER,
            )
            for s in sections
        ]

def read_file_node() -> dict:
    """读取文本文件内容的节点"""
    file_path = r"D:\compact\sample-contracts\sample_labor_contract.txt"
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"text_content": content}
    except Exception as e:
        return {"text_content": f"读取文件出错: {str(e)}"}

if __name__ == '__main__':
    Agent=ClauseExtractionAgent()
    asyncio.run(Agent.run(ContractReviewState()))