"""Tests for LLM provider selection in src.config — multi-provider support."""

from __future__ import annotations

import importlib
import os
import sys

import pytest


# All LLM-related env vars that .env might populate. Tests start each of them at
# empty string so load_dotenv(override=False) during reload does NOT inject
# developer-machine values. Each test sets only the vars it needs.
_LLM_ENV_KEYS = (
    "LLM_PROVIDER",
    "CONTRACT_LLM_API_KEY",
    "CONTRACT_LLM_BASE_URL",
    "CONTRACT_LLM_MODEL",
    "MINIMAX_API_KEY",
    "MINIMAX_API_BASE",
    "MINIMAX_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_MODEL",
    "LLM_MODEL",
    "LLM_TEMPERATURE",
    "LLM_MAX_TOKENS",
    "LLM_TIMEOUT",
    "LLM_MAX_RETRIES",
)


def _reload_config(monkeypatch, **env_overrides):
    """Reload src.config with a clean LLM env + overrides, return (module, settings)."""
    # Blank all LLM-related keys first so .env (loaded via load_dotenv with
    # override=False inside the module) cannot leak real credentials into tests.
    for k in _LLM_ENV_KEYS:
        monkeypatch.setenv(k, "")
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)
    if "src.config" in sys.modules:
        del sys.modules["src.config"]
    for mod in list(sys.modules.keys()):
        if mod.startswith("src.") and mod != "src.config":
            del sys.modules[mod]
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
    import src.config as cfg

    cfg = importlib.reload(cfg)
    return cfg, cfg.settings


def _secret_value(secret) -> str:
    """langchain wraps API keys in SecretStr; extract the plain value."""
    return secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)


_REAL_KEYS = {
    "CONTRACT_LLM_API_KEY": "sk-c7793b13dc54417ba7a1920f0a49526e",
    "MINIMAX_API_KEY": "sk-minimax-real",
    "OPENAI_API_KEY": "sk-openai-real",
}


# ── Per-provider selection ──────────────────────────────────

from langchain_core.messages import HumanMessage, SystemMessage
def test_contract_provider_selected_explicitly(monkeypatch):
    cfg, settings = _reload_config(
        monkeypatch,
        LLM_PROVIDER="contract",
        CONTRACT_LLM_API_KEY=_REAL_KEYS["CONTRACT_LLM_API_KEY"],
        CONTRACT_LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
        CONTRACT_LLM_MODEL="qwen3.8-max-0902",
    )
    llm = cfg.get_llm()
    # result=llm.invoke([("user", "你好")])
    result=llm.invoke("你好")
    print(result.content)
    assert llm.model_name == "qwen3.8-max-0902"
    assert _secret_value(llm.openai_api_key) == "sk-c7793b13dc54417ba7a1920f0a49526e"
    assert "dashscope" in llm.openai_api_base


def test_minimax_provider_selected_explicitly(monkeypatch):
    cfg, settings = _reload_config(
        monkeypatch,
        LLM_PROVIDER="minimax",
        MINIMAX_API_KEY=_REAL_KEYS["MINIMAX_API_KEY"],
        MINIMAX_API_BASE="https://api.minimax.chat/v1",
        MINIMAX_MODEL="MiniMax-M2.7",
    )
    llm = cfg.get_llm()
    assert llm.model_name == "MiniMax-M2.7"
    assert _secret_value(llm.openai_api_key) == "sk-minimax-real"
    assert "minimax" in llm.openai_api_base


def test_openai_provider_selected_explicitly(monkeypatch):
    cfg, settings = _reload_config(
        monkeypatch,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY=_REAL_KEYS["OPENAI_API_KEY"],
        OPENAI_API_BASE="https://api.openai.com/v1",
        OPENAI_MODEL="gpt-4o-mini",
    )
    llm = cfg.get_llm()
    assert llm.model_name == "gpt-4o-mini"
    assert _secret_value(llm.openai_api_key) == "sk-openai-real"
    assert llm.openai_api_base.startswith("https://api.openai.com")


# ── Auto priority order: contract → minimax → openai ────────


def test_auto_picks_contract_first(monkeypatch):
    cfg, _ = _reload_config(
        monkeypatch,
        LLM_PROVIDER="auto",
        CONTRACT_LLM_API_KEY=_REAL_KEYS["CONTRACT_LLM_API_KEY"],
        MINIMAX_API_KEY=_REAL_KEYS["MINIMAX_API_KEY"],
        OPENAI_API_KEY=_REAL_KEYS["OPENAI_API_KEY"],
    )
    llm = cfg.get_llm()
    assert "dashscope" in llm.openai_api_base


