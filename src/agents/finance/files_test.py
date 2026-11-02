import asyncio
from docx import Document

def _read_docx_sync(filepath: str) -> str:
    doc = Document(filepath)
    full_text = []
    for para in doc.paragraphs:
        full_text.append(para.text)
    return "\n".join(full_text)

async def run_financial_audit():
    file_path = r"/sample-contracts/储能系统合同0809.docx"
    try:
        test_contract = await asyncio.to_thread(_read_docx_sync, file_path)
        print("读取成功，内容如下")
        print(test_contract[:500])
    except Exception as e:
        print(f"读取文件出错: {e}")
        return

if __name__ == '__main__':
    asyncio.run(run_financial_audit())