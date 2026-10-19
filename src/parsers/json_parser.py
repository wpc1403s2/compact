"""LLM 输出 JSON 解析器 — 容错处理常见的大模型输出不规范问题。

解决三类问题：
1. 包裹在 ```json / ``` 代码围栏中（带或不带语言标识、前后可能有废话）
2. 字符串值内包含未转义的控制字符（裸换行/制表符）— qwen-max 等模型偶尔输出
3. 输出中 JSON 前后夹杂说明性文字
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class LLMJSONParseError(ValueError):
    """LLM 返回内容无法解析为 JSON。"""

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


def _strip_code_fence(text: str) -> str:
    """剥掉 Markdown 代码围栏（```...```），返回围栏内的内容。

    - 支持 ``` / ```json / ```JSON 等语言标识
    - 若没有围栏，原样返回
    - 只取第一对围栏内的内容
    """
    stripped = text.strip()
    fence_start = stripped.find("```")
    if fence_start == -1:
        return text
    # 找到第一行换行，跳过 ```<lang> 头
    after_open = stripped[fence_start + 3 :]
    newline = after_open.find("\n")
    if newline == -1:
        return text
    inner = after_open[newline + 1 :]
    fence_end = inner.rfind("```")
    if fence_end == -1:
        return text
    return inner[:fence_end]


def _extract_json_substring(text: str) -> str:
    """从可能包含说明文字的输出中提取第一个完整 {...} / [...] 子串。

    使用括号深度计数，正确处理字符串内的花括号（含转义引号）。
    """
    start = -1
    open_char = ""
    close_char = ""
    depth = 0
    in_string = False
    escape = False

    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if start == -1:
            if ch == "{":
                start = i
                open_char, close_char = "{", "}"
                depth = 1
            elif ch == "[":
                start = i
                open_char, close_char = "[", "]"
                depth = 1
            continue

        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    if start != -1:
        return text[start:]
    return text


def parse_llm_json(raw: str) -> Any:
    """从 LLM 返回的文本中解析 JSON。

    流程：剥围栏 → 提取 JSON 子串 → json.loads(strict=False) 容忍控制字符。
    失败时抛 LLMJSONParseError，附带原始文本便于排查。
    """
    if not raw or not raw.strip():
        raise LLMJSONParseError("LLM 返回为空", raw=raw)

    candidate = _strip_code_fence(raw)
    candidate = _extract_json_substring(candidate)
    candidate = candidate.strip()

    try:
        return json.loads(candidate, strict=False)
    except json.JSONDecodeError as exc:
        logger.debug(
            "LLM JSON 解析失败: %s | 原文前200字: %r",
            exc,
            raw[:200],
        )
        raise LLMJSONParseError(
            f"无法解析 LLM 返回的 JSON: {exc.msg} (line {exc.lineno} col {exc.colno})",
            raw=raw,
        ) from exc