def test_auto_picks_minimax_when_contract_missing(monkeypatch):
    cfg, _ = _reload_config(
        monkeypatch,
        LLM_PROVIDER="auto",
        # CONTRACT_LLM_API_KEY not set
        MINIMAX_API_KEY=_REAL_KEYS["MINIMAX_API_KEY"],
        OPENAI_API_KEY=_REAL_KEYS["OPENAI_API_KEY"],
    )
    llm = cfg.get_llm()
    assert "minimax" in llm.openai_api_base


def test_auto_picks_openai_when_others_missing(monkeypatch):
    cfg, _ = _reload_config(
        monkeypatch,
        LLM_PROVIDER="auto",
        OPENAI_API_KEY=_REAL_KEYS["OPENAI_API_KEY"],
    )
    llm = cfg.get_llm()
    assert llm.model_name == "gpt-4o-mini"


# ── Placeholder keys are treated as unset ───────────────────


def test_auto_all_placeholders_raises(monkeypatch):
    cfg, _ = _reload_config(
        monkeypatch,
        LLM_PROVIDER="auto",
        MINIMAX_API_KEY="your_minimax_api_key_here",
        OPENAI_API_KEY="your_openai_api_key_here",
    )
    with pytest.raises(ValueError, match="未配置任何有效 LLM"):
        cfg.get_llm()


def test_auto_skips_placeholder_picks_real(monkeypatch):
    cfg, _ = _reload_config(
        monkeypatch,
        LLM_PROVIDER="auto",
        MINIMAX_API_KEY="your_minimax_api_key_here",
        OPENAI_API_KEY=_REAL_KEYS["OPENAI_API_KEY"],
    )
    llm = cfg.get_llm()
    assert _secret_value(llm.openai_api_key) == "sk-c7793b13dc54417ba7a1920f0a49526e"


def test_explicit_provider_without_key_raises(monkeypatch):
    cfg, _ = _reload_config(
        monkeypatch,
        LLM_PROVIDER="contract",
        # CONTRACT_LLM_API_KEY missing
        MINIMAX_API_KEY=_REAL_KEYS["MINIMAX_API_KEY"],
    )
    with pytest.raises(ValueError, match="CONTRACT_LLM_API_KEY"):
        cfg.get_llm()


# ── Defaults applied when env not set ───────────────────────


def test_contract_default_base_and_model(monkeypatch):
    cfg, settings = _reload_config(
        monkeypatch,
        LLM_PROVIDER="contract",
        CONTRACT_LLM_API_KEY=_REAL_KEYS["CONTRACT_LLM_API_KEY"],
    )
    llm = cfg.get_llm()
    assert llm.model_name == "qwen3.8-max-0902"
    assert "dashscope" in llm.openai_api_base


def test_minimax_default_model(monkeypatch):
    cfg, settings = _reload_config(
        monkeypatch,
        LLM_PROVIDER="minimax",
        MINIMAX_API_KEY=_REAL_KEYS["MINIMAX_API_KEY"],
    )
    llm = cfg.get_llm()
    assert llm.model_name == "MiniMax-M2.7"


def test_openai_default_model(monkeypatch):
    cfg, settings = _reload_config(
        monkeypatch,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY=_REAL_KEYS["OPENAI_API_KEY"],
    )
    llm = cfg.get_llm()
    assert llm.model_name == "gpt-4o-mini"


# ── LLM temperature / max_tokens from env ───────────────────


def test_temperature_and_max_tokens_from_env(monkeypatch):
    cfg, _ = _reload_config(
        monkeypatch,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY=_REAL_KEYS["OPENAI_API_KEY"],
        LLM_TEMPERATURE="0.5",
        LLM_MAX_TOKENS="2048",
    )
    llm = cfg.get_llm()
    assert llm.temperature == 0.5
    assert llm.max_tokens == 2048


def test_temperature_and_max_tokens_defaults(monkeypatch):
    cfg, _ = _reload_config(
        monkeypatch,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY=_REAL_KEYS["OPENAI_API_KEY"],
    )
    llm = cfg.get_llm()
    assert llm.temperature == 0.1
    assert llm.max_tokens == 4096


# ── Edge cases ──────────────────────────────────────────────


def test_unknown_provider_raises(monkeypatch):
    cfg, _ = _reload_config(
        monkeypatch,
        LLM_PROVIDER="acme",
        CONTRACT_LLM_API_KEY=_REAL_KEYS["CONTRACT_LLM_API_KEY"],
    )
    with pytest.raises(ValueError, match="未知的 LLM_PROVIDER"):
        cfg.get_llm()


def test_legacy_llm_model_overrides_defaults(monkeypatch):
    """Backward compat: setting LLM_MODEL overrides per-provider defaults."""
    cfg, _ = _reload_config(
        monkeypatch,
        LLM_PROVIDER="auto",
        LLM_MODEL="custom-model-v1",
        OPENAI_API_KEY=_REAL_KEYS["OPENAI_API_KEY"],
    )
    llm = cfg.get_llm()
    assert llm.model_name == "custom-model-v1"
