"""内存回退存储 — 无数据库时使用。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MemoryStore:
    """轻量级内存存储，用于无数据库环境的开发/测试。"""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self._store.get(key)

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._store[key] = value

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def list_keys(self) -> list[str]:
        return list(self._store.keys())

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


_memory_store = MemoryStore()


def get_memory_store() -> MemoryStore:
    """FastAPI 依赖注入：获取内存存储实例。"""
    return _memory_store
