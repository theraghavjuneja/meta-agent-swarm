"""
This file covers the layered exception hierarchy for the whole application
Deliberately divided into various set of errors
Layered hierarchy - Domain, Infra, Validation
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """
    Base class for every exception. 
    """
    
    default_http_status:int=500
    
    def __init__(self, message: str, *, code: str = "app_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code
 
    def __repr__(self) -> str:  
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"
 
    def to_dict(self) -> dict[str, Any]:
        """Minimal machine-readable shape, safe for logging or an API error body."""
        return {"code": self.code, "message": self.message}


class DomainError(AppError):
    default_http_status=409
    def __init__(
    self,
    message: str,
    *,
    code: str = "domain_error",
    http_status: int | None = None,
) -> None:
        super().__init__(message, code=code)
        if http_status is not None:
            self.default_http_status = http_status

class InfrastructureError(AppError):
    default_http_status = 502
 
    def __init__(
        self,
        message: str,
        *,
        code: str = "infrastructure_error",
        provider: str | None = None,
        operation: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.provider = provider
        self.operation = operation
        self.cause = cause
        if cause is not None:
            self.__cause__=cause

    
    @classmethod
    def wrap(
        cls,
        cause: BaseException,
        *,
        provider: str,
        operation: str,
        message: str | None = None,
        code: str = "infrastructure_error",
    ) -> InfrastructureError:
        """
        Build an infrastructure error from a raised exception
        """
        text = message or f"{provider}.{operation} failed: {cause}"
        return cls(text, code=code, provider=provider, operation=operation, cause=cause)

    def to_log_context(self) -> dict[str, Any]:

        return {
            "code": self.code,
            "provider": self.provider,
            "operation": self.operation,
            "cause_type": type(self.cause).__name__ if self.cause is not None else None,
            "cause_message": str(self.cause) if self.cause is not None else None,
        }


class ValidationError(AppError):
    """Input that fails validation before it should ever reach a workflow or
    service"""
    
    default_http_status=422
    
    def __init__(
    self,
    message: str = "Validation failed",
    *,
    code: str = "validation_error",
    fields: dict[str, list[str]] | None = None,
) -> None:
        super().__init__(message, code=code)
        self.fields = fields or {}
 
    @classmethod
    def from_field_errors(
        cls,
        errors: list[dict[str, Any]],
        *,
        message: str = "Validation failed",
        code: str = "validation_error",
    ) -> ValidationError:
        fields: dict[str, list[str]] = {}
        for err in errors:
            loc = err.get("loc", ())
            field = ".".join(str(part) for part in loc) or "__root__"
            msg = str(err.get("msg", "invalid"))
            fields.setdefault(field, []).append(msg)
        return cls(message, code=code, fields=fields)
 
    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["fields"] = self.fields
        return payload
