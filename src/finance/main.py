import asyncio
import json
import operator
import aiofiles
from typing import TypedDict, List, Optional, Dict
from typing_extensions import Annotated
from pydantic import BaseModel, Field

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
import asyncio
from docx import Document

import os
import dotenv
dotenv.load_dotenv()


# ==========================================
# 1. 数据结构与状态定义 (Pydantic & TypedDict)
# ==========================================

class AuditRiskPoint(BaseModel):
    original_text: str = Field(description="有风险的合同条款原文")
    risk_type: str = Field(description="风险类型，如：付款节点、客户信用、敞口风险、保函风险等")
    risk_analysis: str = Field(description="具体的财务/信用/敞口风险分析")
    recommendation: str = Field(description="修改建议或谈判底线")

class FinancialAuditResult(BaseModel):
    audit_passed: bool = Field(description="是否通过初步财务审核")
    exposure_amount: Optional[float] = Field(None, description="测算的潜在最大损失敞口金额")
    risk_points: List[AuditRiskPoint] = Field(default_factory=list, description="识别出的风险点列表")
    summary: str = Field(description="财务审核总体结论")

class ContractState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    contract_text: str
    client_rating: str
    contract_amount: float
    business_params: Dict[str, float]  # 动态挂载的业务风控参数
    audit_result: Optional[FinancialAuditResult]

# ==========================================
# 2. 外部工具定义 (Tools)
# ==========================================

@tool
async def calculate_exposure(
    contract_amount: float, 
    margin_rate: float, 
    pre_shipment_collection: float, 
    sinosure_limit: float, 
    compensation_ratio: float
) -> float:
    """
    计算项目的敞口金额 (潜在最大损失敞口)。必须在输出最终报告前调用此工具进行测算。
    计算逻辑依据财务标准敞口公式。
    """
    post_shipment_collection = contract_amount - pre_shipment_collection
    insured_amount = min(sinosure_limit, post_shipment_collection) * compensation_ratio
    exposure = contract_amount * (1 - margin_rate) - pre_shipment_collection - insured_amount
    return max(0.0, float(exposure))

# ==========================================
# 3. 核心节点逻辑 (Nodes)
# ==========================================

# 初始化模型 (请确保环境变量中已配置 OPENAI_API_KEY，或替换为您实际使用的大模型)
llm = ChatOpenAI(
    model=os.getenv("CONTRACT_LLM_MODEL"),
    api_key=os.getenv("CONTRACT_LLM_API_KEY"),
    base_url=os.getenv("CONTRACT_LLM_BASE_URL"),
    temperature=0)
tools = [calculate_exposure]
llm_with_tools = llm.bind_tools(tools)

async def reasoning_node(state: ContractState) -> ContractState:
    """推理节点：分析合同内容，决定是否调用敞口测算工具"""
    SYSTEM_PROMPT = """你是一位拥有多年大型跨国企业经验的资深财务合规与风控专家。

当前项目信息：
- 客户资信等级：{client_rating}
- 合同总金额：{contract_amount}

【系统强制挂载业务参数】（来源于业务线最新风控配置）：
- 边际贡献率 (margin_rate)：{margin_rate}
- 中信保批复限额 (sinosure_limit)：{sinosure_limit}
- 中信保赔付比例 (compensation_ratio)：{compensation_ratio}

评审指令：
1. 阅读合同条款，提取“发货前可收货款”的具体金额。
2. 你必须调用 `calculate_exposure` 工具来测算潜在的敞口金额。
3. 调用工具时，必须严格使用上述【系统强制挂载业务参数】，绝不允许自行假设。"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="messages"),
    ])
    
    chain = prompt | llm_with_tools
    
    # 初始化人类提问
    if not state.get("messages"):
        human_input = f"请评审以下合同条款：\n\n{state['contract_text']}"
        state["messages"] = [HumanMessage(content=human_input)]
        
    response = await chain.ainvoke({
        "client_rating": state["client_rating"],
        "contract_amount": state["contract_amount"],
        "margin_rate": state["business_params"].get("margin_rate", 0.0),
        "sinosure_limit": state["business_params"].get("sinosure_limit", 0.0),
        "compensation_ratio": state["business_params"].get("compensation_ratio", 0.0),
        "messages": state["messages"]
    })
    
    return {"messages": [response]}

async def formatting_node(state: ContractState) -> ContractState:
    """格式化节点：汇总历史消息与工具计算结果，强制输出结构化JSON"""
    structured_llm = llm.with_structured_output(FinancialAuditResult)
    
    formatting_prompt = ChatPromptTemplate.from_messages([
        ("system", "你是一名财务风控专家，请根据之前的对话与敞口计算结果，生成最终的结构化财务审核报告。"),
        MessagesPlaceholder(variable_name="messages"),
    ])
    
    chain = formatting_prompt | structured_llm
    result = await chain.ainvoke({"messages": state["messages"]})
    
    return {"audit_result": result}

# ==========================================
# 4. 图编排 (LangGraph)
# ==========================================

workflow = StateGraph(ContractState)

workflow.add_node("reasoning", reasoning_node)
workflow.add_node("tools", ToolNode(tools))
workflow.add_node("formatting", formatting_node)

workflow.add_edge(START, "reasoning")
# 条件路由：如果 reasoning 返回了 tool_calls，流向 tools，否则流向 formatting
workflow.add_conditional_edges("reasoning", tools_condition, {"tools": "tools", END: "formatting"})
workflow.add_edge("tools", "reasoning")
workflow.add_edge("formatting", END)

financial_agent_app = workflow.compile()

# ==========================================
# 5. 执行层与测试入口
# ==========================================

async def load_business_config(filepath: str = "risk_config.json") -> dict:
    async with aiofiles.open(filepath, mode='r', encoding='utf-8') as f:
        content = await f.read()
        return json.loads(content)

# 读取 D:\compact\sample-contracts\储能系统合同0809.docx 文件
async def _read_docx_sync(filepath:str)->str:
    doc=Document(filepath)
    full_text=[]
    for para in doc.paragraphs:
        full_text.append(para.text)

    return "\n".join(full_text)

async def run_financial_audit():
    # 1. 模拟业务输入
    file_path=r"D:\compact\sample-contracts\储能系统合同0809.docx"
    try:
        test_contract=await asyncio.to_thread(_read_docx_sync,file_path)
        print("读取成功，内容如下")
        print(test_contract[:500])
    except Exception as e:
        print(f"读取文件出错")

    client_rating = "B类"
    contract_amount = 1000000.0

    # 2. 动态加载JSON配置
    global_config = await load_business_config("risk_config.json")
    current_business_params = global_config.get(client_rating, {})
    
    if not current_business_params:
        raise ValueError(f"配置文件中未找到资信等级 {client_rating} 的参数配置。")

    # 3. 初始化状态机
    initial_state = {
        "contract_text": test_contract,
        "client_rating": client_rating,
        "contract_amount": contract_amount,
        "business_params": current_business_params,
        "messages": []
    }

    # 4. 执行 Graph
    print("🚀 开始执行财务智能体审批工作流...")
    result_state = await financial_agent_app.ainvoke(initial_state)
    
    # 5. 打印结构化结果
    print("\n✅ 审批完成，输出结构化报告：")
    audit_result: FinancialAuditResult = result_state["audit_result"]
    print(audit_result.model_dump_json(indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(run_financial_audit())