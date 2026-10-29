from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from .contracts import QueueFullError

T = TypeVar("T")


class InferenceQueue:
    """One CPU inference thread with one additional outstanding request."""

    def __init__(self, *, max_outstanding: int = 2, timeout_seconds: float = 60):
        if max_outstanding < 1:
            raise ValueError("max_outstanding must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.max_outstanding = max_outstanding
        self.timeout_seconds = timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="retrieval-inference")
        self._lock = asyncio.Lock()
        self._outstanding = 0
        self._closed = False

    @property
    def outstanding(self) -> int:
        return self._outstanding

    async def run(self, call: Callable[[], T]) -> T:
        async with self._lock:
            if self._closed or self._outstanding >= self.max_outstanding:
                raise QueueFullError()
            self._outstanding += 1

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, call)
        try:
            result = await asyncio.wait_for(
                asyncio.shield(future), timeout=self.timeout_seconds
            )
        except TimeoutError as exc:
            asyncio.create_task(self._release_when_done(future))
            raise TimeoutError() from exc
        except asyncio.CancelledError:
            asyncio.create_task(self._release_when_done(future))
            raise
        except Exception:
            await self._release()
            raise
        else:
            await self._release()
            return result

    async def _release_when_done(self, future: asyncio.Future[object]) -> None:
        await asyncio.gather(future, return_exceptions=True)
        await self._release()

    async def _release(self) -> None:
        async with self._lock:
            self._outstanding = max(0, self._outstanding - 1)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)
