"""校验 .env 里配置的 LLM API Key 是否真的可用。

两种用法：

1) 命令行（推荐，输出最直观）
       python tests/test_apikey.py            # 校验 CONTRACT_LLM_API_KEY
       python tests/test_apikey.py openai     # 校验 OPENAI_API_KEY
       python tests/test_apikey.py minimax    # 校验 MINIMAX_API_KEY

2) pytest
       python -m pytest tests/test_apikey.py -v
   未配置 Key 时自动跳过（skip），配置了才发起真实请求。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)

import httpx  # noqa: E402
import pytest  # noqa: E402

from src.config import settings  # noqa: E402

# provider -> (key 属性, base_url 属性, model 属性)
PROVIDERS = {
    "contract": ("CONTRACT_LLM_API_KEY", "CONTRACT_LLM_BASE_URL", "CONTRACT_LLM_MODEL"),
    "minimax": ("MINIMAX_API_KEY", "MINIMAX_API_BASE", "MINIMAX_MODEL"),
    "openai": ("OPENAI_API_KEY", "OPENAI_API_BASE", "OPENAI_MODEL"),
}

PLACEHOLDERS = ("", "sk-xxx", "your_key_here", "your-api-key", "change-me", "none")

TIMEOUT = 30.0


def mask(key: str) -> str:
    """隐藏 key 中间部分，只显示前后各 4 位。"""
    if not key:
        return "<未配置>"
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}****{key[-4:]} (长度 {len(key)})"


def normalize_base(base: str) -> str:
    """保证 base_url 以 /v1 结尾（OpenAI 兼容路径）。"""
    base = (base or "").rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def get_key(provider: str) -> str:
    """读取指定 provider 的 Key（已去空白）。"""
    key_attr, _, _ = PROVIDERS[provider]
    return (getattr(settings, key_attr) or "").strip()


def probe(provider: str, verbose: bool = True) -> tuple[bool, str]:
    """向 provider 发一条最小请求，返回 (是否有效, 结论说明)。"""
    key_attr, base_attr, model_attr = PROVIDERS[provider]
    api_key = get_key(provider)
    base_url = getattr(settings, base_attr)
    model = getattr(settings, model_attr)

    if verbose:
        print("=" * 56)
        print(f"provider : {provider}   (LLM_PROVIDER={settings.LLM_PROVIDER})")
        print(f"env key  : {key_attr}")
        print(f"value    : {mask(api_key)}")
        print(f"base_url : {base_url}")

    if api_key.lower() in PLACEHOLDERS:
        msg = f"未配置 {key_attr} 或仍是占位符"
        if verbose:
            print(f"结果     : {msg}")
        return False, msg
    if not base_url:
        msg = f"未配置 {base_attr}"
        if verbose:
            print(f"结果     : {msg}")
        return False, msg

    # url = normalize_base(base_url) + "/chat/completions"
    url = normalize_base(base_url)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 8,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    if verbose:
        print(f"请求     : POST {url}")
        print(f"model    : {model}")

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=TIMEOUT)
    except httpx.TimeoutException:
        return False, f"请求 {url} 超时（{TIMEOUT}s），检查网络/代理"
    except httpx.HTTPError as e:
        return False, f"网络错误 {type(e).__name__}: {e}"

    if verbose:
        print(f"HTTP     : {resp.status_code}")

    body = resp.text[:300]
    if resp.status_code == 200:
        content = resp.json()["choices"][0]["message"]["content"]
        if verbose:
            print(f"结果     : 有效 ✓  模型回复：{content!r}")
        return True, f"有效，模型回复：{content!r}"

    if resp.status_code in (401, 403):
        msg = "Key 无效 ✗（错误/过期，或账号无此模型权限）"
    elif resp.status_code == 404:
        msg = f"404：base_url 或模型名不对（{model}）"
    elif resp.status_code == 429:
        msg = "Key 有效，但被限流或额度已用尽"
    elif resp.status_code >= 500:
        msg = "服务端错误，稍后重试"
    else:
        msg = f"异常响应 {resp.status_code}"
    if verbose:
        print(f"结果     : {msg}")
        print(f"响应     : {body}")
    return False, f"{msg}：{body}"


# ── pytest 用例 ─────────────────────────────────────────────
import openai
from langchain_openai import ChatOpenAI
@pytest.mark.network
def test_contract_llm_api_key():
    """CONTRACT_LLM_API_KEY 应能通过 API 校验（未配置则跳过）。"""

    if not settings.CONTRACT_LLM_API_KEY:
        pytest.skip("CONTRACT_LLM_API_KEY 未配置")

    llm = ChatOpenAI(
        model=settings.CONTRACT_LLM_MODEL,
        api_key=settings.CONTRACT_LLM_API_KEY,
        base_url=settings.CONTRACT_LLM_BASE_URL,
        temperature=0,
        timeout=30,
    )
    result = llm.invoke("hi")
    print(result)
    assert True, 'ok'


# @pytest.mark.network
# def test_openai_api_key():
#     if get_key("openai").lower() in PLACEHOLDERS:
#         pytest.skip("未配置 OPENAI_API_KEY，跳过")
#     ok, msg = probe("openai")
#     assert ok, msg


# @pytest.mark.network
# def test_minimax_api_key():
#     if get_key("minimax").lower() in PLACEHOLDERS:
#         pytest.skip("未配置 MINIMAX_API_KEY，跳过")
#     ok, msg = probe("minimax")
#     assert ok, msg


# ── CLI 入口 ────────────────────────────────────────────────

def main() -> int:
    provider = sys.argv[1] if len(sys.argv) > 1 else "contract"
    if provider not in PROVIDERS:
        print(f"未知 provider: {provider}，可选：{' | '.join(PROVIDERS)}")
        return 2
    if get_key(provider).lower() in PLACEHOLDERS:
        print(f"{PROVIDERS[provider][0]} 未配置，无法校验")
        return 1
    ok, msg = probe(provider)
    print("-" * 56)
    print(f"结论：{msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
