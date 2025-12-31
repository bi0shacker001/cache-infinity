"""Shared database adapter for SQLite and PostgreSQL backends.

This module centralizes the small abstraction layer used by CacheInfinity to
support both SQLite (default) and PostgreSQL connections. It wraps the minimal SQL dialect
differences (parameter style, AUTOINCREMENT syntax) and provides helper methods
for executing queries and fetching rows as dicts.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from core.errors import ConfigError
from .backends.mariadb import MariaDBBackend
from .backends.postgresql import PostgreSQLBackend
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
    """Lightweight helper that hides SQL dialect differences.

    The adapter exposes a SQLite-like API (`?` parameters, AUTOINCREMENT
    semantics) so the rest of the code can remain blissfully unaware of the
    underlying engine.
    """

    def __init__(self, settings: 'DatabaseSettings'):
        self._settings = settings
        engine = settings.engine or "sqlite"
        self.engine = engine
        self._lock = threading.RLock()
        
        # Initialize SQL backend
        if engine == "sqlite":
            sqlite_path = settings.sqlite_path
            if not sqlite_path:
                raise ConfigError("SQLite engine requires sqlite_path")
            self._backend = SQLiteBackend(sqlite_path)
            self._backend.connect()
        elif engine == "postgres":
            dsn = settings.database_url
            if not dsn:
                raise ConfigError("postgres engine requires database_url")
            self._backend = PostgreSQLBackend(dsn)
            self._backend.connect()
        elif engine == "mariadb":
            dsn = settings.database_url
            if not dsn:
                raise ConfigError("mariadb engine requires database_url")
            self._backend = MariaDBBackend(dsn)
            self._backend.connect()
        else:
            raise ConfigError(f"Unsupported database engine '{engine}'")
        
        # Initialize authentication tables
        self._init_auth_tables()
        


    # Basic execution helpers -------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] | None = None):
        # Use backend-specific SQL conversion if available
        if hasattr(self._backend, 'convert_sql'):
            sql = self._backend.convert_sql(sql)
        return self._backend.execute(sql, params or ())

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]):
        if hasattr(self._backend, 'convert_sql'):
            sql = self._backend.convert_sql(sql)
        self._backend.executemany(sql, list(seq))

    def fetchone(self, sql: str, params: Sequence[Any] | None = None) -> dict | None:
        if hasattr(self._backend, 'convert_sql'):
            sql = self._backend.convert_sql(sql)
        return self._backend.fetchone(sql, params or ())

    def fetchall(self, sql: str, params: Sequence[Any] | None = None) -> list[dict]:
        if hasattr(self._backend, 'convert_sql'):
            sql = self._backend.convert_sql(sql)
        return self._backend.fetchall(sql, params or ())

    def commit(self) -> None:
        self._backend.commit()

    def rollback(self) -> None:
        self._backend.rollback()

    def close(self) -> None:
        self._backend.close()


    def health_check(self) -> bool:
        """Perform a comprehensive health check on the database connection."""
        try:
            result = self.fetchone("SELECT 1 as health_check")
            return result is not None
        except Exception:
            return False

    def get_pool_stats(self) -> dict:
        """Get connection pool statistics."""
        try:
            return self._backend.get_pool_stats()
        except Exception:
            return {"engine": self.engine, "pool_size": 0, "available_connections": 0, "in_use_connections": 0}

    def close_idle_connections(self) -> None:
        """Close idle connections in the pool to prevent resource leaks."""
        return




    # Internal helpers --------------------------------------------------
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
    
    def get_user_credentials(self, username: str, *, purpose: str = "webui") -> dict | None:
        """Get user credentials from database."""
        try:
            result = self.fetchone(
                "SELECT id, username, password_plain, password_hash, enabled, is_admin, purpose, created_at, updated_at FROM auth_users WHERE username = ? AND purpose = ?",
                (username, purpose)
            )
            return result
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(f"Failed to get user credentials: {exc}")
            return None
    
    def validate_credentials(self, username: str, password: str, *, purpose: str = "webui") -> bool:
        """Validate user credentials against database."""
        try:
            result = self.get_user_credentials(username, purpose=purpose)
            if not result:
                return False
            if not result.get("enabled"):
                return False

            stored_plain = result.get('password_plain')
            stored_hash = _normalize_password_hash(result.get('password_hash'))
            
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
