"""Shared database adapter for SQLite and PostgreSQL backends.

This module centralizes the small abstraction layer used by CacheInfinity to
support both SQLite (default) and PostgreSQL connections.  It wraps the
minimal SQL dialect differences (parameter style, AUTOINCREMENT syntax) and
provides helper methods for executing queries and fetching rows as dicts.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..core.errors import ConfigError
from .dbmanage import DatabaseSettings


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
        self._lock = threading.RLock()
        
        # Connection pooling for PostgreSQL
        self._pool_size = 5
        self._pool = []
        self._pool_lock = threading.Lock()
        self._pool_connections = 0
        
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
            # Initialize connection pool
            self._init_connection_pool()
        else:  # pragma: no cover - guarded by validation
            raise ConfigError(f"Unsupported database engine '{engine}'")

    def _init_connection_pool(self):
        """Initialize the connection pool for PostgreSQL."""
        if self.engine != "postgres":
            return
        
        try:
            for _ in range(self._pool_size):
                conn = self._psycopg.connect(self._postgres_dsn)
                conn.autocommit = False
                self._pool.append(conn)
        except Exception as exc:
            # If pool initialization fails, continue with single connection
            import logging
            logging.getLogger(__name__).warning("Failed to initialize connection pool: %s", exc)

    def _get_connection_from_pool(self):
        """Get a connection from the pool."""
        if self.engine != "postgres":
            return self._conn
            
        with self._pool_lock:
            if self._pool:
                return self._pool.pop()
            else:
                # Create a new connection if pool is empty
                return self._psycopg.connect(self._postgres_dsn)

    def _return_connection_to_pool(self, conn):
        """Return a connection to the pool."""
        if self.engine != "postgres" or not conn:
            return
            
        with self._pool_lock:
            if len(self._pool) < self._pool_size:
                try:
                    # Test the connection before returning to pool
                    cur = conn.cursor()
                    cur.execute("SELECT 1")
                    cur.close()
                    self._pool.append(conn)
                except Exception:
                    # Close broken connections
                    try:
                        conn.close()
                    except Exception:
                        pass
            else:
                # Pool is full, close the connection
                try:
                    conn.close()
                except Exception:
                    pass

    # Basic execution helpers -------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] | None = None):
        if self.engine == "postgres":
            # Use connection pool for PostgreSQL
            conn = self._get_connection_from_pool()
            try:
                cur = self._run_with_reconnect(lambda: conn.cursor(), conn)
                cur.execute(self._convert_sql(sql), params or ())
                return cur
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                self._return_connection_to_pool(conn)
        else:
            # Use single connection for SQLite
            return self._run_with_reconnect(lambda cur: cur.execute(self._convert_sql(sql), params or ()))

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]):
        if self.engine == "postgres":
            conn = self._get_connection_from_pool()
            try:
                cur = self._run_with_reconnect(lambda: conn.cursor(), conn)
                cur.executemany(self._convert_sql(sql), seq)
                return cur
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                self._return_connection_to_pool(conn)
        else:
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
        if self.engine == "postgres":
            # For pooled connections, we don't manage commit at this level
            # Each operation should handle its own transaction
            pass
        else:
            self._conn.commit()

    def rollback(self) -> None:
        if self.engine == "postgres":
            # For pooled connections, rollback is handled per-operation
            pass
        else:
            try:
                self._conn.rollback()
            except Exception:  # pragma: no cover - defensive
                pass

    def close(self) -> None:
        if self.engine == "postgres":
            # Close all pooled connections
            with self._pool_lock:
                for conn in self._pool:
                    try:
                        conn.close()
                    except Exception:
                        pass
                self._pool.clear()
            # Close the main connection
            try:
                self._conn.close()
            except Exception:
                pass
        else:
            self._conn.close()

    # Reconnect helpers -------------------------------------------------
    def _run_with_reconnect(self, func):
        """Execute a database operation with automatic reconnection for server resilience.

        This method implements a robust reconnection strategy suitable for server software:
        - Up to 50 attempts to handle transient network issues
        - Exponential backoff with max 5s delay between attempts
        - Automatic reconnection when recoverable errors occur
        """
        attempt = 0
        last_exc = None
        max_attempts = 50  # Server-grade resilience: 50 attempts
        while attempt < max_attempts:
            try:
                cur = self._cursor()
                result = func(cur)
                return result
            except self._recoverable_errors as exc:  # pragma: no cover - requires postgres
                last_exc = exc
                self.rollback()
                attempt += 1
                if attempt >= max_attempts:
                    raise
                self._reconnect()
                # Add a small delay between reconnect attempts with exponential backoff
                import time
                time.sleep(min(5.0, 0.1 * attempt))  # Max 5s delay, exponential growth
            except Exception:
                self.rollback()
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

    def ensure_connection(self) -> None:
        """Check if connection is alive and reconnect if needed."""
        if self.engine != "postgres":
            return
        try:
            # Test the connection with a simple query
            self.execute("SELECT 1")
        except self._recoverable_errors:
            self._reconnect()

    def health_check(self) -> bool:
        """Perform a comprehensive health check on the database connection."""
        try:
            if self.engine == "postgres":
                # Test with a simple query
                cur = self.execute("SELECT 1 as health_check")
                result = cur.fetchone()
                cur.close()
                return result is not None and result[0] == 1
            else:
                # SQLite health check
                cur = self.execute("SELECT 1 as health_check")
                result = cur.fetchone()
                cur.close()
                return result is not None and result[0] == 1
        except Exception:
            return False

    def get_pool_stats(self) -> dict:
        """Get connection pool statistics (PostgreSQL only)."""
        if self.engine != "postgres":
            return {"engine": "sqlite", "pool_size": 0, "available_connections": 0}
        
        with self._pool_lock:
            return {
                "engine": "postgres",
                "pool_size": self._pool_size,
                "available_connections": len(self._pool),
                "in_use_connections": self._pool_size - len(self._pool)
            }

    def close_idle_connections(self) -> None:
        """Close idle connections in the pool to prevent resource leaks."""
        if self.engine != "postgres":
            return
        
        with self._pool_lock:
            healthy_connections = []
            for conn in self._pool:
                try:
                    # Test each connection
                    cur = conn.cursor()
                    cur.execute("SELECT 1")
                    cur.close()
                    healthy_connections.append(conn)
                except Exception:
                    # Close broken connections
                    try:
                        conn.close()
                    except Exception:
                        pass
            self._pool = healthy_connections

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
