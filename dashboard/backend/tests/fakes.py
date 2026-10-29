from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from app.secrets_store import KeyProtector, VaultLockedError


class FakeKeyProtector(KeyProtector):
    """Reversible test double that keeps fake wrapped keys distinguishable."""

    prefix = b"test-protected-key:"

    def __init__(self):
        self.fail_unprotect = False
        self.last_key: bytes | None = None

    def protect(self, plaintext: bytes) -> bytes:
        self.last_key = plaintext
        return self.prefix + bytes(byte ^ 0xA5 for byte in plaintext)

    def unprotect(self, protected: bytes) -> bytes:
        if self.fail_unprotect or not protected.startswith(self.prefix):
            raise VaultLockedError()
        return bytes(byte ^ 0xA5 for byte in protected[len(self.prefix) :])


@dataclass
class FakeCommandResult:
    return_code: int
    stdout: str


class FakeJobCommandAdapter:
    """Test-only bounded command adapter for the dashboard job runner."""

    def __init__(self) -> None:
        self.stdout = '{"ok":true,"health":"ok","model":"ddu-narrator","requests":2}'
        self.exit_code = 0
        self.block = False
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[dict] = []
        self.artifact_lines: list[str] = []

    def run(
        self,
        job_id: str,
        options: dict,
        *,
        artifact_path: Path,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> FakeCommandResult:
        self.calls.append({"job_id": job_id, "options": dict(options), "timeout_seconds": timeout_seconds})
        self.started.set()
        if self.block:
            while not self.release.wait(0.01):
                if cancel_event.is_set():
                    return FakeCommandResult(130, "{}")
        if self.artifact_lines:
            artifact_path.write_text("\n".join(self.artifact_lines) + "\n", encoding="utf-8")
        return FakeCommandResult(self.exit_code, self.stdout)
