"""Authentication management for CacheInfinity."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

# Import necessary components for SSH key management
# Note: We should not import db.adapter directly - use database management service instead
# Use absolute imports as required by project standards
from core.config import FTPConfig # Absolute import for config
from db.dbmanage import DatabaseManager # Use database management service instead

_logger = logging.getLogger(__name__)


# Password hashing constants
_HASH_SCHEME_PBKDF2 = "pbkdf2_sha256"
_HASH_SCHEME_SHA256 = "sha256"
_HASH_DEFAULT_ITERATIONS = 200_000


def _hash_password(password: str, *, iterations: int = _HASH_DEFAULT_ITERATIONS) -> str:
    """Hash a password using PBKDF2."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        iterations,
    )
    return f"{_HASH_SCHEME_PBKDF2}${iterations}${salt}${digest.hex()}"


def _normalize_password_hash(password_hash: str | None) -> str | None:
    """Normalize password hash format."""
    if not password_hash:
        return None
    if "$" in password_hash:
        return password_hash
    return f"{_HASH_SCHEME_SHA256}${password_hash}"


def _verify_password_hash(password: str, stored_hash: str) -> bool:
    """Verify a password against its hash."""
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


@dataclass
class SessionToken:
    """Session token for WebUI authentication."""
    token: str
    username: str
    created_at: datetime
    last_used: datetime
    expires_at: datetime

    def is_valid(self) -> bool:
        """Check if the session token is still valid."""
        return datetime.utcnow() < self.expires_at

    def update_last_used(self) -> None:
        """Update the last used timestamp."""
        self.last_used = datetime.utcnow()


# --- User SSH Key Management ---

class UserSSHKeyManager:
    """Manager for user SSH public keys stored in the database."""

    def __init__(self, db_manager: IndexDatabaseManager):
        """Initialize UserSSHKeyManager.

        Args:
            db_manager: Database management service for credential storage.
        """
        self.db_manager = db_manager
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize database schema for user SSH public keys."""
        try:
            self.db_manager.execute(
                """
                CREATE TABLE IF NOT EXISTS user_ssh_public_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    key_type TEXT NOT NULL,
                    key_data TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, key_type)
                )
                """
            )
            self.db_manager.commit()
            _logger.info("User SSH public keys table initialized")
        except Exception as e:
            _logger.error(f"Failed to initialize user SSH public keys schema: {e}")
            self.db_manager.rollback()

    def save_user_ssh_key(self, user_id: str, key_type: str, key_data: str, fingerprint: str) -> bool:
        """Save a user's SSH public key to the database.

        Args:
            user_id: The ID of the user.
            key_type: Type of SSH key (e.g., 'rsa', 'ecdsa', 'ed25519').
            key_data: The public key data in PEM format.
            fingerprint: The fingerprint of the public key.

        Returns:
            True if the key was saved successfully, False otherwise.
        """
        try:
            timestamp = datetime.now().isoformat()
            self.db_manager.execute(
                """
                INSERT INTO user_ssh_public_keys
                (user_id, key_type, key_data, fingerprint, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, key_type) DO UPDATE SET
                    key_data = excluded.key_data,
                    fingerprint = excluded.fingerprint,
                    updated_at = excluded.updated_at
                """,
                (user_id, key_type, key_data, fingerprint, timestamp, timestamp)
            )
            self.db_manager.commit()
            _logger.info(f"Saved SSH public key for user {user_id} ({key_type})")
            return True
        except Exception as e:
            _logger.error(f"Failed to save SSH public key for user {user_id} ({key_type}): {e}")
            self.db_manager.rollback()
            return False

    def get_user_ssh_keys(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all SSH public keys for a specific user.

        Args:
            user_id: The ID of the user.

        Returns:
            A list of dictionaries, where each dictionary contains key information.
        """
        try:
            rows = self.db_manager.fetchall(
                """
                SELECT key_type, key_data, fingerprint, created_at, updated_at
                FROM user_ssh_public_keys
                WHERE user_id = ?
                ORDER BY key_type
                """,
                (user_id,)
            )
            return rows if rows else []
        except Exception as e:
            _logger.error(f"Failed to get SSH public keys for user {user_id}: {e}")
            return []

    def delete_user_ssh_key(self, user_id: str, key_type: str) -> bool:
        """Delete a specific SSH public key for a user.

        Args:
            user_id: The ID of the user.
            key_type: The type of SSH key to delete.

        Returns:
            True if the key was deleted successfully, False otherwise.
        """
        try:
            self.db_manager.execute(
                "DELETE FROM user_ssh_public_keys WHERE user_id = ? AND key_type = ?",
                (user_id, key_type)
            )
            self.db_manager.commit()
            _logger.info(f"Deleted SSH public key for user {user_id} ({key_type})")
            return True
        except Exception as e:
            _logger.error(f"Failed to delete SSH public key for user {user_id} ({key_type}): {e}")
            self.db_manager.rollback()
            return False

    def delete_all_user_ssh_keys(self, user_id: str) -> bool:
        """Delete all SSH public keys for a specific user.

        Args:
            user_id: The ID of the user.

        Returns:
            True if all keys were deleted successfully, False otherwise.
        """
        try:
            self.db_manager.execute(
                "DELETE FROM user_ssh_public_keys WHERE user_id = ?",
                (user_id,)
            )
            self.db_manager.commit()
            _logger.info(f"Deleted all SSH public keys for user {user_id}")
            return True
        except Exception as e:
            _logger.error(f"Failed to delete all SSH public keys for user {user_id}: {e}")
            self.db_manager.rollback()
            return False


