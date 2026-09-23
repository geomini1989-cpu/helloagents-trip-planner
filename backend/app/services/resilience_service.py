"""Agent 执行可靠性策略。

提供统一的异常分类、重试和退避逻辑，让 Orchestrator 不需要在每个 Agent
调用点重复处理失败。这里刻意不实现线程级“强制超时”：模型请求超时继续由
LLM_TIMEOUT / provider 客户端负责，避免留下无法安全终止的后台线程。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Set, TypeVar


T = TypeVar("T")

_TOOL_FAILURE_MARKERS = (
    "tool_error:",
    "未找到工具",
    "工具不存在",
    "工具调用失败",
    "mcp 操作失败",
    "异步操作失败",
    "tool not found",
    "unknown tool",
    "no such tool",
)


def ensure_successful_tool_result(result: T, *, context: str = "tool") -> T:
    """Reject textual tool failures so Eval/Trace cannot report false success.

    Some Agent runtimes return a normal string even when the underlying tool call
    failed. Treat well-known failure markers and empty output as execution errors,
    allowing the existing retry/fallback path to handle them consistently.
    """
    text = str(result or "").strip()
    if not text:
        raise RuntimeError(f"{context} returned empty result")

    normalized = text.lower()
    marker = next((item for item in _TOOL_FAILURE_MARKERS if item in normalized), None)
    if marker:
        raise RuntimeError(f"{context} failed: {text[:500]}")
    return result


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
    retry_categories: Optional[Set[str]] = None,
    on_attempt_error: Optional[Callable[[AttemptError], None]] = None,
) -> tuple[T, int, List[AttemptError]]:
    """执行函数并按统一策略重试。

    `max_retries=2` 表示最多执行 3 次。默认使用异常分类器给出的 retryable；
    调用方也可以传入 `retry_categories` 覆盖策略。例如 Planner 可以把
    `validation` 加入重试集合，因为模型偶发生成非法 JSON 时重新生成是合理的。
    认证错误始终不会重试。
    """
    errors: List[AttemptError] = []
    total_attempts = max_retries + 1

    for attempt in range(1, total_attempts + 1):
        try:
            return runner(), attempt, errors
        except Exception as exc:
            category, default_retryable = classify_exception(exc)
            retryable = default_retryable
            if retry_categories is not None:
                retryable = category in retry_categories
            if category == "auth":
                retryable = False

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

            # 简单、可预测的线性退避，便于 Eval 与 Trace 分析。
            time.sleep(backoff_seconds * attempt)

    raise AgentExecutionError(
        "agent execution failed",
        category="agent_error",
        attempts=total_attempts,
        errors=errors,
    )
