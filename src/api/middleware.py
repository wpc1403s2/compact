"""请求中间件 — Request ID 追踪 + 结构化访问日志 + 指标采集 + 链路追踪。"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from ..tracing import get_tracer
from .routers.metrics import record_request

logger = logging.getLogger("access")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """为每个请求分配唯一 ID，注入 request.state.request_id。"""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:12])
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """结构化访问日志 + OpenTelemetry Span。"""

    async def dispatch(self, request: Request, call_next):
        tracer = get_tracer()
        with tracer.start_span(f"{request.method} {request.url.path}") as span:
            start = time.perf_counter()
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start) * 1000

            request_id = getattr(request.state, "request_id", "-")

            # Span 属性
            span.set_attribute("http.method", request.method)
            span.set_attribute("http.url", str(request.url))
            span.set_attribute("http.status_code", response.status_code)
            span.set_attribute("http.request_id", request_id)
            span.set_attribute("http.duration_ms", round(duration_ms, 1))

            # 结构化日志
            extra = {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 1),
            }

            # Prometheus 指标
            record_request(
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration=duration_ms / 1000,
            )

            if response.status_code >= 500:
                logger.error(
                    "%s %s → %d (%.1fms) request_id=%s",
                    request.method, request.url.path,
                    response.status_code, duration_ms, request_id,
                    extra=extra,
                )
            elif response.status_code >= 400:
                logger.warning(
                    "%s %s → %d (%.1fms) request_id=%s",
                    request.method, request.url.path,
                    response.status_code, duration_ms, request_id,
                    extra=extra,
                )
            else:
                logger.info(
                    "%s %s → %d (%.1fms) request_id=%s",
                    request.method, request.url.path,
                    response.status_code, duration_ms, request_id,
                    extra=extra,
                )

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """添加安全响应头。"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


def register_middleware(app: FastAPI) -> None:
    """注册中间件（注意顺序：后注册的先执行）。"""
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
