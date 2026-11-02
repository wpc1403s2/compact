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

from __future__ import annotations  # ← 启用延迟求值类型注解，允许前向引用类型

import logging  # ← 导入日志模块，用于记录运行日志
import asyncio  # ← 导入异步 IO，提供 async/await 与 asyncio.run
from pathlib import Path  # ← 导入 Path，用于软路径定位文件

import dotenv  # ← 导入 python-dotenv，用于加载 .env 配置
from docx import Document

dotenv.load_dotenv()  # ← 读取项目根目录 .env 文件，把配置注入环境变量
from langchain_core.messages import HumanMessage, SystemMessage  # ← 导入系统/用户消息类型
from langchain_core.output_parsers import JsonOutputParser  # ← 导入 JSON 输出解析器
from langchain_core.prompts import ChatPromptTemplate  # ← 导入聊天 Prompt 模板
from langchain_openai import ChatOpenAI  # ← 导入 OpenAI 兼容 LLM 客户端

from src.parsers.document_parser import DocumentParser, parse_raw_text_to_sections  # ← 文档解析 + 章节切分
from src.parsers.json_parser import parse_llm_json  # ← LLM JSON 容错解析
from src.resilience import CircuitBreaker, get_agent_breaker, resilient_call  # ← 超时/重试/熔断
from src.pipeline.state import (  # ← 导入共享状态相关模型
    Clause,  # ← 条款模型
    ClauseCategory,  # ← 条款类别枚举
    ContractEntity,  # ← 合同实体模型
    ContractReviewState,  # ← 流水线共享状态
    ReviewStatus,  # ← 审查状态枚举
)
import os  # ← 导入 os，读取环境变量

# 加载.env 配置

logger = logging.getLogger(__name__)  # ← 创建模块级 logger 对象

# ← 定义系统提示词，约定 LLM 输出 JSON 结构（三引号字符串，内部行不可加注释）
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
- pricing: 价格条款
- acceptance: 验收条款
- milestone: 里程碑/交付计划条款
- breach_penalty: 违约责任/违约金条款
- change_control: 变更/签证/补充协议条款
- tax_invoice: 税务/发票条款
- subcontracting: 分包/转包条款
- quantity_delivery: 服务范围条款
- other: 其他条款

