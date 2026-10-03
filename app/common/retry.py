
from __future__ import annotations

import asyncio
import functools
import random
import time
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger

logger = get_logger(__name__)

P = ParamSpec("P")
T = TypeVar("T")

#: A predicate deciding whether a caught exception is worth retrying.
RetryPredicate = Callable[[BaseException], bool]


def retry_always(_exc: BaseException) -> bool:
    """Default predicate: treat every exception as retryable."""
    return True


def _compute_delay(attempt: int, base_delay: float, max_delay: float, jitter: bool) -> float:
    """Exponential backoff, attempt 1 -> base_delay, attempt 2 -> 2x, etc.,
    capped at `max_delay`. With `jitter=True`, returns a uniform random
    draw in `[0, capped_delay]` ("full jitter").
    """
    capped = min(max_delay, base_delay * (2 ** (attempt - 1)))
    return random.uniform(0, capped) if jitter else capped


def with_retry(
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.5,
    max_delay_seconds: float = 30.0,
    jitter: bool = True,
    is_retryable: RetryPredicate = retry_always,
    provider: str | None = None,
    operation: str | None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator adding retry-with-backoff to a sync or async callable.

    Args:
        max_attempts: total attempts including the first (not "retries" --
            `max_attempts=3` means up to 2 retries after the initial call).
        base_delay_seconds: delay before the first retry (before jitter).
        max_delay_seconds: cap on the computed delay for any single retry.
        jitter: apply full-jitter randomization to each computed delay.
            Disable for deterministic tests.
        is_retryable: called with the caught exception; return `False` to
            stop retrying immediately (e.g. for a permanent 4xx-style
            provider error) and fail fast.
        provider: short provider name (e.g. `"openai"`, `"serpapi"`) used in
            the wrapped `InfrastructureError` and in retry log lines.
            Defaults to `"unknown"` when not supplied.
        operation: short operation name (e.g. `"generate_image"`). Defaults
            to the wrapped function's `__name__`.

    Raises:
        InfrastructureError: once attempts are exhausted, or immediately if
            `is_retryable` rejects the first exception -- wraps the last
            underlying exception and chains it as `__cause__`. The raw
            provider exception never escapes this decorator.
    """

    def decorator(fn: Callable[P, T]) -> Callable[P, T]:
        op_name = operation or fn.__name__
        provider_name = provider or "unknown"

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                attempt = 0
                last_exc: BaseException | None = None
                while attempt < max_attempts:
                    attempt += 1
                    try:
                        result = await fn(*args, **kwargs)  # type: ignore[misc]
                        return result  # type: ignore[return-value]
                    except Exception as exc:  # noqa: BLE001 - intentional: we classify below
                        last_exc = exc
                        if not is_retryable(exc) or attempt >= max_attempts:
                            break
                        delay = _compute_delay(attempt, base_delay_seconds, max_delay_seconds, jitter)
                        _log_retry(attempt, max_attempts, delay, provider_name, op_name, exc)
                        await asyncio.sleep(delay)
                raise _exhausted(last_exc, attempt, provider_name, op_name)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            attempt = 0
            last_exc: BaseException | None = None
            while attempt < max_attempts:
                attempt += 1
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - intentional: we classify below
                    last_exc = exc
                    if not is_retryable(exc) or attempt >= max_attempts:
                        break
                    delay = _compute_delay(attempt, base_delay_seconds, max_delay_seconds, jitter)
                    _log_retry(attempt, max_attempts, delay, provider_name, op_name, exc)
                    time.sleep(delay)
            raise _exhausted(last_exc, attempt, provider_name, op_name)

        return sync_wrapper

    return decorator


def _log_retry(
    attempt: int,
    max_attempts: int,
    delay: float,
    provider_name: str,
    op_name: str,
    exc: BaseException,
) -> None:
    logger.warning(
        "provider.call_failed_retrying",
        attempt=attempt,
        max_attempts=max_attempts,
        delay_seconds=round(delay, 3),
        provider=provider_name,
        operation=op_name,
        error_type=type(exc).__name__,
        error=str(exc),
    )


def _exhausted(
    last_exc: BaseException | None,
    attempts_made: int,
    provider_name: str,
    op_name: str,
) -> InfrastructureError:
    """Build the final wrapped error once retries stop, without leaking the
    raw provider exception. `last_exc` is always non-None in practice --
    the wrapper only reaches here after catching at least one exception --
    but a defensive fallback keeps this function total.
    """
    if last_exc is None:  # pragma: no cover - defensive, not reachable via with_retry
        return InfrastructureError(
            f"{op_name} failed after {attempts_made} attempt(s) with no captured exception",
            provider=provider_name,
            operation=op_name,
        )
    return InfrastructureError.wrap(
        last_exc,
        provider=provider_name,
        operation=op_name,
        message=f"{op_name} failed after {attempts_made} attempt(s): {last_exc}",
    )