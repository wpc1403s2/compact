"""
应用配置 — 通过环境变量加载，绝不硬编码密钥。

LLM 提供商通过 LLM_PROVIDER 选择：auto | contract | minimax | openai
- auto: 按 contract → minimax → openai 顺序取第一个有效 key
- contract: 通用 OpenAI 兼容端点（通义千问、DeepSeek、智谱、本地 vLLM 等）
- minimax: MiniMax 官方
- openai: OpenAI 官方
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    val = os.getenv(key)
    return val if val not in (None, "") else default


def _env_int(key: str, default: int) -> int:
    val = os.getenv(key)
    return int(val) if val not in (None, "") else default


def _env_float(key: str, default: float) -> float:
    val = os.getenv(key)
    return float(val) if val not in (None, "") else default


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val in (None, ""):
        return default
    return val.lower() in ("1", "true", "yes", "on")


def _is_real_key(key: str) -> bool:
    """API key 非空且不是 'your_xxx_here' 等占位符。"""
    return bool(key) and not key.startswith("your_")


class Settings:
    def __init__(self) -> None:
        # ── LLM 提供商选择 ──────────────────────────────────
        self.LLM_PROVIDER: str = _env("LLM_PROVIDER", "auto").lower()

        # contract: 通用 OpenAI 兼容端点（千问、DeepSeek、智谱…）
        self.CONTRACT_LLM_API_KEY: str = _env("CONTRACT_LLM_API_KEY", "")
        self.CONTRACT_LLM_BASE_URL: str = _env(
            "CONTRACT_LLM_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.CONTRACT_LLM_MODEL: str = _env("CONTRACT_LLM_MODEL", "qwen3.8-max-0902")

        # MiniMax
        self.MINIMAX_API_KEY: str = _env("MINIMAX_API_KEY", "")
        self.MINIMAX_API_BASE: str = _env("MINIMAX_API_BASE", "https://api.minimax.chat/v1")
        self.MINIMAX_MODEL: str = _env("MINIMAX_MODEL", "MiniMax-M2.7")

        # OpenAI 官方
        self.OPENAI_API_KEY: str = _env("OPENAI_API_KEY", "")
        self.OPENAI_API_BASE: str = _env("OPENAI_API_BASE", "https://api.openai.com/v1")
        self.OPENAI_MODEL: str = _env("OPENAI_MODEL", "gpt-4o-mini")

        # 向后兼容：旧 LLM_MODEL 若设置则覆盖所有 provider 的默认 model
        legacy_model = _env("LLM_MODEL", "")
        if legacy_model:
            if not _env("MINIMAX_MODEL", ""):
                self.MINIMAX_MODEL = legacy_model
            if not _env("OPENAI_MODEL", ""):
                self.OPENAI_MODEL = legacy_model
            if not _env("CONTRACT_LLM_MODEL", ""):
                self.CONTRACT_LLM_MODEL = legacy_model

        # ── LLM 调用参数 ────────────────────────────────────
        self.LLM_TIMEOUT: int = _env_int("LLM_TIMEOUT", 30)
        self.LLM_MAX_RETRIES: int = _env_int("LLM_MAX_RETRIES", 0)
        self.LLM_TEMPERATURE: float = _env_float("LLM_TEMPERATURE", 0.1)
        self.LLM_MAX_TOKENS: int = _env_int("LLM_MAX_TOKENS", 4096)
        self.CIRCUIT_BREAKER_THRESHOLD: int = _env_int("CIRCUIT_BREAKER_THRESHOLD", 3)
        self.CIRCUIT_RECOVERY_TIMEOUT: int = _env_int("CIRCUIT_RECOVERY_TIMEOUT", 30)

        # ── 应用基础配置 ────────────────────────────────────
        self.APP_HOST: str = _env("APP_HOST", "0.0.0.0")
        self.APP_PORT: int = _env_int("APP_PORT", 8000)
        self.LOG_LEVEL: str = _env("LOG_LEVEL", "INFO")
        self.LOG_FORMAT: str = _env("LOG_FORMAT", "text")

        # ── 向量数据库 ──────────────────────────────────────
        self.VECTOR_DB_TYPE: str = _env("VECTOR_DB_TYPE", "faiss")
        self.EMBEDDING_MODEL: str = _env("EMBEDDING_MODEL", "shibing624/text2vec-base-chinese")
        self.FAISS_INDEX_PATH: str = _env(
            "FAISS_INDEX_PATH",
            str(Path(__file__).parent.parent / "data" / "faiss_index"),
        )

        # ── 数据库 ──────────────────────────────────────────
        self.DATABASE_URL: str = _env(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5433/contract_review",
        )
        # 启动时若 users 表为空则加载 data/seed.sql（本地开发使用）
        self.SEED_ON_STARTUP: bool = _env_bool("SEED_ON_STARTUP", False)
        self.DATABASE_URL_SYNC: str = _env(
            "DATABASE_URL_SYNC",
            "postgresql://postgres:postgres@localhost:5433/contract_review",
        )
        self.DB_POOL_SIZE: int = _env_int("DB_POOL_SIZE", 10)
        self.DB_MAX_OVERFLOW: int = _env_int("DB_MAX_OVERFLOW", 20)

        # ── JWT 认证 ────────────────────────────────────────
        self.JWT_SECRET_KEY: str = _env("JWT_SECRET_KEY", "dev-secret-change-in-production")
        self.JWT_ALGORITHM: str = _env("JWT_ALGORITHM", "HS256")
        self.JWT_EXPIRE_MINUTES: int = _env_int("JWT_EXPIRE_MINUTES", 60)

        # ── Celery / Redis ─────────────────────────────────
        self.CELERY_BROKER_URL: str = _env("CELERY_BROKER_URL", "redis://localhost:6379/0")
        self.CELERY_RESULT_BACKEND: str = _env("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
        self.CELERY_TASK_TIME_LIMIT: int = _env_int("CELERY_TASK_TIME_LIMIT", 300)
        self.CELERY_TASK_SOFT_TIME_LIMIT: int = _env_int("CELERY_TASK_SOFT_TIME_LIMIT", 240)

        # ── 安全 ────────────────────────────────────────────
        self.CORS_ORIGINS: str = _env("CORS_ORIGINS", "*")
        self.RATE_LIMIT_PER_MINUTE: int = _env_int("RATE_LIMIT_PER_MINUTE", 60)
        self.MAX_UPLOAD_SIZE_MB: int = _env_int("MAX_UPLOAD_SIZE_MB", 20)

        # ── OpenTelemetry ──────────────────────────────────
        self.OTEL_ENABLED: bool = _env_bool("OTEL_ENABLED", False)
        self.OTEL_SERVICE_NAME: str = _env("OTEL_SERVICE_NAME", "contract-review")
        self.OTEL_EXPORTER_OTLP_ENDPOINT: str = _env(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"
        )

        # ── 路径常量 ────────────────────────────────────────
        self.PROJECT_ROOT: Path = Path(__file__).parent.parent
        self.RULES_DIR: Path = self.PROJECT_ROOT / "src" / "rules" / "definitions"


settings = Settings()


# ── LLM 客户端工厂 ──────────────────────────────────────────


_PROVIDER_SPECS = (
    ("contract", "CONTRACT_LLM_API_KEY", "CONTRACT_LLM_BASE_URL", "CONTRACT_LLM_MODEL"),
    ("minimax", "MINIMAX_API_KEY", "MINIMAX_API_BASE", "MINIMAX_MODEL"),
    ("openai", "OPENAI_API_KEY", "OPENAI_API_BASE", "OPENAI_MODEL"),
)


def get_llm(settings_obj: Settings | None = None):
    """根据 LLM_PROVIDER 构造 ChatOpenAI 客户端。

    - auto: 按 contract → minimax → openai 顺序选第一个有效 key
    - 指定 provider: 若该 provider 无有效 key 则抛出 ValueError
    所有 provider 都走 OpenAI 兼容接口（ChatOpenAI）。
    """
    from langchain_openai import ChatOpenAI

    s = settings_obj or settings
    provider = "contract"

    if provider == "auto":
        candidates = _PROVIDER_SPECS
    else:
        candidates = [c for c in _PROVIDER_SPECS if c[0] == provider]
        if not candidates:
            raise ValueError(
                f"未知的 LLM_PROVIDER='{provider}'，"
                "可选值：auto | contract | minimax | openai"
            )

    for name, key_attr, base_attr, model_attr in candidates:
        api_key = getattr(s, key_attr)
        if _is_real_key(api_key):
            base_url = getattr(s, base_attr)
            model = getattr(s, model_attr)
            # 诊断日志：打印选中的 provider/base_url/model（不含 API key）
            logger.info(
                "LLM 客户端已构造: provider=%s base_url=%s model=%s timeout=%ss",
                name, base_url, model, s.LLM_TIMEOUT,
            )
            return ChatOpenAI(
                model=model,
                api_key=api_key,
                base_url=base_url,
                temperature=s.LLM_TEMPERATURE,
                max_tokens=s.LLM_MAX_TOKENS,
                request_timeout=s.LLM_TIMEOUT,
                max_retries=0,
            )
        if provider != "auto":
            raise ValueError(
                f"LLM_PROVIDER='{name}' 但未配置有效 {key_attr}（空或占位符）"
            )

    raise ValueError(
        "未配置任何有效 LLM API Key。请在 .env 中设置 "
        "CONTRACT_LLM_API_KEY / MINIMAX_API_KEY / OPENAI_API_KEY 之一，"
        "或设置 LLM_PROVIDER=contract|minimax|openai 明确选择。"
    )
