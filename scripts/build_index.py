#!/usr/bin/env python3
"""构建 FAISS 向量索引 — 从 sample_clauses.json 加载数据并建立索引。"""

import json
import sys
from pathlib import Path

# 将项目根目录加入 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))



from src.vector.embeddings import EmbeddingModel
from src.vector.store import VectorStore

def main():
    data_file = PROJECT_ROOT / "data" / "sample_clauses.json"
    index_path = PROJECT_ROOT / "data" / "faiss_index"

    if not data_file.exists():
        print(f"错误：数据文件不存在: {data_file}")
        sys.exit(1)

    with open(data_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    clauses = data["clauses"]
    print(f"加载了 {len(clauses)} 条示例条款")

    texts = [f"{c['title']}：{c['content']}" for c in clauses]
    metadata = [
        {"id": c["id"], "title": c["title"], "category": c["category"], "source": c["source"]}
        for c in clauses
    ]

    print("正在加载 Embedding 模型...")
    model = EmbeddingModel()

    print("正在构建向量索引...")
    store = VectorStore(embedding_model=model)
    store.add(texts=texts, metadata=metadata)

    print(f"正在保存索引到 {index_path}...")
    store.save(str(index_path))

    print(f"完成！索引已保存，共 {store.size} 条记录")
    print(f"索引路径: {index_path}")


if __name__ == "__main__":
    main()
