from __future__ import annotations


class ServiceError(RuntimeError):
    """A sanitized error category safe to return from the consumer API."""

    def __init__(self, status_code: int, category: str) -> None:
        super().__init__(category)
        self.status_code = status_code
        self.category = category
