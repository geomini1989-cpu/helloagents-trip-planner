import pytest

from app.services.resilience_service import (
    AgentExecutionError,
    ensure_successful_tool_result,
    run_with_retry,
)


def test_transient_timeout_retries_then_succeeds():
    calls = {"count": 0}

    def runner():
        calls["count"] += 1
        if calls["count"] < 3:
            raise TimeoutError("provider timed out")
        return "ok"

    result, attempts, errors = run_with_retry(
        runner,
        max_retries=2,
        backoff_seconds=0,
    )

    assert result == "ok"
    assert attempts == 3
    assert len(errors) == 2
    assert all(error.category == "timeout" for error in errors)


def test_auth_error_does_not_retry():
    calls = {"count": 0}

    def runner():
        calls["count"] += 1
        raise RuntimeError("401 unauthorized")

    with pytest.raises(AgentExecutionError) as exc_info:
        run_with_retry(runner, max_retries=3, backoff_seconds=0)

    assert calls["count"] == 1
    assert exc_info.value.category == "auth"
    assert exc_info.value.attempts == 1


def test_validation_error_is_not_retried_by_default():
    calls = {"count": 0}

    def runner():
        calls["count"] += 1
        raise ValueError("invalid local input")

    with pytest.raises(AgentExecutionError) as exc_info:
        run_with_retry(runner, max_retries=2, backoff_seconds=0)

    assert calls["count"] == 1
    assert exc_info.value.category == "validation"


def test_planner_can_opt_into_validation_retry():
    calls = {"count": 0}

    def runner():
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("invalid generated json")
        return "valid"

    result, attempts, errors = run_with_retry(
        runner,
        max_retries=2,
        backoff_seconds=0,
        retry_categories={"validation"},
    )

    assert result == "valid"
    assert attempts == 2
    assert len(errors) == 1
    assert errors[0].category == "validation"



def test_tool_error_text_is_rejected():
    with pytest.raises(RuntimeError, match="failed"):
        ensure_successful_tool_result(
            "抱歉，工具调用失败：未找到工具 'amap_maps_weather'",
            context="Weather Agent/amap",
        )


def test_normal_tool_text_is_accepted():
    result = "工具 'maps_weather' 执行结果：北京今天晴，25℃"
    assert ensure_successful_tool_result(result, context="Weather Agent/amap") == result
