"""向量数据库集成测试 — 验证端到端流程。"""

import json
import tempfile
from pathlib import Path

import pytest

from src.vector.embeddings import EmbeddingModel
from src.vector.store import VectorStore
from src.vector.rag import RAGRetriever


@pytest.fixture
def sample_data():
    """加载示例数据。"""
    data_file = Path(__file__).parent.parent / "data" / "sample_clauses.json"
    if not data_file.exists():
        pytest.skip("sample_clauses.json 不存在")
    with open(data_file, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def embedding_model():
    """创建真实的 EmbeddingModel（需要 sentence-transformers）。"""
    try:
        model = EmbeddingModel()
        # 触发懒加载以检测依赖是否可用
        model.embed_single("test")
        return model
    except (ImportError, ModuleNotFoundError):
        pytest.skip("sentence-transformers 未安装")


@pytest.fixture
def populated_store(embedding_model, sample_data):
    """创建并填充向量存储。"""
    store = VectorStore(embedding_model=embedding_model)
    texts = [f"{c['title']}：{c['content']}" for c in sample_data["clauses"]]
    metadata = [
        {"id": c["id"], "title": c["title"], "category": c["category"]}
        for c in sample_data["clauses"]
    ]
    store.add(texts=texts, metadata=metadata)
    return store


class TestVectorIntegration:
    def test_end_to_end_flow(self, populated_store):
        """测试完整的添加-检索流程。"""
        assert populated_store.size == 20

        results = populated_store.search("甲方付款义务", top_k=3)
        assert len(results) == 3
        assert all("score" in r for r in results)
        assert results[0]["score"] >= results[1]["score"]

    def test_similar_clauses_ranked_higher(self, populated_store):
        """语义相似的条款应排在前面。"""
        results = populated_store.search("保密义务和商业秘密", top_k=5)
        titles = [r["metadata"]["title"] for r in results]
        # 保密相关条款应该排在前面
        assert any("保密" in t for t in titles[:2])

    def test_rag_retriever_integration(self, populated_store):
        """测试 RAG 检索器的端到端流程。"""
        retriever = RAGRetriever(store=populated_store)

        context = retriever.retrieve_for_risk("乙方承担无限赔偿责任")
        assert isinstance(context, str)
        assert len(context) > 0
        assert "参考案例" in context

    def test_save_load_roundtrip(self, embedding_model, populated_store):
        """测试保存-加载的完整往返。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            populated_store.save(tmpdir)

            new_store = VectorStore(embedding_model=embedding_model)
            loaded = new_store.load(tmpdir)
            assert loaded is True
            assert new_store.size == populated_store.size

            # 验证检索结果一致
            original_results = populated_store.search("付款条款", top_k=3)
            loaded_results = new_store.search("付款条款", top_k=3)
            assert len(original_results) == len(loaded_results)
            for orig, loaded in zip(original_results, loaded_results):
                assert orig["metadata"]["id"] == loaded["metadata"]["id"]

    def test_similarity_semantic_not_literal(self, populated_store):
        """验证语义相似度而非字面匹配。"""
        # "购买方支付款项" 和 "付款条款" 语义相似但字面不同
        results = populated_store.search("购买方支付款项", top_k=3)
        assert len(results) > 0
        # 应该匹配到付款相关条款
        categories = [r["metadata"]["category"] for r in results]
        assert "payment" in categories
