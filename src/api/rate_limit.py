"""请求速率限制 — 基于内存的滑动窗口计数器。"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..config import settings


class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于 IP 的滑动窗口速率限制。"""

    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _get_client_ip(self, request: Request) -> str:
        """获取客户端真实 IP。"""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _cleanup(self, client_ip: str, now: float) -> None:
        """清理过期的请求记录。"""
        cutoff = now - self.window_seconds
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if t > cutoff
        ]

    async def dispatch(self, request: Request, call_next):
        # 跳过健康检查和指标端点
        if request.url.path in ("/api/v1/health", "/metrics", "/api/v1/metrics"):
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        now = time.time()

        self._cleanup(client_ip, now)
        self._requests[client_ip].append(now)

        if len(self._requests[client_ip]) > self.max_requests:
            retry_after = int(self.window_seconds - (now - self._requests[client_ip][0]))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "请求过于频繁，请稍后重试",
                    "error_code": "RATE_LIMITED",
                    "retry_after": max(retry_after, 1),
                },
                headers={"Retry-After": str(max(retry_after, 1))},
            )

        return await call_next(request)


def register_rate_limit(app: FastAPI) -> None:
    """注册速率限制中间件。"""
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=settings.RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )
