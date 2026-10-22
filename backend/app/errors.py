from __future__ import annotations


class AppError(RuntimeError):
    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        *,
        retryable: bool = False,
        details: dict | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.retryable = retryable
        self.details = details

