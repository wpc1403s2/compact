"""OpenTelemetry 链路追踪配置 — 可选模块。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_tracer = None
_initialized = False


def init_tracing() -> None:
    """初始化 OpenTelemetry 追踪（按需加载）。"""
    global _initialized

    if _initialized:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        from .config import settings

        if not settings.OTEL_ENABLED:
            logger.debug("OpenTelemetry 未启用")
            _initialized = True
            return

        resource = Resource.create({
            "service.name": settings.OTEL_SERVICE_NAME,
            "service.version": "1.2.0",
        })

        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        _initialized = True
        logger.info("OpenTelemetry 已初始化，导出到 %s", settings.OTEL_EXPORTER_OTLP_ENDPOINT)

    except ImportError:
        logger.debug("OpenTelemetry SDK 未安装，跳过链路追踪")
        _initialized = True
    except Exception as e:
        logger.warning("OpenTelemetry 初始化失败: %s", e)
        _initialized = True


def get_tracer(name: str = "contract-review"):
    """获取 tracer 实例。"""
    global _tracer

    if _tracer is not None:
        return _tracer

    try:
        from opentelemetry import trace
        _tracer = trace.get_tracer(name)
    except ImportError:
        _tracer = _DummyTracer()

    return _tracer


class _DummyTracer:
    """当 OpenTelemetry 不可用时的空实现。"""

    def start_span(self, name: str, **kwargs):
        return _DummySpan()

    def start_as_current_span(self, name: str, **kwargs):
        return _DummySpan()


class _DummySpan:
    """空 Span 实现。"""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, key: str, value):
        pass

    def set_status(self, status):
        pass

    def add_event(self, name: str, attributes=None):
        pass
