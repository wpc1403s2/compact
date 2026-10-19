"""RAG 检索器 — 从向量存储中检索相关条款，为 Agent 提供上下文增强。"""

from __future__ import annotations

from .store import VectorStore


class RAGRetriever:
    """RAG 检索器，为不同 Agent 提供定制化的上下文检索。"""

    def __init__(self, store: VectorStore):
        self._store = store

    def _retrieve(self, clause_text: str, top_k: int = 3) -> str:
        """共享检索逻辑。"""
        results = self._store.search(clause_text, top_k=top_k)
        if not results:
            return ""
        return self._format_context(results)

    def retrieve_for_risk(self, clause_text: str, top_k: int = 3) -> str:
        """为风险识别 Agent 检索相似的历史条款案例。"""
        return self._retrieve(clause_text, top_k)

    def retrieve_for_compliance(self, clause_text: str, top_k: int = 3) -> str:
        """为合规检查 Agent 检索相似条款的审查案例。"""
        return self._retrieve(clause_text, top_k)

    def retrieve_for_suggestion(self, clause_text: str, top_k: int = 3) -> str:
        """为修改建议 Agent 检索相似条款的修改模式。"""
        return self._retrieve(clause_text, top_k)

    def _format_context(self, results: list[dict]) -> str:
        """将搜索结果格式化为 LLM 可读的上下文文本。

        Args:
            results: VectorStore.search() 返回的结果列表

        Returns:
            格式化的上下文字符串
        """
        parts = []
        for i, r in enumerate(results, 1):
            title = r["metadata"].get("title", "未知条款")
            category = r["metadata"].get("category", "")
            score = r["score"]
            text = r["text"]

            part = f"[参考案例 {i}] {title}"
            if category:
                part += f"（类别：{category}）"
            part += f"\n相似度：{score:.2f}\n内容：{text}"
            parts.append(part)

        return "\n\n".join(parts)
