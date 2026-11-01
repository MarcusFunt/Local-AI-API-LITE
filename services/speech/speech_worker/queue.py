from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import TypeVar

from .contracts import QueueFullError

T = TypeVar("T")


class InferenceQueue:
    """A single CPU inference thread with one bounded waiting request."""

    def __init__(self, *, max_outstanding: int, timeout_seconds: float):
        self.max_outstanding = max_outstanding
        self.timeout_seconds = timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="speech-inference")
        self._lock = asyncio.Lock()
        self._metrics_lock = threading.Lock()
        self._outstanding = 0
        self._active_requests = 0
        self._accepted_total = 0
        self._completed_total = 0
        self._failed_total = 0
        self._timed_out_total = 0
        self._rejected_total = 0
        self._stuck_inference = False
        self._last_successful_inference_at: str | None = None
        self._closed = False

    def snapshot(self) -> dict[str, int | bool | str | None]:
        with self._metrics_lock:
            active = self._active_requests
            outstanding = self._outstanding
            return {
                "status": "available",
                "accepted_total": self._accepted_total,
                "completed_total": self._completed_total,
                "failed_total": self._failed_total,
                "timed_out_total": self._timed_out_total,
                "rejected_total": self._rejected_total,
                "active_requests": active,
                "queued_requests": max(0, outstanding - active),
                "outstanding_requests": outstanding,
                "max_outstanding": self.max_outstanding,
                "saturated": outstanding >= self.max_outstanding,
                "stuck_inference": self._stuck_inference,
                "last_successful_inference_at": self._last_successful_inference_at,
                "closed": self._closed,
            }

    async def run(self, call: Callable[[], T]) -> T:
        async with self._lock:
            if self._closed or self._outstanding >= self.max_outstanding:
                with self._metrics_lock:
                    self._rejected_total += 1
                raise QueueFullError()
            self._outstanding += 1
            with self._metrics_lock:
                self._accepted_total += 1

        loop = asyncio.get_running_loop()
        started = threading.Event()

        def invoke() -> T:
            started.set()
            with self._metrics_lock:
                self._active_requests += 1
            try:
                return call()
            finally:
                with self._metrics_lock:
                    self._active_requests = max(0, self._active_requests - 1)
                    if self._active_requests == 0:
                        self._stuck_inference = False

        future = loop.run_in_executor(self._executor, invoke)
        try:
            result = await asyncio.wait_for(asyncio.shield(future), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            with self._metrics_lock:
                self._failed_total += 1
                self._timed_out_total += 1
                self._stuck_inference = started.is_set()
            asyncio.create_task(self._release_when_done(future))
            raise TimeoutError() from exc
        except asyncio.CancelledError:
            with self._metrics_lock:
                self._failed_total += 1
            asyncio.create_task(self._release_when_done(future))
            raise
        except Exception:
            with self._metrics_lock:
                self._failed_total += 1
            await self._release()
            raise
        else:
            with self._metrics_lock:
                self._completed_total += 1
                self._last_successful_inference_at = (
                    datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
                )
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
