"""RAGRetriever 单元测试。"""

from unittest.mock import MagicMock

import pytest

from src.vector.rag import RAGRetriever


@pytest.fixture
def mock_store():
    """创建 mock VectorStore。"""
    store = MagicMock()
    store.search.return_value = [
        {
            "text": "甲方应在收到发票后30日内支付货款",
            "metadata": {"id": "c1", "title": "付款条款", "category": "payment"},
            "score": 0.92,
        },
        {
            "text": "逾期付款的，应按日万分之五支付违约金",
            "metadata": {"id": "c2", "title": "违约金条款", "category": "liability"},
            "score": 0.85,
        },
    ]
    return store


@pytest.fixture
def retriever(mock_store):
    return RAGRetriever(store=mock_store)


class TestRAGRetriever:
    def test_retrieve_for_risk(self, retriever, mock_store):
        result = retriever.retrieve_for_risk("甲方承担无限责任")
        assert "参考案例" in result
        mock_store.search.assert_called_once_with("甲方承担无限责任", top_k=3)

    def test_retrieve_for_compliance(self, retriever, mock_store):
        result = retriever.retrieve_for_compliance("试用期为8个月")
        assert "参考案例" in result
        mock_store.search.assert_called_once_with("试用期为8个月", top_k=3)

    def test_retrieve_for_suggestion(self, retriever, mock_store):
        result = retriever.retrieve_for_suggestion("违约金为合同总额的50%")
        assert "参考案例" in result
        mock_store.search.assert_called_once_with("违约金为合同总额的50%", top_k=3)

    def test_empty_results(self, retriever, mock_store):
        mock_store.search.return_value = []
        result = retriever.retrieve_for_risk("任何条款")
        assert result == ""

    def test_format_context_structure(self, retriever):
        results = [
            {
                "text": "条款内容",
                "metadata": {"title": "标题", "category": "payment"},
                "score": 0.9,
            },
        ]
        formatted = retriever._format_context(results)
        assert "标题" in formatted
        assert "条款内容" in formatted
        assert "0.9" in formatted

    def test_format_context_missing_metadata(self, retriever):
        results = [
            {"text": "条款内容", "metadata": {}, "score": 0.9},
        ]
        formatted = retriever._format_context(results)
        assert "未知条款" in formatted
        assert "条款内容" in formatted
