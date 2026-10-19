"""监控指标路由 — Prometheus 兼容格式 + 应用指标。"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, defaultdict

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])

# ── 指标存储（线程安全）────────────────────────────────────

_lock = threading.Lock()
_request_count: Counter = Counter()
_request_latency: dict[tuple[str, str], list[float]] = defaultdict(list)
_review_count: Counter = Counter()
_review_risk: Counter = Counter()
APP_START_TIME = time.time()

# ── 指标操作 ──────────────────────────────────────────────


def record_request(method: str, path: str, status_code: int, duration: float) -> None:
    """记录一次请求的指标。"""
    with _lock:
        _request_count[(method, path, str(status_code))] += 1
        _request_latency[(method, path)].append(duration)


def record_review(status: str, risk_level: str = "none") -> None:
    """记录一次审查的指标。"""
    with _lock:
        _review_count[status] += 1
        _review_risk[risk_level] += 1


# ── 指标导出 ──────────────────────────────────────────────

def _format_prometheus() -> str:
    """将所有指标格式化为 Prometheus text exposition 格式。"""
    lines = []

    # uptime
    uptime = time.time() - APP_START_TIME
    lines.append("# HELP app_uptime_seconds 应用运行时间")
    lines.append("# TYPE app_uptime_seconds gauge")
    lines.append(f"app_uptime_seconds {uptime:.1f}")
    lines.append("")

    # request count
    lines.append("# HELP http_requests_total HTTP 请求总数")
    lines.append("# TYPE http_requests_total counter")
    with _lock:
        for (method, path, status), value in _request_count.items():
            lines.append(f'http_requests_total{{method="{method}",path="{path}",status_code="{status}"}} {value}')
    lines.append("")

    # request latency
    lines.append("# HELP http_request_duration_seconds HTTP 请求延迟")
    lines.append("# TYPE http_request_duration_seconds summary")
    with _lock:
        for (method, path), durations in _request_latency.items():
            if durations:
                avg = sum(durations) / len(durations)
                p50 = sorted(durations)[len(durations) // 2]
                p99 = sorted(durations)[int(len(durations) * 0.99)]
                lines.append(f'http_request_duration_seconds{{method="{method}",path="{path}",quantile="0.5"}} {p50:.3f}')
                lines.append(f'http_request_duration_seconds{{method="{method}",path="{path}",quantile="0.99"}} {p99:.3f}')
                lines.append(f'http_request_duration_seconds_sum{{method="{method}",path="{path}"}} {sum(durations):.3f}')
                lines.append(f'http_request_duration_seconds_count{{method="{method}",path="{path}"}} {len(durations)}')
    lines.append("")

    # review count
    lines.append("# HELP contract_reviews_total 合同审查总数")
    lines.append("# TYPE contract_reviews_total counter")
    with _lock:
        for status, value in _review_count.items():
            lines.append(f'contract_reviews_total{{status="{status}"}} {value}')
    lines.append("")

    # risk level
    lines.append("# HELP contract_review_risk_level_total 审查风险等级分布")
    lines.append("# TYPE contract_review_risk_level_total counter")
    with _lock:
        for risk, value in _review_risk.items():
            lines.append(f'contract_review_risk_level_total{{risk_level="{risk}"}} {value}')

    return "\n".join(lines)


# ── 路由 ──────────────────────────────────────────────────

@router.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    """Prometheus 兼容的指标端点。"""
    return PlainTextResponse(
        content=_format_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/api/v1/metrics")
async def app_metrics():
    """应用级指标（JSON 格式）。"""
    uptime = time.time() - APP_START_TIME
    with _lock:
        total_requests = sum(_request_count.values())
        total_reviews = sum(_review_count.values())
        risk_dist = dict(_review_risk)
    return {
        "uptime_seconds": round(uptime, 1),
        "request_count": total_requests,
        "review_count": total_reviews,
        "risk_distribution": risk_dist,
    }
