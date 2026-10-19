"""自定义异常和全局异常处理器。"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


# ── 自定义异常 ──────────────────────────────────────────────

class ReviewNotFoundError(Exception):
    """审查记录不存在。"""
    def __init__(self, review_id: str = "") -> None:
        self.review_id = review_id
        super().__init__(f"审查记录不存在: {review_id}")


class ReviewProcessingError(Exception):
    """审查处理失败。"""
    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail)


class InvalidFileFormatError(Exception):
    """不支持的文件格式。"""
    def __init__(self, format: str = "") -> None:
        self.format = format
        super().__init__(f"不支持的文件格式: {format}")


# ── 异常处理器注册 ──────────────────────────────────────────

def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器。"""

    @app.exception_handler(ReviewNotFoundError)
    async def review_not_found_handler(request: Request, exc: ReviewNotFoundError):
        request_id = getattr(request.state, "request_id", "")
        return JSONResponse(
            status_code=404,
            content={
                "detail": str(exc),
                "error_code": "REVIEW_NOT_FOUND",
                "request_id": request_id,
            },
        )

    @app.exception_handler(ReviewProcessingError)
    async def review_processing_error_handler(request: Request, exc: ReviewProcessingError):
        request_id = getattr(request.state, "request_id", "")
        logger.error("审查处理失败 request_id=%s: %s", request_id, exc.detail)
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"审查处理失败: {exc.detail}",
                "error_code": "REVIEW_PROCESSING_ERROR",
                "request_id": request_id,
            },
        )

    @app.exception_handler(InvalidFileFormatError)
    async def invalid_file_format_handler(request: Request, exc: InvalidFileFormatError):
        request_id = getattr(request.state, "request_id", "")
        return JSONResponse(
            status_code=400,
            content={
                "detail": str(exc),
                "error_code": "INVALID_FILE_FORMAT",
                "request_id": request_id,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", "")
        errors = []
        for error in exc.errors():
            loc = " → ".join(str(l) for l in error["loc"])
            errors.append(f"{loc}: {error['msg']}")
        return JSONResponse(
            status_code=422,
            content={
                "detail": "请求参数验证失败",
                "errors": errors,
                "error_code": "VALIDATION_ERROR",
                "request_id": request_id,
            },
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "")
        logger.error("未处理异常 request_id=%s: %s", request_id, exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "服务器内部错误",
                "error_code": "INTERNAL_ERROR",
                "request_id": request_id,
            },
        )
