from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class RuntimeSnapshot:
    started_at: str
    last_success_at: str | None
    last_error_at: str | None
    last_error: str | None
    messages_total: int
    requests_total: int
    active_requests: int


class BridgeRuntimeState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = utc_now_iso()
        self._last_success_at: str | None = None
        self._last_error_at: str | None = None
        self._last_error: str | None = None
        self._messages_total = 0
        self._requests_total = 0
        self._active_requests = 0

    def record_message(self) -> None:
        with self._lock:
            self._messages_total += 1

    def request_started(self) -> None:
        with self._lock:
            self._requests_total += 1
            self._active_requests += 1

    def request_succeeded(self) -> None:
        with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            self._last_success_at = utc_now_iso()

    def request_failed(self, error: str) -> None:
        with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            self._last_error_at = utc_now_iso()
            self._last_error = error

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return RuntimeSnapshot(
                started_at=self._started_at,
                last_success_at=self._last_success_at,
                last_error_at=self._last_error_at,
                last_error=self._last_error,
                messages_total=self._messages_total,
                requests_total=self._requests_total,
                active_requests=self._active_requests,
            )
