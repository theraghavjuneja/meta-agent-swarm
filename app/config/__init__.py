"""Public surface of app.config.

Every other package should do:

    from app.config import get_settings
    settings = get_settings()

rather than constructing `Settings()` itself, so the process has exactly one
validated settings instance.
"""

from functools import lru_cache

from app.config.settings import Settings

__all__ = ["Settings", "get_settings"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:

    return Settings()