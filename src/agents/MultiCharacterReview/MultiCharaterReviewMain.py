# 导入操作系统相关库，用于处理文件路径和环境变量
import os
# 导入 JSON 库，用于解析大模型返回的结构化审查结果
import json
# 导入类型提示，用于定义强类型的状态字典
from typing import TypedDict, Dict, Any, List

from docx import Document
# 导入 LangGraph 的核心组件：状态图、起始节点、结束节点
from langgraph.graph import StateGraph, START, END
# 导入 LangChain 的 OpenAI 兼容接口，因为 DeepSeek API 兼容 OpenAI 的调用规范
from langchain_openai import ChatOpenAI
# 导入消息组件，用于构建 SystemPrompt 和 UserMessage
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv
load_dotenv()
# ==========================================
# 1. 环境变量与模型配置
# ==========================================
# 从环境变量中获取 DeepSeek 的 API Key
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "your-deepseek-api-key-here")

# 实例化大语言模型，采用 openai 兼容协议调用 deepseek-flash
llm = ChatOpenAI(
    model=os.getenv("CONTRACT_LLM_MODEL"),                         # 指定使用 deepseek型
    api_key=os.getenv("CONTRACT_LLM_API_KEY"),                      # 传入 API 密钥
    base_url=os.getenv("CONTRACT_LLM_BASE_URL"),           # 指定 DeepSeek 的官方 API 基础地址
    temperature=0.1,                               # 调低温度以确保审查结果的确定性和严谨性
    model_kwargs={"response_format": {"type": "json_object"}} # 强制要求模型输出 JSON 格式
)

# ==========================================
# 2. 定义 Agent 工作流的状态机 (State)
# ==========================================
# 定义 LangGraph 在各个节点之间传递的全局状态字典
class ContractReviewState(TypedDict):
    contract_path: str                             # 待审查合同的本地文件路径
    contract_text: str                             # 从本地读取的合同纯文本内容
    legal_result: List[Dict[str, Any]]             # 法务 Agent 审查后输出的 JSON 数组
    finance_result: List[Dict[str, Any]]           # 财务 Agent 审查后输出的 JSON 数组
    pm_result: List[Dict[str, Any]]                # 项目经理 Agent 审查后输出的 JSON 数组
    final_report: str                              # 最终合成的综合审查报告

# ==========================================
# 3. 辅助函数：加载提示词与合同文本
# ==========================================
def load_system_prompt(role: str) -> str:
    """
    根据角色动态加载本地对应的 XML 提示词文件（如 法务.xml）。
    """
    file_name = f"{role}.xml"
    try:
        # 以 utf-8 编码读取本地 XML 文件内容[cite: 2, 3, 4]
        with open(file_name, "r", encoding="utf-8") as f:
            xml_content = f.read()
    except Exception as e:
        raise FileNotFoundError(f"加载提示词文件 {file_name} 失败，请检查文件是否存在。错误信息: {e}")

    # 【关键追加】原 XML 中要求输出 JSON 数组，但 DeepSeek 的 json_object 模式强制要求返回 JSON Object
    # 因此在这里追加一段强制格式约定，将审查数组包裹在 "reviews" 键内。
    json_enforcement = """
    【强制输出格式补充说明】
    由于 API 系统调用限制，请务必返回一个合法的 JSON 对象 (JSON Object)，而不要直接返回 JSON 数组。
    请严格使用以下数据结构：
    {
      "reviews": [
        {
          "check_item": "审查项名称",
          "original_clause": "原文条款",
          "risk_assessment": "风险分析",
          "suggested_revision": "修改建议",
          "status": "通过 / 需确认 / 缺失"
        }
      ]
    }
    """
    return xml_content + "\n\n" + json_enforcement

# ==========================================
# 4. 定义 Graph 节点 (Nodes)
# ==========================================
def read_contract_node(state: ContractReviewState) -> ContractReviewState:
    """
    节点 1：读取本地docx合同文件。
    """
    path = state.get("contract_path", "")
    doc =Document(path)
    full_text=[]
    for pare in doc.paragraphs:
        full_text.append(pare.text)
    contract_text = "\n\n".join(full_text)

    
    return {"contract_text": contract_text}

def run_agent_reviewer(role: str, contract_text: str) -> List[Dict[str, Any]]:
    """
    通用的 Agent 审查执行函数。
    接收角色名与合同文本，加载对应的 XML 提示词，调用 DeepSeek 模型并解析返回的 JSON。
    """
    sys_prompt = load_system_prompt(role)
    
    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=f"请审查以下合同文本：\n\n{contract_text}")
    ]
    
    try:
        response = llm.invoke(messages)
        content = response.content
        parsed_json = json.loads(content)
        # 从强制要求返回的 JSON Object 中提取 reviews 数组
        return parsed_json.get("reviews", [])
    except Exception as e:
        print(f"[{role} Agent] 解析失败: {e}")
        return [{"check_item": "Error", "risk_assessment": str(e)}]

def legal_reviewer_node(state: ContractReviewState) -> ContractReviewState:
    """节点 2A：法务 Agent 审查（并行执行）"""
    print("-> 启动法务 Agent 审查...")
    result = run_agent_reviewer("法务", state["contract_text"])
    return {"legal_result": result}

def finance_reviewer_node(state: ContractReviewState) -> ContractReviewState:
    """节点 2B：财务 Agent 审查（并行执行）"""
    print("-> 启动财务 Agent 审查...")
    result = run_agent_reviewer("财务", state["contract_text"])
    return {"finance_result": result}

def pm_reviewer_node(state: ContractReviewState) -> ContractReviewState:
    """节点 2C：项目经理 Agent 审查（并行执行）"""
    print("-> 启动项目经理 Agent 审查...")
    result = run_agent_reviewer("项目经理", state["contract_text"])
    return {"pm_result": result}

def synthesize_report_node(state: ContractReviewState) -> ContractReviewState:
    """
    节点 3：汇总报告。将三方意见组装为 Markdown。
    """
    print("-> 开始汇总三方评审意见...")
    legal_res = state.get("legal_result", [])
    finance_res = state.get("finance_result", [])
    pm_res = state.get("pm_result", [])
    
    report = "# 重大合同多维度评审综合报告\n\n"
    
    report += "## 1. 财务审查意见\n"
    for item in finance_res:
        report += f"- **{item.get('check_item', '')}** [{item.get('status', '')}]\n"
        report += f"  - 原文条款: {item.get('original_clause', '')}\n"
        report += f"  - 风险: {item.get('risk_assessment', '')}\n"
        report += f"  - 建议: {item.get('suggested_revision', '')}\n\n"
        
    report += "## 2. 法务审查意见\n"
    for item in legal_res:
        report += f"- **{item.get('check_item', '')}** [{item.get('status', '')}]\n"
        report += f"  - 原文条款: {item.get('original_clause', '')}\n"
        report += f"  - 风险: {item.get('risk_assessment', '')}\n"
        report += f"  - 建议: {item.get('suggested_revision', '')}\n\n"

    report += "## 3. 项目经理审查意见\n"
    for item in pm_res:
        report += f"- **{item.get('check_item', '')}** [{item.get('status', '')}]\n"
        report += f"  - 原文条款: {item.get('original_clause', '')}\n"
        report += f"  - 风险: {item.get('risk_assessment', '')}\n"
        report += f"  - 建议: {item.get('suggested_revision', '')}\n\n"
        
    return {"final_report": report}

# ==========================================
# 5. 构建与编译 LangGraph 状态图
# ==========================================
builder = StateGraph(ContractReviewState)

builder.add_node("read_contract", read_contract_node)           
builder.add_node("legal_agent", legal_reviewer_node)            
builder.add_node("finance_agent", finance_reviewer_node)        
builder.add_node("pm_agent", pm_reviewer_node)                  
builder.add_node("synthesize", synthesize_report_node)          

builder.add_edge(START, "read_contract")
builder.add_edge("read_contract", "legal_agent")
builder.add_edge("read_contract", "finance_agent")
builder.add_edge("read_contract", "pm_agent")
builder.add_edge("legal_agent", "synthesize")
builder.add_edge("finance_agent", "synthesize")
builder.add_edge("pm_agent", "synthesize")
builder.add_edge("synthesize", END)

app = builder.compile()

# ==========================================
# 6. 主程序入口调试
# ==========================================
if __name__ == "__main__":
    # 测试文件，假设放置在脚本同级目录
    #
    test_contract_path = r"D:\compact\sample-contracts\储能系统合同0809.docx"
    
    if not os.path.exists(test_contract_path):
        with open(test_contract_path, "w", encoding="utf-8") as f:
            f.write("本储能合同总价为固定价1000万，含增值税。运费自理。如遇材料降价不作调整。货到并验收合格后2年质保。一旦违约罚款合同总额20%。")
            
    initial_state = {
        "contract_path": test_contract_path
    }
    
    print("=" * 40)
    print("🚀 开始启动合同审查多智能体工作流...")
    print("=" * 40)
    
    result_state = app.invoke(initial_state)
    
    print("=" * 40)
    # print("✅ 审查完成！最终综合报告如下：\n")
    print(result_state["final_report"])
    # 保存到本地
    if os.path.exists("final_report.md"):
        os.remove("final_report.md")

    with open("final_report.md", "w", encoding="utf-8") as f:
        f.write(result_state["final_report"])