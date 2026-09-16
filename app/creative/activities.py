"""Temporal activity entry point for Module 5.

ASSUMED INTERFACES (Modules 1-4), same caveat as service.py:

    app.db.session_scope() -> async context manager yielding an AsyncSession,
        committing on clean exit and rolling back on exception.

Both DomainError (corrective retries exhausted) and InfrastructureError (a provider
call inside the adapter failing outright) are allowed to propagate uncaught —
Temporal's RetryPolicy, configured later in the workflows module, decides what
happens next. This activity does not catch either.
"""

from __future__ import annotations

from temporalio import activity

from app.creative import service
from app.creative.dto import GenerateSpecInput, GenerateSpecOutput
from app.db import session_scope

__all__ = ["generate_creative_spec"]


@activity.defn
async def generate_creative_spec(spec_input: GenerateSpecInput) -> GenerateSpecOutput:
    async with session_scope() as session:
        return await service.generate_creative_spec(session, spec_input)