"""Embedding 模型封装 — 基于 sentence-transformers 的中文法律文本嵌入。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "shibing624/text2vec-base-chinese"


class EmbeddingModel:
    """sentence-transformers 封装，支持懒加载和中文法律文本嵌入。"""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self._model_name = model_name
        self._model = None

    def _load_model(self):
        """懒加载模型，仅在首次调用时加载。"""
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers 未安装。请运行: "
                "pip install sentence-transformers>=2.2.0"
            )
        logger.info("加载 Embedding 模型: %s", self._model_name)
        self._model = SentenceTransformer(self._model_name)
        logger.info("模型加载完成，维度: %d", self._model.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> list[list[float]]:
        """将文本列表转换为向量。"""
        self._load_model()
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return vectors.tolist()

    def embed_single(self, text: str) -> list[float]:
        """将单个文本转换为向量。"""
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        """返回嵌入向量维度。"""
        self._load_model()
        return self._model.get_sentence_embedding_dimension()
