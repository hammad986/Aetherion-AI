"""
db/base_store.py — Abstract Store Interface
===========================================
Provider-agnostic database access contract.
SQLite and PostgreSQL implementations both satisfy this interface.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseStore(ABC):
    """Minimal contract that every store backend must satisfy."""

    @abstractmethod
    def execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute a single SQL statement, return cursor/result."""

    @abstractmethod
    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict]:
        """Return a single row as a dict, or None."""

    @abstractmethod
    def fetchall(self, sql: str, params: tuple = ()) -> List[Dict]:
        """Return all rows as list of dicts."""

    @abstractmethod
    def commit(self) -> None:
        """Commit the current transaction."""

    @abstractmethod
    def rollback(self) -> None:
        """Roll back the current transaction."""

    @abstractmethod
    def close(self) -> None:
        """Release connection resources."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False
