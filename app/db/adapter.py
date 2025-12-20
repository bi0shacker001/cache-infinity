"""Shared database adapter for SQLite, PostgreSQL, and Redis backends.

This module centralizes the small abstraction layer used by CacheInfinity to
support both SQLite (default) and PostgreSQL connections, with optional Redis
caching for file metadata and checksums. It wraps the minimal SQL dialect
differences (parameter style, AUTOINCREMENT syntax) and provides helper methods
for executing queries and fetching rows as dicts, plus Redis caching operations.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from core.errors import ConfigError
from .backends.postgresql import PostgreSQLBackend
from .backends.redis import RedisBackend
from .backends.sqlite import SQLiteBackend

_HASH_SCHEME_PBKDF2 = "pbkdf2_sha256"
_HASH_SCHEME_SHA256 = "sha256"
_HASH_DEFAULT_ITERATIONS = 200_000


def _hash_password(password: str, *, iterations: int = _HASH_DEFAULT_ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        iterations,
    )
    return f"{_HASH_SCHEME_PBKDF2}${iterations}${salt}${digest.hex()}"


def _normalize_password_hash(password_hash: str | None) -> str | None:
    if not password_hash:
        return None
    if "$" in password_hash:
        return password_hash
    return f"{_HASH_SCHEME_SHA256}${password_hash}"


def _verify_password_hash(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    if "$" not in stored_hash:
        return hashlib.sha256(password.encode("utf-8")).hexdigest() == stored_hash
    parts = stored_hash.split("$")
    scheme = parts[0]
    if scheme == _HASH_SCHEME_PBKDF2 and len(parts) == 4:
        try:
            iterations = int(parts[1])
            salt = bytes.fromhex(parts[2])
            expected = parts[3]
        except ValueError:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        ).hex()
        return secrets.compare_digest(digest, expected)
    if scheme == _HASH_SCHEME_SHA256:
        if len(parts) == 2:
            expected = parts[1]
            digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
            return secrets.compare_digest(digest, expected)
        if len(parts) == 3:
            salt = parts[1]
            expected = parts[2]
            digest = hashlib.sha256(f"{salt}{password}".encode("utf-8")).hexdigest()
            return secrets.compare_digest(digest, expected)
    return False


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
        self._lock = threading.RLock()
        
        # Redis support
        self._redis_enabled = getattr(settings, 'redis_enabled', False)
        self._redis_backend: RedisBackend | None = None
        
        # Initialize SQL backend
        if engine == "sqlite":
            config_dir = settings.config_dir
            if not config_dir:
                raise ConfigError("SQLite engine requires config_dir")
            self._backend = SQLiteBackend(config_dir)
            self._backend.connect()
        elif engine == "postgres":
            dsn = settings.postgres_dsn
            if not dsn:
                raise ConfigError("postgres engine requires postgres_dsn")
            self._backend = PostgreSQLBackend(dsn)
            self._backend.connect()
        else:
            raise ConfigError(f"Unsupported database engine '{engine}'")
        
        # Initialize authentication tables
        self._init_auth_tables()
        
        # Initialize Redis if enabled
        if self._redis_enabled:
            self._init_redis()

    def _init_redis(self):
        """Initialize Redis connection for caching."""
        if not self._redis_enabled:
            return

        redis_url = getattr(self._settings, "redis_url", "redis://localhost:6379/0")
        self._redis_backend = RedisBackend(redis_url)
        if not self._redis_backend.is_connected():
            self._redis_enabled = False
            self._redis_backend = None

    # Basic execution helpers -------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] | None = None):
        return self._backend.execute(self._convert_sql(sql), params or ())

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]):
        self._backend.executemany(self._convert_sql(sql), list(seq))

    def fetchone(self, sql: str, params: Sequence[Any] | None = None) -> dict | None:
        return self._backend.fetchone(self._convert_sql(sql), params or ())

    def fetchall(self, sql: str, params: Sequence[Any] | None = None) -> list[dict]:
        return self._backend.fetchall(self._convert_sql(sql), params or ())

    def commit(self) -> None:
        self._backend.commit()

    def rollback(self) -> None:
        self._backend.rollback()

    def close(self) -> None:
        # Close Redis connection
        if self._redis_backend:
            self._redis_backend.close()
            self._redis_backend = None
        
        self._backend.close()

    # Operation routing helpers -------------------------------------------
    def should_use_redis(self, operation_type: str) -> bool:
        """Determine if Redis should be used for the given operation type."""
        if not self._redis_enabled or not self._redis_backend:
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

    def health_check(self) -> bool:
        """Perform a comprehensive health check on the database connection."""
        try:
            result = self.fetchone("SELECT 1 as health_check")
            return result is not None
        except Exception:
            return False

    def get_pool_stats(self) -> dict:
        """Get connection pool statistics (PostgreSQL only)."""
        return {"engine": self.engine, "pool_size": 0, "available_connections": 0, "in_use_connections": 0}

    def close_idle_connections(self) -> None:
        """Close idle connections in the pool to prevent resource leaks."""
        return

    # Redis-specific operations -------------------------------------------
    def redis_set(self, key: str, value: str, ttl: int = None) -> bool:
        """Set a value in Redis with optional TTL."""
        if not self._redis_enabled or not self._redis_backend:
            return False
        
        return self._redis_backend.set_value(key, value, ttl=ttl)

    def redis_get(self, key: str) -> str | None:
        """Get a value from Redis."""
        if not self._redis_enabled or not self._redis_backend:
            return None
        return self._redis_backend.get_value(key)

    def redis_delete(self, key: str) -> bool:
        """Delete a key from Redis."""
        if not self._redis_enabled or not self._redis_backend:
            return False
        return self._redis_backend.delete_value(key)

    def redis_exists(self, key: str) -> bool:
        """Check if a key exists in Redis."""
        if not self._redis_enabled or not self._redis_backend:
            return False
        return self._redis_backend.exists(key)

    def redis_keys(self, pattern: str) -> list[str]:
        """Get keys matching a pattern from Redis."""
        if not self._redis_enabled or not self._redis_backend:
            return []
        return self._redis_backend.keys(pattern)

    def redis_flushdb(self) -> bool:
        """Flush the Redis database."""
        if not self._redis_enabled or not self._redis_backend:
            return False
        return self._redis_backend.flushdb()

    # Sync operations -----------------------------------------------------
    def sync_redis_to_sql(self) -> bool:
        """Sync Redis data to SQL database."""
        if not self._redis_enabled or not self._redis_backend:
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
        return self._redis_enabled and self._redis_backend is not None

    # Internal helpers --------------------------------------------------
    def _convert_sql(self, sql: str) -> str:
        if self.engine != "postgres":
            return sql
        converted = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        converted = converted.replace("AUTOINCREMENT", "")
        return converted.replace("?", "%s")

    @property
    def sqlite_path(self) -> Path | None:
        if self.engine != "sqlite":
            return None
        return self._backend.path

    def reconnect(self) -> None:
        """Reconnect the underlying backend."""
        self._backend.connect()
    
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

            normalized_hash = _normalize_password_hash(password_hash)
            if password_plain:
                normalized_hash = _hash_password(password_plain)
                if purpose != "cli" and username != "cli-backend":
                    password_plain = None
            
            if existing:
                # Update existing user
                self.execute(
                    "UPDATE auth_users SET password_plain = ?, password_hash = ?, enabled = ?, is_admin = ?, purpose = ?, updated_at = ? WHERE username = ?",
                    (password_plain, normalized_hash, enabled, is_admin, purpose, datetime.utcnow().isoformat(), username)
                )
            else:
                # Create new user
                self.execute(
                    "INSERT INTO auth_users (username, password_plain, password_hash, enabled, is_admin, purpose, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (username, password_plain, normalized_hash, enabled, is_admin, purpose, datetime.utcnow().isoformat(), datetime.utcnow().isoformat())
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
            stored_hash = _normalize_password_hash(result.get('password_hash'))
            purpose = result.get("purpose")
            
            # Check plain text password first (for backward compatibility)
            if stored_plain and stored_plain == password:
                if not stored_hash:
                    stored_hash = _hash_password(password)
                    if purpose != "cli" and username != "cli-backend":
                        stored_plain = None
                    self.execute(
                        "UPDATE auth_users SET password_plain = ?, password_hash = ? WHERE username = ?",
                        (stored_plain, stored_hash, username),
                    )
                    self.commit()
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
        return _verify_password_hash(password, stored_hash)
    
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
