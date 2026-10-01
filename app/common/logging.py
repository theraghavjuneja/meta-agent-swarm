from __future__ import annotations

import json
import logging
import sys
from typing import Any

from app.common.context import get_correlation_id

_RESERVED_LOG_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))



class _CorrelationIdFilter(logging.Filter):
    """
    Stamps current correlation id with every record
    """
    
    
    def filter(self, record:logging.LogRecord)->bool:
        
        record.correlation_id = get_correlation_id()  # type: ignore[attr-defined]
        return True


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    
    exclude = _RESERVED_LOG_RECORD_ATTRS | {"correlation_id", "message", "asctime"}
    return {k: v for k, v in vars(record).items() if k not in exclude}
 
 
class _JsonFormatter(logging.Formatter):
    def format(self, record:logging.LogRecord)->str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)

class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        cid = getattr(record, "correlation_id", None) or "-"
        extra = _extra_fields(record)
        suffix = f" {extra}" if extra else ""
        base = (
            f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<8} "
            f"[{cid}] {record.name}: {record.getMessage()}{suffix}"
        )
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base

def configure_logging(*, json_format: bool = True, level: str | int = "INFO") -> None:
    """
    Configure the root logger. call once at process start
    """
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
 
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter() if json_format else _ConsoleFormatter())
    handler.addFilter(_CorrelationIdFilter())
    root.addHandler(handler)
    
    effective_level = root.getEffectiveLevel()
    for noisy in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, effective_level))
 
 
def get_logger(name: str) -> logging.Logger:
    """
    eturn a stdlib `Logger` for `name`.
 
    Once :func:`configure_logging` has run, every record emitted through
    this logger automatically carries the current correlation id -- no
    per-call-site plumbing required. Safe to call before
    `configure_logging()` too (e.g. at module import time); the logger just
    won't produce output until a handler exists on the root logger.

    """
    
    return logging.getLogger(name)


    