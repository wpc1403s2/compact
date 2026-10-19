"""向量数据库模块 — FAISS + sentence-transformers 实现语义检索。"""

from .embeddings import EmbeddingModel
from .rag import RAGRetriever
from .store import VectorStore

__all__ = ["EmbeddingModel", "RAGRetriever", "VectorStore"]
