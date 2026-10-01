"""
correlation id propagation via contextvars

"""
from __future__ import annotations

import contextvars
import uuid
from collections.abc import Iterator

from contextlib import contextmanager

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)



def get_correlation_id()->str|None:
    """Return the correlation id bound to the current execution context, or
    `None` if nothing has set one yet (e.g. logs emitted before any campaign
    exists). Callers must treat `None` as a normal, expected value.
    """
    return _correlation_id.get()
def set_correlation_id(correlation_id: str) -> contextvars.Token[str | None]:
    """
    
    Set correlation id
    """
    return _correlation_id.set(correlation_id)

def reset_correlation_id(token: contextvars.Token[str | None]) -> None:
    """Restore the correlation id to what it was before the matching
    :func:`set_correlation_id` call."""
    _correlation_id.reset(token)



@contextmanager
def correlation_id_scope(correlation_id: str) -> Iterator[str]:
    token = set_correlation_id(correlation_id)
    try:
        yield correlation_id
    finally:
        reset_correlation_id(token)
 
 
@contextmanager
def campaign_id_scope(campaign_id: str) -> Iterator[str]:
    with correlation_id_scope(campaign_id) as cid:
        yield cid

def new_correlation_id() -> str:
    """Generate a fresh correlation id, for contexts with no natural id yet
    (e.g. a request that hasn't created a campaign)."""
    return str(uuid.uuid4())