# --- Authentication Manager ---

class AuthenticationManager:
    """Authentication manager for WebUI and service authentication.
    
    This class handles:
    - WebUI user authentication and session management
    - Database-backed credential validation
    - Session token creation and validation
    - Periodic session cleanup
    - User SSH public key management for SFTP/SSH access
    """

    def __init__(self, db_manager: IndexDatabaseManager):
        """Initialize AuthenticationManager.

        Args:
            db_manager: Database management service for credential storage
        """
        self.db_manager = db_manager
        self._sessions: Dict[str, SessionToken] = {}
        self._lock = threading.RLock()
        
        # Initialize UserSSHKeyManager
        self.user_ssh_key_manager = UserSSHKeyManager(db_manager)
        
        # Start session cleanup background thread
        self._start_session_cleanup()

    def authenticate_user(self, username: str, password: str, purpose: str = "webui") -> Optional[str]:
        """Authenticate a user and create a session token.
        
        Args:
            username: Username to authenticate
            password: Password to verify
            purpose: Authentication purpose (webui, webdav, etc.)
            
        Returns:
            Session token if authentication succeeds, None otherwise
        """
        if self._validate_user_credentials(username, password, purpose):
            return self._create_session_token(username)
        return None

    def _validate_user_credentials(self, username: str, password: str, purpose: str) -> bool:
        """Validate user credentials against database.

        Args:
            username: Username to validate
            password: Password to verify
            purpose: Authentication purpose

        Returns:
            True if credentials are valid, False otherwise
        """
        try:
            query = """
                SELECT password_plain, password_hash, enabled
                FROM auth_users
                WHERE username = ? AND purpose = ? AND enabled = 1
            """
            result = self.db_manager.fetchone(query, (username, purpose))
            
            if not result:
                return False
            
            stored_plain = result.get('password_plain')
            stored_hash = _normalize_password_hash(result.get('password_hash'))
            
            # Check plain text password first (for backward compatibility)
            if stored_plain and stored_plain == password:
                if not stored_hash:
                    stored_hash = _hash_password(password)
                    stored_plain = None
                    self.db_manager.execute(
                        "UPDATE auth_users SET password_plain = ?, password_hash = ? WHERE username = ? AND purpose = ?",
                        (stored_plain, stored_hash, username, purpose),
                    )
                    self.db_manager.commit()
                return True
            
            # Check hashed password
            if stored_hash and _verify_password_hash(password, stored_hash):
                return True
            
            return False
        except Exception:
            _logger.error("Failed to validate user credentials for %s", username)
            return False

    def _create_session_token(self, username: str) -> str:
        """Create a new session token for a user.
        
        Args:
            username: Username to create session for
            
        Returns:
            Generated session token
        """
        # Clean up expired sessions first
        self._cleanup_expired_sessions()
        
        # Remove existing sessions for this user
        self._remove_user_sessions(username)
        
        # Generate new token
        token = secrets.token_urlsafe(32)
        created_at = datetime.utcnow()
        last_used = created_at
        expires_at = created_at + timedelta(hours=24)  # 24 hour expiration
        
        session = SessionToken(
            token=token,
            username=username,
            created_at=created_at,
            last_used=last_used,
            expires_at=expires_at
        )
        
        with self._lock:
            self._sessions[token] = session
        
        # Store session in database
        try:
            success = self.db_manager.create_session(
                username=username,
                token=token,
                expires_at=expires_at
            )
            if not success:
                _logger.error("Failed to store session in database")
        except Exception as exc:
            _logger.error("Failed to store session in database: %s", exc)
        
        return token

    def validate_session_token(self, token: str) -> Optional[str]:
        """Validate a session token and return username if valid.
        
        Args:
            token: Session token to validate
            
        Returns:
            Username if token is valid, None otherwise
        """
        if not token:
            return None
        
        # Check in-memory cache first
        with self._lock:
            session = self._sessions.get(token)
            if session:
                if session.is_valid():
                    session.update_last_used()
                    return session.username
                else:
                    # Remove expired session
                    del self._sessions[token]
        
        # Check database
        try:
            session_data = self.db_manager.get_session(token)
            if not session_data:
                return None
            
            username = session_data['username']
            last_used = session_data['last_used']
            expires_at = session_data['expires_at']
            
            if isinstance(last_used, str):
                try:
                    last_used = datetime.fromisoformat(last_used)
                except ValueError:
                    last_used = datetime.utcnow()
            if isinstance(expires_at, str):
                try:
                    expires_at = datetime.fromisoformat(expires_at)
                except ValueError:
                    expires_at = datetime.utcnow()
            
            # Check if expired
            if datetime.utcnow() >= expires_at:
                # Remove expired session from database
                self.db_manager.delete_session(token)
                return None
            
            # Update last used time
            new_last_used = datetime.utcnow()
            success = self.db_manager.execute(
                "UPDATE auth_sessions SET last_used = ? WHERE token = ?",
                (new_last_used.isoformat(), token)
            )
            if success:
                self.db_manager.commit()
            
            # Update in-memory cache
            with self._lock:
                self._sessions[token] = SessionToken(
                    token=token,
                    username=username,
                    created_at=(
                        datetime.fromisoformat(session_data['created_at'])
                        if isinstance(session_data.get('created_at'), str)
                        else session_data['created_at']
                    ),
                    last_used=new_last_used,
                    expires_at=expires_at
                )
            
            return username
        except Exception:
            _logger.error("Failed to validate session token")
            return None

    def _remove_user_sessions(self, username: str) -> None:
        """Remove all sessions for a specific user.
        
        Args:
            username: Username to remove sessions for
        """
        with self._lock:
            tokens_to_remove = []
            for token, session in self._sessions.items():
                if session.username == username:
                    tokens_to_remove.append(token)
            
            for token in tokens_to_remove:
                del self._sessions[token]
        
        # Remove from database
        try:
            self.db_manager.execute("DELETE FROM auth_sessions WHERE username = ?", (username,))
            self.db_manager.commit()
        except Exception as exc:
            _logger.error("Failed to remove user sessions: %s", exc)

    def _cleanup_expired_sessions(self) -> None:
        """Clean up expired sessions from memory."""
        with self._lock:
            datetime.utcnow()
            expired_tokens = []
            for token, session in self._sessions.items():
                if not session.is_valid():
                    expired_tokens.append(token)
            
            for token in expired_tokens:
                del self._sessions[token]

    def _start_session_cleanup(self) -> None:
        """Start background thread for session cleanup."""
        def cleanup_loop():
            while True:
                try:
                    # Clean up expired sessions
                    self._cleanup_expired_sessions()
                    
                    # Clean up database sessions older than 24 hours
                    cleaned = self.db_manager.cleanup_expired_sessions(max_age_hours=24)
                    if cleaned > 0:
                        _logger.debug("Cleaned up %d expired sessions", cleaned)
                except Exception as exc:
                    _logger.warning("Session cleanup error: %s", exc)
                
                # Wait 1 hour before next cleanup
                time.sleep(3600)
        
        cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        cleanup_thread.start()

    def logout_user(self, token: str) -> None:
        """Logout a user by invalidating their session token.

        Args:
            token: Session token to invalidate
        """
        with self._lock:
            if token in self._sessions:
                del self._sessions[token]
        
        # Remove from database
        self.db_manager.delete_session(token)

    def authenticate_request(self, username: str, password: str) -> dict:
        """Authenticate request and return session info.
        
        Args:
            username: Username or "api-key"
            password: Password or API key
            
        Returns:
            Dictionary with authentication result and session info
        """
        # Check if session token authentication
        if self.validate_session_token(username):
            session_username = self.validate_session_token(username)
            return {
                'authenticated': True,
                'method': 'session',
                'username': session_username,
                'token': username
            }
        
        # Check database credentials
        if self.db_adapter.validate_credentials(username, password, purpose="webui"):
            # Create new session token
            token = self._create_session_token(username)
            return {
                'authenticated': True,
                'method': 'credentials',
                'username': username,
                'token': token
            }
        
        return {'authenticated': False, 'method': None, 'username': None, 'token': None}

    # --- User Permissions ---
    def get_user_permissions(self, username: str) -> Dict[str, bool]:
        """Get user permissions from the database."""
        try:
            query = """
                SELECT write_access, read_access, delete_access, modify_access
                FROM auth_users
                WHERE username = ? AND enabled = 1
            """
            result = self.db_manager.fetchone(query, (username,))
            if not result:
                return {'write': False, 'read': False, 'delete': False, 'modify': False}
            
            return {
                'write': result.get('write_access', False),
                'read': result.get('read_access', False),
                'delete': result.get('delete_access', False),
                'modify': result.get('modify_access', False),
            }
        except Exception as e:
            _logger.error(f"Failed to get permissions for user {username}: {e}")
            return {'write': False, 'read': False, 'delete': False, 'modify': False}

    def get_all_users(self) -> Dict[str, Dict[str, Any]]:
        """Get all users from the database."""
        try:
            query = "SELECT username, password_hash, enabled FROM auth_users"
            results = self.db_manager.fetchall(query)
            users = {}
            for row in results:
                users[row['username']] = {
                    'password_hash': row['password_hash'],
                    'enabled': row['enabled'],
                }
            return users
        except Exception as e:
            _logger.error(f"Failed to get all users: {e}")
            return {}


__all__ = [
    "AuthenticationManager",
    "SessionToken",
    "_hash_password",
    "_verify_password_hash",
]