实体类型(entity_type)包括：
- party_a: 甲方
- party_b: 乙方
- amount: 金额
- date: 日期
- subject: 标的物/服务内容
- duration: 期限
- address: 地址
- currency: 币种
- tax_rate: 税率
- payment_term: 账期/付款条件条件
- percentage: 比例
- unit: 单位
- acceptance_criteria: 验收标准
- milestone_date: 里程碑日期
- penalty_amount: 违约金金额
- bank_account: 银行账户
- contact_person: 联系人
- approval_role: 审批角色
- contract_status: 合同状态
- jurisdiction: 管辖机构/仲裁地
"""  # ← 系统提示词定义结束


class ClauseExtractionAgent:  # ← 定义条款提取 Agent 类
    """条款提取Agent：合同文档解析 + 条款分割 + 实体识别 + 条款分类。"""

    def __init__(self):  # ← 构造函数，初始化依赖
        self.parser = DocumentParser()  # ← 实例化文档解析器（PDF/DOCX/TXT → 文本）
        self.llm = ChatOpenAI(  # ← 从环境变量构造 LLM 客户端
            model_name=os.getenv("CONTRACT_LLM_MODEL", "deepseek-flash"),  # ← 模型名（默认 deepseek-flash）
            api_key=os.getenv("CONTRACT_LLM_API_KEY"),  # ← API Key
            base_url=os.getenv("CONTRACT_LLM_BASE_URL"),  # ← 服务端点（OpenAI 兼容）
            temperature=0.0,  # ← 温度 0，输出确定性最高
        )
        self._breaker = get_agent_breaker("clause_extraction")  # ← 获取本 Agent 专属熔断器

    # def _ensure_llm(self):
    #     if self.llm is None:
    #         self.llm = get_llm()

    async def run(self, state: ContractReviewState) -> dict:  # ← LangGraph 节点入口，返回部分状态更新
        """LangGraph节点函数：执行条款提取。"""
        logger.info("条款提取Agent开始处理 review_id=%s", state.review_id)  # ← 记录开始日志

        # 构建 Prompt
        prompt = ChatPromptTemplate.from_messages([  # ← 构建聊天 Prompt 模板
            SystemMessage(content=SYSTEM_PROMPT),  # ← 系统角色消息
            ("user", "以下是需要提取信息的合同文本：\n\n{text}"),  # ← 用户消息，{text} 为占位符
        ])
        # 使用 JsonOutputParser 确保输出被解析为 Python 字典
        parser = JsonOutputParser()  # ← 创建 JSON 输出解析器（把 LLM 文本转 dict）
        # 构建执行链
        chain = prompt | self.llm | parser  # ← 用管道符组装链：模板 → LLM → 解析器
        text_content = read_file_node()['text_content']  # ← 调用 read_file_node 读取合同文件内容
        # 在 async 函数内使用 ainvoke，避免同步调用阻塞事件循环
        result = await chain.ainvoke({"text": text_content})  # ← 异步执行链，得到解析后的 dict

        clauses = self._parse_clauses(result)  # ← 把 LLM 结果解析为 Clause 列表
        entities = self._parse_entities(result)  # ← 把 LLM 结果解析为 ContractEntity 列表

        print(clauses)  # ← 调试输出：打印条款
        print("=" * 10)  # ← 调试输出：分隔线
        print(entities)  # ← 调试输出：打印实体

        contract_type = result.get("contract_type", "未识别")  # ← 取出合同类型，缺省"未识别"

        return {  # ← 返回需要写回共享状态的字段
            "raw_text": text_content,  # ← 原始文本
            "clauses": clauses,  # ← 条款列表
            "entities": entities,  # ← 实体列表
            "contract_type": contract_type,  # ← 合同类型
            "status": ReviewStatus.IN_PROGRESS,  # ← 状态置为"进行中"
        }

    def _prepare_text(self, raw_text: str, sections: list[dict]) -> str:  # ← 截断超长文本
        if len(raw_text) > 15000:  # ← 超过 15000 字
            return raw_text[:15000] + "\n...(文本已截断)"  # ← 截断到 15000 字并加提示
        return raw_text  # ← 否则原样返回

    async def _extract_with_llm(self, text: str) -> dict:  # ← 用 LLM 提取（备用方法，run 中未使用）
        messages = [  # ← 构造消息列表
            SystemMessage(content=SYSTEM_PROMPT),  # ← 系统消息
            HumanMessage(content=f"请提取以下合同的条款信息：\n\n{text}"),  # ← 用户消息（带文本）
        ]
        response = await self.llm.ainvoke(messages)  # ← 异步调用 LLM
        content = response.content.strip()  # ← 取出回复文本并去除首尾空白
        return parse_llm_json(content)  # ← 容错解析为 dict

    def _parse_clauses(self, result: dict) -> list[Clause]:  # ← LLM 结果 → Clause 列表
        clauses = []  # ← 初始化结果列表
        for item in result.get("clauses", []):  # ← 遍历 clauses 数组
            try:
                category = ClauseCategory(item.get("category", "other"))  # ← 解析条款类别
            except ValueError:
                category = ClauseCategory.OTHER  # ← 非法类别回退为 other
            clauses.append(  # ← 构造 Clause 对象并加入列表
                Clause(
                    title=item.get("title", "未命名条款"),  # ← 标题
                    content=item.get("content", ""),  # ← 内容
                    category=category,  # ← 类别
                    section_number=item.get("section_number", ""),  # ← 章节编号
                )
            )
        return clauses  # ← 返回条款列表

    def _parse_entities(self, result: dict) -> list[ContractEntity]:  # ← LLM 结果 → 实体列表
        entities = []  # ← 初始化实体列表
        for item in result.get("entities", []):  # ← 遍历 entities 数组
            entities.append(  # ← 构造 ContractEntity 并加入列表
                ContractEntity(
                    entity_type=item.get("entity_type", "other"),  # ← 实体类型
                    value=item.get("value", ""),  # ← 实体值
                    location=item.get("location", ""),  # ← 位置
                )
            )
        return entities  # ← 返回实体列表

    def _fallback_extraction(self, raw_text: str) -> list[Clause]:  # ← LLM 失败时的规则切分回退
        """LLM调用失败时的回退方案：基于规则切分条款。"""
        sections = parse_raw_text_to_sections(raw_text)  # ← 用规则把文本切分为章节
        return [  # ← 返回由章节生成的 Clause 列表
            Clause(
                title=s["title"],  # ← 章节标题
                content=s["content"],  # ← 章节内容
                category=ClauseCategory.OTHER,  # ← 类别统一为 other
            )
            for s in sections  # ← 每个章节生成一个 Clause
        ]


def read_file_node() -> dict:  # ← 读取本地合同文件内容的辅助函数
    """读取文本文件内容的节点"""
    # 软路径：基于本文件定位项目根目录（src/abc/01.py 向上两级），不依赖启动目录
    file_path = Path(__file__).resolve().parents[2] / "sample-contracts" / "储能系统合同0809.docx"  # ← 用软路径定位采购合同样例

    doc = Document(file_path)
    content_list=[]
    for para in doc.paragraphs:
        content_list.append(para.text)
    content = "\n".join(content_list)  # ← 合并段落为纯
    return {"text_content": content}
    # try:
    #     with open(file_path, "r", encoding="utf-8") as f:  # ← 以 UTF-8 打开文件
    #         content = f.read()  # ← 读取全文
    #     return {"text_content": content}  # ← 返回内容字典
    # except Exception as e:
    #     return {"text_content": f"读取文件出错: {str(e)}"}  # ← 读取失败时返回错误信息


if __name__ == '__main__':  # ← 脚本入口
    Agent = ClauseExtractionAgent()  # ← 实例化 Agent
    asyncio.run(Agent.run(ContractReviewState()))  # ← 用 asyncio.run 异步执行 run

