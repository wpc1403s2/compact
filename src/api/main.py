"""
FastAPI 应用入口 — 薄壳工厂，组装路由/中间件/异常处理。

架构：
- main.py      → 应用工厂（lifespan、中间件、路由注册）
- routers/     → 路由处理器（按领域拆分）
- schemas/     → 请求/响应 Pydantic 模型
- dependencies/ → 共享依赖（内存存储等）
- exceptions.py → 自定义异常 + 全局处理器
- middleware.py → Request ID + 访问日志
- rate_limit.py → 请求速率限制
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import text
from starlette.responses import RedirectResponse

from ..config import settings
from ..db import session as db_session
from ..db.models import Base
from ..logging_config import setup_logging
from ..tracing import init_tracing
from .exceptions import register_exception_handlers
from .middleware import register_middleware
from .rate_limit import register_rate_limit
from .routers import auth_router, health_router, metrics_router, review_router, rules_router, templates_router, regulations_router, negotiation_router, diff_router

setup_logging(level=settings.LOG_LEVEL, fmt=settings.LOG_FORMAT)
logger = logging.getLogger(__name__)


# ── 迁移与种子数据 ──────────────────────────────────────────────

_BASE_DIR = Path(__file__).resolve().parents[2]
_MIGRATIONS_DIR = _BASE_DIR / "migrations"
_SEED_FILE = _BASE_DIR / "data" / "seed.sql"


def _split_sql_statements(sql: str) -> list[str]:
    """极简 SQL 分句：按 ';' 切分，去除整行注释与空语句；不处理存储过程/$-quoted 字符串。"""
    cleaned_lines: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        cleaned_lines.append(line)
    cleaned_sql = "\n".join(cleaned_lines)
    return [stmt.strip() for stmt in cleaned_sql.split(";") if stmt.strip()]


async def _run_migrations(conn) -> None:
    """按文件名顺序执行 migrations/*.sql 中的所有语句（幂等，使用 IF NOT EXISTS）。"""
    if not _MIGRATIONS_DIR.is_dir():
        logger.warning("migrations 目录不存在: %s", _MIGRATIONS_DIR)
        return
    for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        try:
            stmts = _split_sql_statements(sql_file.read_text(encoding="utf-8"))
            for stmt in stmts:
                await conn.execute(text(stmt))
            logger.info("已执行迁移: %s (%d 条语句)", sql_file.name, len(stmts))
        except Exception as e:
            logger.error("迁移 %s 执行失败: %s", sql_file.name, e)
            raise


async def _run_seed_if_needed(conn) -> None:
    """若 SEED_ON_STARTUP=true 且 users 表为空，执行 data/seed.sql 注入测试数据。"""
    if not settings.SEED_ON_STARTUP:
        return
    if not _SEED_FILE.is_file():
        logger.warning("种子数据文件不存在: %s", _SEED_FILE)
        return
    count_result = await conn.execute(text("SELECT COUNT(*) FROM users"))
    user_count = count_result.scalar() or 0
    if user_count > 0:
        logger.info("users 表已有 %d 条记录，跳过种子数据加载", user_count)
        return
    stmts = _split_sql_statements(_SEED_FILE.read_text(encoding="utf-8"))
    for stmt in stmts:
        await conn.execute(text(stmt))
    logger.info("已加载种子数据: %s (%d 条语句)", _SEED_FILE.name, len(stmts))


# ── 应用生命周期 ──────────────────────────────────────────────

# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     """启动时初始化数据库 + 链路追踪，关闭时释放连接。"""
#     init_tracing()
#
#     if settings.DATABASE_URL:
#         try:
#             db_session.init_db(settings.DATABASE_URL)
#             async with db_session.engine.begin() as conn:
#                 await conn.run_sync(Base.metadata.create_all)
#                 await _run_migrations(conn)
#                 await _run_seed_if_needed(conn)
#             logger.info("数据库已初始化")
#         except Exception as e:
#             logger.warning("数据库连接失败，使用内存存储模式: %s", e)
#             # 重置数据库状态，确保 get_db 返回 None
#             db_session.engine = None
#             db_session.async_session_factory = None
#     else:
#         logger.warning("未配置 DATABASE_URL，使用内存存储模式")
#     yield
#     await db_session.close_db()


# ── 应用工厂 ──────────────────────────────────────────────────

def _parse_cors_origins(origins_str: str) -> list[str]:
    """解析 CORS 白名单，"*" 表示允许所有。"""
    if origins_str.strip() == "*":
        return ["*"]
    return [o.strip() for o in origins_str.split(",") if o.strip()]


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    app = FastAPI(
        title="多Agent智能合同审查系统",
        description="基于LangGraph的四Agent流水线合同审查API",
        version="1.3.0",
        # lifespan=lifespan,
    )

    # CORS — 生产环境应限制为指定域名
    cors_origins = _parse_cors_origins(settings.CORS_ORIGINS)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    # 可信主机（非 * 时启用）
    if cors_origins != ["*"]:
        hosts = [o.replace("https://", "").replace("http://", "") for o in cors_origins]
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    # GZip 压缩（减少响应体积）
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # 安全中间件
    register_rate_limit(app)
    register_middleware(app)

    # 全局异常处理器
    register_exception_handlers(app)

    # 路由注册
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(review_router)
    app.include_router(rules_router)
    app.include_router(templates_router)
    app.include_router(metrics_router)
    app.include_router(regulations_router)
    app.include_router(negotiation_router)
    app.include_router(diff_router)

    @app.get("/", include_in_schema=False)
    async def _index():
        """根路径跳转到 Swagger 文档，避免浏览器直接打开站点根时出现 404。"""
        return RedirectResponse(url="/docs")

    return app


app = create_app()


# ── CLI 入口 ──────────────────────────────────────────────────

def start():
    """启动API服务器。"""
    import uvicorn

    uvicorn.run(
        "src.api.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=True,
    )


if __name__ == "__main__":
    start()
