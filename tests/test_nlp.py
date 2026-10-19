"""法律NLP模块单元测试 — 无需LLM API Key即可运行。"""

from unittest.mock import patch, MagicMock

from src.nlp.legal_nlp import (
    classify_clause,
    extract_entities,
    text_similarity,
    find_similar_clauses,
    clean_legal_text,
    split_sentences,
)


class TestClauseClassification:
    def test_payment_clause(self):
        category, confidence = classify_clause("甲方应在收到发票后30日内支付货款。")
        assert category == "payment"
        assert confidence > 0

    def test_confidentiality_clause(self):
        category, _ = classify_clause("双方应对商业秘密承担保密义务。")
        assert category == "confidentiality"

    def test_dispute_clause(self):
        category, _ = classify_clause("争议由北京仲裁委员会仲裁解决。")
        assert category == "dispute_resolution"

    def test_unknown_clause(self):
        category, _ = classify_clause("今天天气很好。")
        assert category == "other"


class TestEntityExtraction:
    def test_extract_party_a(self):
        entities = extract_entities("甲方：北京科技有限公司")
        types = [e.label for e in entities]
        assert "party_a" in types

    def test_extract_amount(self):
        entities = extract_entities("合同金额为人民币500,000元整。")
        types = [e.label for e in entities]
        assert "amount" in types

    def test_extract_date(self):
        entities = extract_entities("签订日期：2026年4月1日")
        types = [e.label for e in entities]
        assert "date" in types


class TestTextSimilarity:
    def test_identical_texts(self):
        assert text_similarity("你好世界", "你好世界") == 1.0

    def test_different_texts(self):
        score = text_similarity("合同付款条款", "天气预报信息")
        assert score < 0.5

    def test_similar_texts(self):
        score = text_similarity(
            "甲方应在30日内支付货款",
            "甲方应在30个工作日内支付货款",
        )
        assert score > 0.7

    def test_empty_text(self):
        assert text_similarity("", "任何文本") == 0.0


class TestFindSimilarClauses:
    def test_find_similar(self):
        target = "甲方应支付违约金"
        candidates = ["乙方应支付违约金", "天气很好", "甲方应支付赔偿金"]
        results = find_similar_clauses(target, candidates, threshold=0.5)
        assert len(results) > 0
        assert results[0][0] in [0, 2]


class TestTextPreprocessing:
    def test_clean_text(self):
        result = clean_legal_text("  你好   世界  ")
        assert result == "你好 世界"

    def test_split_sentences(self):
        sentences = split_sentences("第一句话。第二句话！第三句话？")
        assert len(sentences) == 3


class TestTextSimilarityFAISS:
    """测试 FAISS 模式的语义相似度。"""

    @patch("src.nlp.legal_nlp._get_embedding_model")
    def test_semantically_similar_texts(self, mock_get_model):
        """语义相似但字面不同的文本应有较高分数。"""
        mock_model = MagicMock()
        # 模拟语义相似的向量（高余弦相似度）
        mock_model.embed.return_value = [
            [0.95, 0.05, 0.0],
            [0.93, 0.07, 0.0],
        ]
        mock_get_model.return_value = mock_model

        score = text_similarity("买方付款", "购买方支付款项")
        assert score > 0.8

    @patch("src.nlp.legal_nlp._get_embedding_model")
    def test_fallback_to_sequence_matcher(self, mock_get_model):
        """EmbeddingModel 失败时应回退到 SequenceMatcher。"""
        mock_get_model.side_effect = ImportError("no module")

        score = text_similarity("你好世界", "你好世界")
        assert score == 1.0

    @patch("src.nlp.legal_nlp._get_embedding_model")
    def test_find_similar_clauses_faiss(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.embed.return_value = [
            [1.0, 0.0],  # target
            [0.9, 0.1],  # candidate 0 — similar
            [0.1, 0.9],  # candidate 1 — different
            [0.8, 0.2],  # candidate 2 — somewhat similar
        ]
        mock_get_model.return_value = mock_model

        results = find_similar_clauses("付款条款", ["支付条款", "保密义务", "付款约定"], threshold=0.5)
        assert len(results) >= 2
        # 结果按分数降序
        assert results[0][1] >= results[1][1]
