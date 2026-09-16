"""Campaign Studio - app.campaigns

Owns the root aggregate of the pipeline (``campaigns``) plus the two
cross-cutting logs that didn't exist before this module: the append-only
``stage_events`` audit trail and the ``provider_usage`` ledger.

This package intentionally contains no Temporal Workflow code and no API
routers - those belong to Module 8 (workflows) and Module 9 (api). It does
own small Temporal Activity wrappers (``activities.py``) so that the future
orchestrating workflow never touches the database directly, matching the
pattern already established by research/creative/assets.
"""