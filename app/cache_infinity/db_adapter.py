"""Shared database adapter for SQLite and PostgreSQL backends.

This module centralizes the small abstraction layer used by CacheInfinity to
support both SQLite (default) and PostgreSQL connections.  It wraps the
minimal SQL dialect differences (parameter style, AUTOINCREMENT syntax) and
provides helper methods for executing queries and fetching rows as dicts.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import ConfigError, DatabaseSettings


class DBAdapter:
    """Lightweight helper that hides SQL dialect differences.

    The adapter exposes a SQLite-like API (`?` parameters, AUTOINCREMENT
    semantics) so the rest of the code can remain blissfully unaware of the
    underlying engine.
    """

    def __init__(self, settings: DatabaseSettings):
        self._settings = settings
        engine = settings.engine or "sqlite"
        self.engine = engine
        self._recoverable_errors: tuple[type[Exception], ...] = ()
        if engine == "sqlite":
            sqlite_path = settings.sqlite_path or Path("cacheinfinity.db")
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            self._sqlite_path = sqlite_path
            self._conn = sqlite3.connect(sqlite_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        elif engine == "postgres":
            dsn = settings.postgres_dsn
            if not dsn:
                raise ConfigError("postgres engine requires postgres_dsn")
            try:  # pragma: no cover - optional dependency
                import psycopg
            except ImportError as exc:  # pragma: no cover
                raise ConfigError("psycopg package is required for postgres engine") from exc
            self._psycopg = psycopg
            self._postgres_dsn = dsn
            self._conn = psycopg.connect(dsn)
            self._conn.autocommit = False
            self._recoverable_errors = (psycopg.OperationalError, psycopg.InterfaceError)
        else:  # pragma: no cover - guarded by validation
            raise ConfigError(f"Unsupported database engine '{engine}'")

    # Basic execution helpers -------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] | None = None):
        return self._run_with_reconnect(lambda cur: cur.execute(self._convert_sql(sql), params or ()))

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]):
        def _run(cur):
            cur.executemany(self._convert_sql(sql), seq)
            return cur

        cur = self._run_with_reconnect(_run)
        cur.close()

    def fetchone(self, sql: str, params: Sequence[Any] | None = None) -> dict | None:
        cur = self.execute(sql, params)
        row = cur.fetchone()
        description = cur.description
        cur.close()
        return self._row_to_dict(row, description)

    def fetchall(self, sql: str, params: Sequence[Any] | None = None) -> list[dict]:
        cur = self.execute(sql, params)
        description = cur.description
        rows = cur.fetchall()
        cur.close()
        return [self._row_to_dict(row, description) for row in rows]

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        except Exception:  # pragma: no cover - defensive
            pass

    def close(self) -> None:
        self._conn.close()

    # Reconnect helpers -------------------------------------------------
    def _run_with_reconnect(self, func):
        attempt = 0
        last_exc = None
        while attempt < 2:
            try:
                cur = self._cursor()
                result = func(cur)
                return result
            except self._recoverable_errors as exc:  # pragma: no cover - requires postgres
                last_exc = exc
                attempt += 1
                if attempt >= 2:
                    raise
                self._reconnect()
            except Exception:
                raise
        if last_exc:
            raise last_exc

    def _cursor(self):
        return self._conn.cursor()

    def _reconnect(self) -> None:
        if self.engine != "postgres":
            return
        self.close()
        self._conn = self._psycopg.connect(self._postgres_dsn)
        self._conn.autocommit = False

    # Internal helpers --------------------------------------------------
    def _convert_sql(self, sql: str) -> str:
        if self.engine != "postgres":
            return sql
        converted = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        converted = converted.replace("AUTOINCREMENT", "")
        return converted.replace("?", "%s")

    def _row_to_dict(self, row, description) -> dict | None:
        if row is None:
            return None
        if self.engine == "sqlite":
            return dict(row)
        columns = [col.name for col in description]
        return {col: value for col, value in zip(columns, row)}


__all__ = ["DBAdapter"]
