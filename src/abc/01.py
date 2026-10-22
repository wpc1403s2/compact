import asyncio
import os
import json
from typing import TypedDict, Dict, Any

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from langgraph.graph import StateGraph, START, END
import dotenv

from src.parsers.document_parser import parse_raw_text_to_sections
from src.pipeline.state import Clause, ContractEntity, ReviewStatus, ContractReviewState
from src.pipeline.state import ClauseCategory

dotenv.load_dotenv()
# 1. 定义状态 (State)
# State 是在图的各个节点之间传递的数据结构
class ContractState(TypedDict):
    file_path: str          # 输入的文件路径
    text_content: str       # 读取的文本内容
    extracted_json: Dict[str, Any]  # 最终提取的结构化 JSON 结果

# 2. 定义系统提示词 (从用户需求中提供)
# 2. 定义系统提示词 (从用户需求中提供)
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

# 3. 定义节点函数 (Nodes)

def read_file_node() -> dict:
    """读取文本文件内容的节点"""
    file_path = r"D:\compact\sample-contracts\sample_labor_contract.txt"
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"text_content": content}
    except Exception as e:
        return {"text_content": f"读取文件出错: {str(e)}"}

class ClauseExtractionAgent:
    """条款提取Agent：合同文档解析 + 条款分割 + 实体识别 + 条款分类。"""

    def __init__(self):
        # 初始化大语言模型 从env中获取 API Key
        self.llm = ChatOpenAI(
            model_name=os.getenv("CONTRACT_LLM_MODEL", "deepseek-flash"),
            api_key=os.getenv("CONTRACT_LLM_API_KEY"),
            base_url=os.getenv("CONTRACT_LLM_BASE_URL"),
            temperature=0.0,
        )

    async def run(self) -> dict:
        # 构建 Prompt
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=SYSTEM_PROMPT),
            ("user", "以下是需要提取信息的合同文本：\n\n{text}")
        ])
        # 使用 JsonOutputParser 确保输出被解析为 Python 字典
        parser = JsonOutputParser()
        # 构建执行链
        chain = prompt | self.llm | parser
        text_content=read_file_node()['text_content']
        # 在 async 函数内使用 ainvoke，避免同步调用阻塞事件循环
        result = await chain.ainvoke({"text": text_content})


        clauses = self._parse_clauses(result)
        entities = self._parse_entities(result)

        print(clauses)
        print("="*10)
        print(entities)

        contract_type = result.get("contract_type", "未识别")

        return {
            "raw_text": text_content,
            "clauses": clauses,
            "entities": entities,
            "contract_type": contract_type,
            "status": ReviewStatus.IN_PROGRESS,
        }




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


if __name__ == '__main__':
    agent = ClauseExtractionAgent()
    # run 是协程函数，必须 await，否则只会创建协程对象而不执行
    # asyncio.run(agent.run(ContractReviewState()))
    asyncio.run(agent.run())

