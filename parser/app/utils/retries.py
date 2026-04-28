from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar


T = TypeVar("T")


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    retries: int = 3,
    base_delay: float = 0.5,
    retry_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await fn()
        except retry_exceptions as exc:  # type: ignore[misc]
            last_exc = exc
            if attempt == retries:
                break
            jitter = random.uniform(0.0, 0.2)
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)) + jitter)
    assert last_exc is not None
    raise last_exc
