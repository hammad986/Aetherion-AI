"""
db — Provider-agnostic database access layer for Aetherion AI.

Public API
----------
get_store   : Factory function; returns a ``BaseStore`` instance backed by
              PostgreSQL (via ``FallbackStore``) or SQLite, depending on
              POSTGRES_MODE / DATABASE_URL environment variables.
BaseStore   : Abstract base class that all store backends implement.
              Use for type annotations: ``def foo(store: BaseStore) -> None``.

Usage
-----
    from db import get_store

    store = get_store()                         # default: sessions.db
    store = get_store("my_feature.db")          # explicit SQLite path

    with store:
        row = store.fetchone("SELECT 1 AS x")
        # auto-commits on __exit__, rolls back on exception
"""

from db.store_factory import get_store
from db.base_store import BaseStore

__all__ = [
    "get_store",
    "BaseStore",
]
