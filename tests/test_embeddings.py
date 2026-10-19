"""EmbeddingModel 单元测试。"""

import sys
import numpy as np
import pytest
from unittest.mock import patch, MagicMock
from src.vector.embeddings import EmbeddingModel


@pytest.fixture(autouse=True)
def mock_sentence_transformers():
    """注入 mock sentence_transformers 模块到 sys.modules。"""
    mock_st = MagicMock()
    mock_st_module = MagicMock()
    mock_st.SentenceTransformer = mock_st_module
    with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
        yield mock_st_module


class TestEmbeddingModel:
    def test_init_with_default_model(self):
        model = EmbeddingModel()
        assert model._model_name == "shibing624/text2vec-base-chinese"
        assert model._model is None  # lazy load

    def test_init_with_custom_model(self):
        model = EmbeddingModel(model_name="custom/model")
        assert model._model_name == "custom/model"

    def test_embed_returns_vectors(self, mock_sentence_transformers):
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.1] * 768, [0.2] * 768])
        mock_sentence_transformers.return_value = mock_model

        model = EmbeddingModel()
        result = model.embed(["text1", "text2"])

        assert len(result) == 2
        assert len(result[0]) == 768
        mock_model.encode.assert_called_once_with(["text1", "text2"], normalize_embeddings=True)

    def test_embed_single(self, mock_sentence_transformers):
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.1] * 768])
        mock_sentence_transformers.return_value = mock_model

        model = EmbeddingModel()
        result = model.embed_single("hello")

        assert len(result) == 768

    def test_lazy_loading(self, mock_sentence_transformers):
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.1] * 768])
        mock_sentence_transformers.return_value = mock_model

        model = EmbeddingModel()
        assert model._model is None

        model.embed(["test"])
        assert model._model is not None
        mock_sentence_transformers.assert_called_once_with("shibing624/text2vec-base-chinese")

    def test_missing_sentence_transformers(self):
        """模拟 sentence_transformers 未安装时的错误处理。"""
        with patch.dict(sys.modules, {"sentence_transformers": None}):
            model = EmbeddingModel()
            with pytest.raises(ImportError, match="sentence-transformers"):
                model.embed(["test"])

    def test_dimension(self, mock_sentence_transformers):
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 768
        mock_sentence_transformers.return_value = mock_model

        model = EmbeddingModel()
        assert model.dimension == 768
