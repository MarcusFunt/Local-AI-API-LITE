from __future__ import annotations

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
