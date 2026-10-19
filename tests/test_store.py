"""VectorStore 单元测试。"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.vector.store import VectorStore


@pytest.fixture
def mock_embedding_model():
    """创建 mock EmbeddingModel，返回固定维度的随机向量。"""
    model = MagicMock()
    vec1 = np.array([1.0, 0.0, 0.0])
    vec2 = np.array([0.0, 1.0, 0.0])
    vec3 = np.array([0.7, 0.7, 0.0])
    vec3 = vec3 / np.linalg.norm(vec3)
    all_vecs = [vec1.tolist(), vec2.tolist(), vec3.tolist()]

    model.embed.side_effect = lambda texts: all_vecs[:len(texts)]
    model.embed_single.return_value = vec1.tolist()
    model.dimension = 3
    return model


@pytest.fixture
def store(mock_embedding_model):
    return VectorStore(embedding_model=mock_embedding_model)


class TestVectorStore:
    def test_init_empty(self, store):
        assert store.size == 0

    def test_add_texts(self, store):
        store.add(
            texts=["clause 1", "clause 2", "clause 3"],
            metadata=[
                {"id": "1", "title": "付款条款"},
                {"id": "2", "title": "保密条款"},
                {"id": "3", "title": "违约条款"},
            ],
        )
        assert store.size == 3

    def test_search_returns_results(self, store, mock_embedding_model):
        store.add(
            texts=["clause 1", "clause 2", "clause 3"],
            metadata=[
                {"id": "1", "title": "付款条款"},
                {"id": "2", "title": "保密条款"},
                {"id": "3", "title": "违约条款"},
            ],
        )
        mock_embedding_model.embed_single.return_value = [1.0, 0.0, 0.0]

        results = store.search("甲方付款", top_k=2)
        assert len(results) == 2
        assert "text" in results[0]
        assert "metadata" in results[0]
        assert "score" in results[0]
        assert results[0]["score"] >= results[1]["score"]

    def test_search_empty_store(self, store):
        results = store.search("任何查询")
        assert results == []

    def test_save_and_load(self, store, mock_embedding_model):
        store.add(
            texts=["clause 1", "clause 2"],
            metadata=[{"id": "1"}, {"id": "2"}],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            store.save(tmpdir)

            assert (Path(tmpdir) / "index.faiss").exists()
            assert (Path(tmpdir) / "metadata.json").exists()

            new_store = VectorStore(embedding_model=mock_embedding_model)
            loaded = new_store.load(tmpdir)
            assert loaded is True
            assert new_store.size == 2

    def test_load_nonexistent(self, store):
        loaded = store.load("/nonexistent/path")
        assert loaded is False

    def test_metadata_json_serializable(self, store):
        store.add(
            texts=["test"],
            metadata=[{"id": "1", "title": "测试", "category": "payment"}],
        )
        json_str = json.dumps(store._metadata, ensure_ascii=False)
        assert "测试" in json_str
