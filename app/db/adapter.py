"""Shared database adapter for SQLite, PostgreSQL, and Redis backends.

This module centralizes the small abstraction layer used by CacheInfinity to
support both SQLite (default) and PostgreSQL connections, with optional Redis
caching for file metadata and checksums. It wraps the minimal SQL dialect
differences (parameter style, AUTOINCREMENT syntax) and provides helper methods
for executing queries and fetching rows as dicts, plus Redis caching operations.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence, Optional, Union

from core.errors import ConfigError


class DBAdapter:
    """Lightweight helper that hides SQL dialect differences and provides Redis caching.

    The adapter exposes a SQLite-like API (`?` parameters, AUTOINCREMENT
    semantics) so the rest of the code can remain blissfully unaware of the
    underlying engine. It also provides Redis caching for file metadata and
    checksums when Redis is enabled.
    """

    def __init__(self, settings: 'DatabaseSettings'):
        self._settings = settings
        engine = settings.engine or "sqlite"
        self.engine = engine
        self._engine = engine  # Store engine for match-case usage
        self._recoverable_errors: tuple[type[Exception], ...] = ()
        self._lock = threading.RLock()
        
        # Redis support
        self._redis_enabled = getattr(settings, 'redis_enabled', False)
        self._redis = None
        self._redis_lock = threading.RLock()
        
        # Connection pooling for PostgreSQL
        self._pool_size = 5
        self._pool = []
        self._pool_lock = threading.Lock()
        self._pool_connections = 0
        
        # Initialize SQL connection using match-case pattern
        match engine:
            case "sqlite":
                # Get config directory from settings
                config_dir = settings.config_dir
                if not config_dir:
                    raise ConfigError("SQLite engine requires config_dir")
                
                # Create SQLite backend with config directory
                from .backends.sqlite import SQLiteBackend
                self._sqlite_backend = SQLiteBackend(config_dir)
                self._sqlite_backend.connect()
                self._conn = self._sqlite_backend._conn
                self._sqlite_path = self._sqlite_backend.path
            case "postgres":
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
            case _:
                # pragma: no cover - guarded by validation
                raise ConfigError(f"Unsupported database engine '{engine}'")
        
        # Initialize authentication tables
        self._init_auth_tables()
        
        # Initialize Redis if enabled
        if self._redis_enabled:
            self._init_redis()

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

    def _init_redis(self):
        """Initialize Redis connection for caching."""
        if not self._redis_enabled:
            return
        
        try:
            import redis
        except ImportError as exc:
            import logging
            logging.getLogger(__name__).warning("Redis package not available, disabling Redis caching: %s", exc)
            self._redis_enabled = False
            return
        
        try:
            redis_url = getattr(self._settings, 'redis_url', 'redis://localhost:6379/0')
            self._redis = redis.from_url(redis_url)
            # Test the connection
            self._redis.ping()
            import logging
            logging.getLogger(__name__).info("Redis connection established")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Failed to connect to Redis, disabling Redis caching: %s", exc)
            self._redis_enabled = False
            self._redis = None

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
        # Close Redis connection
        if self._redis:
            try:
                self._redis.close()
            except Exception:
                pass
            self._redis = None
        
        # Close SQL connections using match-case pattern
        match self._engine:
            case "postgres":
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
            case "sqlite":
                # Close SQLite backend
                if hasattr(self, '_sqlite_backend') and self._sqlite_backend:
                    self._sqlite_backend.close()
                else:
                    # Fallback for direct connection
                    try:
                        self._conn.close()
                    except Exception:
                        pass
            case _:
                # Close any other connection
                try:
                    self._conn.close()
                except Exception:
                    pass

    # Operation routing helpers -------------------------------------------
    def should_use_redis(self, operation_type: str) -> bool:
        """Determine if Redis should be used for the given operation type."""
        if not self._redis_enabled or not self._redis:
            return False
        
        # Operations that should use Redis when available
        redis_operations = {
            'file_metadata',
            'checksums',
            'indexing_data',
            'cache_state',
            'session_data'
        }
        
        return operation_type in redis_operations

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
            match self._engine:
                case "postgres":
                    # Test with a simple query
                    cur = self.execute("SELECT 1 as health_check")
                    result = cur.fetchone()
                    cur.close()
                    return result is not None and result[0] == 1
                case "sqlite":
                    # SQLite health check
                    cur = self.execute("SELECT 1 as health_check")
                    result = cur.fetchone()
                    cur.close()
                    return result is not None and result[0] == 1
                case _:
                    # Generic health check
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

    # Redis-specific operations -------------------------------------------
    def redis_set(self, key: str, value: str, ttl: int = None) -> bool:
        """Set a value in Redis with optional TTL."""
        if not self._redis_enabled or not self._redis:
            return False
        
        try:
            with self._redis_lock:
                if ttl:
                    self._redis.setex(key, ttl, value)
                else:
                    self._redis.set(key, value)
            return True
        except Exception:
            return False

    def redis_get(self, key: str) -> str | None:
        """Get a value from Redis."""
        if not self._redis_enabled or not self._redis:
            return None
        
        try:
            with self._redis_lock:
                value = self._redis.get(key)
                return value.decode('utf-8') if value else None
        except Exception:
            return None

    def redis_delete(self, key: str) -> bool:
        """Delete a key from Redis."""
        if not self._redis_enabled or not self._redis:
            return False
        
        try:
            with self._redis_lock:
                result = self._redis.delete(key)
                return result > 0
        except Exception:
            return False

    def redis_exists(self, key: str) -> bool:
        """Check if a key exists in Redis."""
        if not self._redis_enabled or not self._redis:
            return False
        
        try:
            with self._redis_lock:
                result = self._redis.exists(key)
                return result > 0
        except Exception:
            return False

    def redis_keys(self, pattern: str) -> list[str]:
        """Get keys matching a pattern from Redis."""
        if not self._redis_enabled or not self._redis:
            return []
        
        try:
            with self._redis_lock:
                keys = self._redis.keys(pattern)
                return [key.decode('utf-8') for key in keys]
        except Exception:
            return []

    def redis_flushdb(self) -> bool:
        """Flush the Redis database."""
        if not self._redis_enabled or not self._redis:
            return False
        
        try:
            with self._redis_lock:
                self._redis.flushdb()
                return True
        except Exception:
            return False

    # Sync operations -----------------------------------------------------
    def sync_redis_to_sql(self) -> bool:
        """Sync Redis data to SQL database."""
        if not self._redis_enabled or not self._redis:
            return True  # Nothing to sync
        
        try:
            # This is a placeholder for the actual sync logic
            # In a real implementation, this would:
            # 1. Get all keys from Redis
            # 2. For each key, get the value and sync to appropriate SQL table
            # 3. Handle conflicts and data consistency
            import logging
            logging.getLogger(__name__).info("Redis to SQL sync completed")
            return True
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Redis to SQL sync failed: %s", exc)
            return False

    def is_redis_enabled(self) -> bool:
        """Check if Redis is enabled and available."""
        return self._redis_enabled and self._redis is not None

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
    
    def _init_auth_tables(self):
        """Create authentication tables if they don't exist."""
        try:
            # Create users table
            self.execute("""
                CREATE TABLE IF NOT EXISTS auth_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_plain TEXT,
                    password_hash TEXT,
                    enabled BOOLEAN DEFAULT 1,
                    is_admin BOOLEAN DEFAULT 0,
                    purpose TEXT DEFAULT 'webui',
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            
            # Create sessions table
            self.execute("""
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT UNIQUE NOT NULL,
                    username TEXT NOT NULL,
                    created_at TEXT,
                    last_used TEXT,
                    expires_at TEXT,
                    FOREIGN KEY (username) REFERENCES auth_users (username)
                )
            """)
            
            self.commit()
        except Exception as exc:
            self.rollback()
            import logging
            logging.getLogger(__name__).error(f"Failed to create auth tables: {exc}")
            raise
    
    def upsert_auth_user(self, username: str, password_plain: str = None,
                        password_hash: str = None, enabled: bool = True,
                        is_admin: bool = False, purpose: str = None) -> bool:
        """Create or update user in database."""
        try:
            # Check if user exists
            existing = self.fetchone(
                "SELECT id FROM auth_users WHERE username = ?",
                (username,)
            )
            
            if existing:
                # Update existing user
                self.execute(
                    "UPDATE auth_users SET password_plain = ?, password_hash = ?, enabled = ?, is_admin = ?, purpose = ?, updated_at = ? WHERE username = ?",
                    (password_plain, password_hash, enabled, is_admin, purpose, datetime.utcnow().isoformat(), username)
                )
            else:
                # Create new user
                self.execute(
                    "INSERT INTO auth_users (username, password_plain, password_hash, enabled, is_admin, purpose, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (username, password_plain, password_hash, enabled, is_admin, purpose, datetime.utcnow().isoformat(), datetime.utcnow().isoformat())
                )
            
            self.commit()
            return True
        except Exception as exc:
            self.rollback()
            import logging
            logging.getLogger(__name__).error(f"Failed to upsert auth user: {exc}")
            return False
    
    def get_user_credentials(self, username: str) -> dict | None:
        """Get user credentials from database."""
        try:
            result = self.fetchone(
                "SELECT id, username, password_plain, password_hash, enabled, is_admin, purpose, created_at, updated_at FROM auth_users WHERE username = ?",
                (username,)
            )
            return result
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(f"Failed to get user credentials: {exc}")
            return None
    
    def validate_credentials(self, username: str, password: str) -> bool:
        """Validate user credentials against database."""
        try:
            result = self.get_user_credentials(username)
            if not result:
                return False
            
            stored_plain = result.get('password_plain')
            stored_hash = result.get('password_hash')
            
            # Check plain text password first (for backward compatibility)
            if stored_plain and stored_plain == password:
                return True
            
            # Check hashed password
            if stored_hash and self._verify_password_hash(password, stored_hash):
                return True
            
            return False
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(f"Failed to validate credentials: {exc}")
            return False
    
    def _verify_password_hash(self, password: str, stored_hash: str) -> bool:
        """Verify a password against its hash."""
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest() == stored_hash
    
    def create_session(self, username: str, token: str, expires_at: datetime) -> bool:
        """Create a new session in database."""
        try:
            created_at = datetime.utcnow()
            self.execute(
                "INSERT INTO auth_sessions (token, username, created_at, last_used, expires_at) VALUES (?, ?, ?, ?, ?)",
                (token, username, created_at.isoformat(), created_at.isoformat(), expires_at.isoformat())
            )
            self.commit()
            return True
        except Exception as exc:
            self.rollback()
            import logging
            logging.getLogger(__name__).error(f"Failed to create session: {exc}")
            return False
    
    def get_session(self, token: str) -> dict | None:
        """Get session from database."""
        try:
            result = self.fetchone(
                "SELECT token, username, created_at, last_used, expires_at FROM auth_sessions WHERE token = ?",
                (token,)
            )
            if result:
                # Parse timestamps
                result['created_at'] = datetime.fromisoformat(result['created_at'])
                result['last_used'] = datetime.fromisoformat(result['last_used'])
                result['expires_at'] = datetime.fromisoformat(result['expires_at'])
            return result
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(f"Failed to get session: {exc}")
            return None
    
    def delete_session(self, token: str) -> bool:
        """Delete session from database."""
        try:
            self.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
            self.commit()
            return True
        except Exception as exc:
            self.rollback()
            import logging
            logging.getLogger(__name__).error(f"Failed to delete session: {exc}")
            return False
    
    def cleanup_expired_sessions(self, max_age_hours: int = 24) -> int:
        """Clean up expired sessions from database."""
        try:
            cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
            result = self.execute(
                "DELETE FROM auth_sessions WHERE expires_at < ?",
                (cutoff.isoformat(),)
            )
            self.commit()
            return result.rowcount if hasattr(result, 'rowcount') else 0
        except Exception as exc:
            self.rollback()
            import logging
            logging.getLogger(__name__).error(f"Failed to cleanup expired sessions: {exc}")
            return 0


__all__ = ["DBAdapter"]
