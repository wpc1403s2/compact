"""通用响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """错误响应。"""
    detail: str
    error_code: str = "INTERNAL_ERROR"
    request_id: str = ""


class HealthResponse(BaseModel):
    """健康检查响应。"""
    status: str = "healthy"
    service: str = "contract-review"
    version: str = ""
    database: str = "connected"


class MessageResponse(BaseModel):
    """通用消息响应。"""
    message: str
    status: str = ""
