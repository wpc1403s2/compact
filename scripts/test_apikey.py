import openai
from langchain_openai import ChatOpenAI


llm=ChatOpenAI(
                # model="deepseek-v4-pro",
                model="deepseek-chat",
               api_key="sk-cb25918274f94041a507160a56d27f6e",
               base_url="https://api.deepseek.com/v1"
)
result=llm.invoke([{"role":"user","content":"你好"}])
print(result.content)