"""Tests for src.parsers.json_parser — robust parsing of LLM-returned JSON."""

from __future__ import annotations

import json

import pytest

from src.parsers.json_parser import parse_llm_json, LLMJSONParseError


# ── Control characters inside JSON strings (root cause of the bug) ──


def test_unescaped_newline_in_string_value():
    """qwen-max sometimes emits literal newlines inside string values — strict
    json.loads rejects this with 'Invalid control character'. Our parser must
    tolerate it."""
    raw = '{"issue": "第一条\n甲方责任：...", "status": "non_compliant"}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)  # confirm baseline: stdlib rejects it
    result = parse_llm_json(raw)
    assert result["status"] == "non_compliant"
    assert "第一条" in result["issue"]
    assert "\n" in result["issue"]


def test_unescaped_tab_in_string_value():
    raw = '{"clause_id": "c1", "issue": "列1\t列2"}'
    result = parse_llm_json(raw)
    assert result["issue"] == "列1\t列2"


# ── Markdown code fences ─────────────────────────────────────────


def test_fence_with_language_tag():
    raw = "```json\n" '{"findings": []}\n' "```"
    result = parse_llm_json(raw)
    assert result == {"findings": []}


def test_fence_without_language_tag():
    raw = "```\n" '{"a": 1}\n' "```"
    result = parse_llm_json(raw)
    assert result == {"a": 1}


def test_followed_by_trailing_text():
    """LLM sometimes adds a sentence after the closing fence."""
    raw = "```json\n" '{"a": 1}\n' "```\n以上是分析结果。"
    result = parse_llm_json(raw)
    assert result == {"a": 1}


def test_preceded_by_intro_text():
    raw = "分析结果如下：\n```json\n" '{"a": 1}\n' "```"
    result = parse_llm_json(raw)
    assert result == {"a": 1}


# ── Plain JSON (no fence) still works ────────────────────────────


def test_plain_json():
    assert parse_llm_json('{"a": 1, "b": [1,2]}') == {"a": 1, "b": [1, 2]}


def test_plain_json_with_leading_trailing_whitespace():
    assert parse_llm_json('   \n  {"a":1}  \n  ') == {"a": 1}


# ── Nested braces inside strings don't confuse extraction ─────────


def test_braces_in_string_value():
    raw = '{"issue": "格式如 {甲方} 应承担责任", "ok": true}'
    result = parse_llm_json(raw)
    assert result["issue"].endswith("} 应承担责任")
    assert result["ok"] is True


def test_nested_object():
    raw = '{"outer": {"inner": {"k": "v"}}, "arr": [1, 2]}'
    result = parse_llm_json(raw)
    assert result["outer"]["inner"]["k"] == "v"
    assert result["arr"] == [1, 2]


# ── Garbage input raises LLMJSONParseError (not a raw ValueError) ──


def test_garbage_raises_custom_error():
    with pytest.raises(LLMJSONParseError):
        parse_llm_json("这不是JSON，只是一段文字")


def test_empty_raises_custom_error():
    with pytest.raises(LLMJSONParseError):
        parse_llm_json("")
