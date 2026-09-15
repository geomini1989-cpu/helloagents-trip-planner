"""Agent 执行可靠性策略。

提供统一的异常分类、重试和退避逻辑，让 Orchestrator 不需要在每个 Agent
调用点重复处理失败。这里刻意不实现线程级“强制超时”：模型请求超时继续由
LLM_TIMEOUT / provider 客户端负责，避免留下无法安全终止的后台线程。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Generic, List, Optional, TypeVar


T = TypeVar("T")


@dataclass
class AttemptError:
    attempt: int
    category: str
    message: str
    retryable: bool


class AgentExecutionError(RuntimeError):
    """包含重试上下文的统一 Agent 执行异常。"""

    def __init__(self, message: str, *, category: str, attempts: int, errors: List[AttemptError]):
        super().__init__(message)
        self.category = category
        self.attempts = attempts
        self.errors = errors


def classify_exception(exc: Exception) -> tuple[str, bool]:
    """把不同 SDK / Provider 的异常收敛成少量稳定类别。"""
    text = str(exc).lower()

    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text:
        return "timeout", True

    if "429" in text or "rate limit" in text or "too many requests" in text:
        return "rate_limit", True

    if "connection" in text or "network" in text or "dns" in text:
        return "network", True

    if "401" in text or "403" in text or "unauthorized" in text or "forbidden" in text:
        return "auth", False

    if isinstance(exc, (ValueError, TypeError)):
        return "validation", False

    return "agent_error", True


def run_with_retry(
    runner: Callable[[], T],
    *,
    max_retries: int = 2,
    backoff_seconds: float = 0.5,
    on_attempt_error: Optional[Callable[[AttemptError], None]] = None,
) -> tuple[T, int, List[AttemptError]]:
    """执行函数并按统一策略重试。

    `max_retries=2` 表示最多执行 3 次。认证和本地校验类错误不会重试；
    网络、限流、超时和未分类 Agent 错误会按线性退避重试。
    """
    errors: List[AttemptError] = []
    total_attempts = max_retries + 1

    for attempt in range(1, total_attempts + 1):
        try:
            return runner(), attempt, errors
        except Exception as exc:
            category, retryable = classify_exception(exc)
            attempt_error = AttemptError(
                attempt=attempt,
                category=category,
                message=str(exc),
                retryable=retryable,
            )
            errors.append(attempt_error)
            if on_attempt_error:
                on_attempt_error(attempt_error)

            exhausted = attempt >= total_attempts
            if exhausted or not retryable:
                raise AgentExecutionError(
                    str(exc),
                    category=category,
                    attempts=attempt,
                    errors=errors,
                ) from exc

            # 简单、可预测的退避策略，便于 Eval 与 Trace 分析。
            time.sleep(backoff_seconds * attempt)

    # 理论上不会触达，仅用于满足类型检查。
    raise AgentExecutionError(
        "agent execution failed",
        category="agent_error",
        attempts=total_attempts,
        errors=errors,
    )
