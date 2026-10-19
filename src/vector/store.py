"""FAISS 向量存储 — 基于 FAISS IndexFlatIP 的向量索引和检索。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import faiss
import numpy as np

from .embeddings import EmbeddingModel

logger = logging.getLogger(__name__)


class VectorStore:
    """FAISS 向量存储，支持添加、检索、持久化。"""

    def __init__(self, embedding_model: EmbeddingModel, index_path: str | None = None):
        self._model = embedding_model
        self._index: faiss.IndexFlatIP | None = None
        self._metadata: list[dict] = []
        self._texts: list[str] = []
        self._index_path = index_path

    def add(self, texts: list[str], metadata: list[dict]) -> None:
        """将文本及其元数据嵌入后添加到向量存储。

        Args:
            texts: 待嵌入的文本列表。
            metadata: 与 texts 一一对应的元数据列表。

        Raises:
            ValueError: 当 texts 与 metadata 长度不一致时。
        """
        if not texts:
            return

        if len(texts) != len(metadata):
            raise ValueError(f"texts ({len(texts)}) 和 metadata ({len(metadata)}) 长度不一致")

        vectors = self._model.embed(texts)
        vectors_np = np.array(vectors, dtype=np.float32)

        if self._index is None:
            dim = vectors_np.shape[1]
            self._index = faiss.IndexFlatIP(dim)

        self._index.add(vectors_np)
        self._texts = [*self._texts, *texts]
        self._metadata = [*self._metadata, *metadata]

        logger.info("添加 %d 条记录到向量存储，总计 %d 条", len(texts), self.size)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """根据查询文本检索最相似的记录。

        Args:
            query: 查询文本。
            top_k: 返回的最大结果数。

        Returns:
            包含 text、metadata、score 的字典列表，按相似度降序排列。
        """
        if self._index is None or self._index.ntotal == 0:
            return []

        query_vector = np.array([self._model.embed_single(query)], dtype=np.float32)
        k = min(top_k, self._index.ntotal)
        scores, indices = self._index.search(query_vector, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            results.append({
                "text": self._texts[idx],
                "metadata": self._metadata[idx],
                "score": float(score),
            })
        return results

    def save(self, path: str | None = None) -> None:
        """将向量索引和元数据持久化到磁盘。

        Args:
            path: 保存目录路径，为 None 时使用构造时传入的 index_path。

        Raises:
            ValueError: 当未指定保存路径且构造时也未提供 index_path。
        """
        resolved = path or self._index_path
        if resolved is None:
            raise ValueError("未指定保存路径")
        save_path = Path(resolved)

        save_path.mkdir(parents=True, exist_ok=True)

        if self._index is not None:
            faiss.write_index(self._index, str(save_path / "index.faiss"))

        with open(save_path / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(
                {"texts": self._texts, "metadata": self._metadata},
                f,
                ensure_ascii=False,
                indent=2,
            )

        logger.info("向量索引已保存到 %s，共 %d 条记录", save_path, self.size)

    def load(self, path: str | None = None) -> bool:
        """从磁盘加载向量索引和元数据。

        Args:
            path: 加载目录路径，为 None 时使用构造时传入的 index_path。

        Returns:
            加载成功返回 True，路径未指定或文件不存在返回 False。
        """
        resolved = path or self._index_path
        if resolved is None:
            return False
        load_path = Path(resolved)

        index_file = load_path / "index.faiss"
        meta_file = load_path / "metadata.json"

        if not index_file.exists() or not meta_file.exists():
            logger.info("索引文件不存在: %s", load_path)
            return False

        self._index = faiss.read_index(str(index_file))

        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            self._texts = [*data.get("texts", [])]
            self._metadata = [*data.get("metadata", [])]

        logger.info("从 %s 加载向量索引，共 %d 条记录", load_path, self.size)
        return True

    @property
    def size(self) -> int:
        """返回向量存储中的记录总数。"""
        return self._index.ntotal if self._index is not None else 0
