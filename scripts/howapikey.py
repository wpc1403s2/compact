import dotenv
import openai
from langchain_openai import ChatOpenAI
import os
if __name__ == '__main__':
    dotenv.load_dotenv()
    llm=ChatOpenAI(
            model_name=os.getenv("CONTRACT_LLM_MODEL", "deepseek-flash"),
            api_key=os.getenv("CONTRACT_LLM_API_KEY"),
            base_url=os.getenv("CONTRACT_LLM_BASE_URL"),
            temperature=0.0,
        )
    result=llm.invoke([{"role":"user","content":"你好"}])
    print(result.content)