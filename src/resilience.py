"""弹性机制 — 超时 + 重试 + 熔断器。

用于 Agent 调用 LLM 时的容错处理：
- 超时：防止 LLM 调用无限挂起
- 重试：临时失败自动重试（指数退避）
- 熔断：连续失败后快速失败，避免雪崩
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── 熔断器状态 ──────────────────────────────────────────────

class CircuitState(str, Enum):
    CLOSED = "closed"       # 正常，允许请求
    OPEN = "open"           # 熔断，快速失败
    HALF_OPEN = "half_open" # 半开，尝试恢复


# ── 熔断器 ──────────────────────────────────────────────────

@dataclass
class CircuitBreaker:
    """简单熔断器：连续失败 N 次后熔断，M 秒后半开尝试恢复。

    用法：
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        result = breaker.call(some_function, arg1, arg2)
    """
    failure_threshold: int = 3          # 连续失败次数阈值
    recovery_timeout: float = 30.0      # 熔断恢复时间（秒）
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)

    def _check_state(self) -> None:
        """检查是否应该从 OPEN 转为 HALF_OPEN。"""
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("熔断器进入半开状态，尝试恢复")

    def record_success(self) -> None:
        """记录成功，重置计数器。"""
        self._failure_count = 0
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            logger.info("熔断器恢复正常")

    def record_failure(self) -> None:
        """记录失败，达到阈值时熔断。"""
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning("熔断器打开：连续失败 %d 次", self._failure_count)

    @property
    def is_open(self) -> bool:
        self._check_state()
        return self._state == CircuitState.OPEN

    @property
    def state(self) -> CircuitState:
        self._check_state()
        return self._state


# ── 超时执行 ──────────────────────────────────────────────

async def with_timeout(coro, timeout_seconds: float) -> Any:
    """带超时的协程执行。"""
    return await asyncio.wait_for(coro, timeout=timeout_seconds)


# ── 弹性调用 ──────────────────────────────────────────────

async def resilient_call(
    func: Callable,
    *args,
    max_retries: int = 0,
    base_delay: float = 1.0,
    timeout_seconds: float = 60.0,
    circuit_breaker: CircuitBreaker | None = None,
    **kwargs,
) -> Any:
    """弹性调用：超时 + 重试（指数退避） + 熔断。

    Args:
        func: 要调用的异步函数
        max_retries: 最大重试次数
        base_delay: 初始重试延迟（秒）
        timeout_seconds: 单次调用超时（秒）
        circuit_breaker: 可选的熔断器实例

    Returns:
        函数返回值

    Raises:
        最后一次重试的异常
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        # 熔断检查
        if circuit_breaker and circuit_breaker.is_open:
            raise RuntimeError(f"熔断器已打开，拒绝调用（连续失败 {circuit_breaker._failure_count} 次）")

        attempt_started_at = time.perf_counter()
        try:
            result = await with_timeout(func(*args, **kwargs), timeout_seconds)
            if circuit_breaker:
                circuit_breaker.record_success()
            logger.info(
                "调用成功 attempt=%d/%d elapsed=%.2fs",
                attempt + 1,
                max_retries + 1,
                time.perf_counter() - attempt_started_at,
            )
            return result

        except asyncio.TimeoutError:
            last_exception = RuntimeError(f"调用超时（{timeout_seconds}秒）")
            if circuit_breaker:
                circuit_breaker.record_failure()
            logger.warning(
                "调用超时 attempt=%d/%d timeout=%.1fs elapsed=%.2fs",
                attempt + 1,
                max_retries + 1,
                timeout_seconds,
                time.perf_counter() - attempt_started_at,
            )

        except Exception as e:
            last_exception = e
            if circuit_breaker:
                circuit_breaker.record_failure()
            logger.warning(
                "调用失败 attempt=%d/%d elapsed=%.2fs: %s",
                attempt + 1,
                max_retries + 1,
                time.perf_counter() - attempt_started_at,
                str(e),
            )

        # 指数退避
        if attempt < max_retries:
            delay = base_delay * (2 ** attempt)
            logger.info("等待 %.1fs 后重试...", delay)
            await asyncio.sleep(delay)

    raise last_exception


# ── 全局熔断器实例 ──────────────────────────────────────────

# 每个 Agent 一个独立的熔断器
_agent_circuit_breakers: dict[str, CircuitBreaker] = {}


def get_agent_breaker(agent_name: str) -> CircuitBreaker:
    """获取 Agent 的熔断器实例。"""
    if agent_name not in _agent_circuit_breakers:
        _agent_circuit_breakers[agent_name] = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=60.0,
        )
    return _agent_circuit_breakers[agent_name]


def reset_all_breakers() -> None:
    """重置所有熔断器（用于测试或手动恢复）。"""
    for breaker in _agent_circuit_breakers.values():
        breaker._failure_count = 0
        breaker._state = CircuitState.CLOSED
    logger.info("所有熔断器已重置")
