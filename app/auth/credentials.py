"""Authentication management for CacheInfinity."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import shlex
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

try:
    import asyncssh
    ASYNCSSH_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    ASYNCSSH_AVAILABLE = False
    asyncssh = None

_logger = logging.getLogger(__name__)


def start_session_cleanup_thread(
    index_db,
    stop_event: threading.Event,
    *,
    interval_seconds: int = 3600,
    max_age_hours: int = 24,
) -> threading.Thread:
    """Start a background thread to expire stale WebUI sessions."""

    def _loop() -> None:
        while not stop_event.is_set():
            try:
                index_db.cleanup_expired_sessions(max_age_hours=max_age_hours)
            except Exception as exc:  # pragma: no cover - defensive
                _logger.warning("Session cleanup failed: %s", exc, exc_info=True)
            stop_event.wait(interval_seconds)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread


# Password hashing constants
_HASH_SCHEME_PBKDF2 = "pbkdf2_sha256"
_HASH_SCHEME_SHA256 = "sha256"
_HASH_DEFAULT_ITERATIONS = 200_000


def render_authorized_keys(entries: List[Dict[str, Any]]) -> str:
    lines: list[str] = []
    for key in entries:
        key_data = (key.get("key_data") or "").strip()
        key_type = (key.get("key_type") or "").strip()
        if not key_data:
            continue
        comment = f"CacheInfinity {key.get('key_type', 'unknown')} key"
        if key_type:
            lines.append(f"{key_type} {key_data} {comment}")
        else:
            lines.append(f"{key_data} {comment}")
    return "\n".join(lines) + ("\n" if lines else "")


def parse_authorized_keys_content(content: str) -> List[Dict[str, str]]:
    keys: list[dict[str, str]] = []
    lines = content.strip().split("\n")
    valid_key_types = {
        "ssh-rsa",
        "ssh-dss",
        "ssh-ed25519",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
    }
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = shlex.split(line, posix=True)
        except ValueError:
            continue
        key_type = None
        key_data = None
        comment = ""
        for idx, part in enumerate(parts):
            if part in valid_key_types:
                if idx + 1 < len(parts):
                    key_type = part
                    key_data = parts[idx + 1]
                    comment = " ".join(parts[idx + 2:]) if idx + 2 < len(parts) else ""
                break
        if key_type and key_data:
            keys.append({"key_type": key_type, "key_data": key_data, "comment": comment})
    return keys


def validate_authorized_keys_content(content: str) -> tuple[bool, List[Dict[str, str]]]:
    if not content.strip():
        return True, []
    raw_lines = [
        line for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    parsed_keys = parse_authorized_keys_content(content)
    if not parsed_keys or len(parsed_keys) != len(raw_lines):
        return False, []
    if not ASYNCSSH_AVAILABLE:
        return False, []
    for key in parsed_keys:
        key_type = key.get("key_type", "")
        key_data = key.get("key_data", "")
        if not key_type or not key_data:
            return False, []
        try:
            asyncssh.import_public_key(f"{key_type} {key_data}")
        except Exception:
            return False, []
    return True, parsed_keys


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


# --- SSH Host Key Management ---

class SSHHostKeyManager:
    """Manager for SSH host keys stored in database."""

    def __init__(self, db_adapter):
        """Initialize SSH host key manager.

        Args:
            db_adapter: Database adapter instance
        """
        self.db_adapter = db_adapter
        self._logger = logging.getLogger(__name__)
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize database schema for SSH host keys."""
        try:
            self.db_adapter.execute(
                """
                CREATE TABLE IF NOT EXISTS config_ssh_host_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_type TEXT NOT NULL,
                    key_data TEXT NOT NULL,
                    key_comment TEXT,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(key_type)
                )
                """
            )
            self.db_adapter.commit()
            self._logger.info("SSH host keys table initialized")
        except Exception as e:
            self._logger.error(f"Failed to initialize SSH host keys schema: {e}")
            self.db_adapter.rollback()

    def save_host_key(
        self,
        key_type: str,
        key_data: str,
        key_comment: str | None = None,
        fingerprint: str | None = None,
    ) -> bool:
        """Save SSH host key to database."""
        try:
            timestamp = datetime.now().isoformat()
            self.db_adapter.execute(
                """
                INSERT INTO config_ssh_host_keys
                (key_type, key_data, key_comment, fingerprint, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(key_type) DO UPDATE SET
                    key_data = excluded.key_data,
                    key_comment = excluded.key_comment,
                    fingerprint = excluded.fingerprint,
                    updated_at = excluded.updated_at
                """,
                (key_type, key_data, key_comment, fingerprint, timestamp, timestamp),
            )
            self.db_adapter.commit()
            self._logger.info(f"Saved SSH host key: {key_type}")
            return True
        except Exception as e:
            self._logger.error(f"Failed to save SSH host key {key_type}: {e}")
            self.db_adapter.rollback()
            return False

    def get_host_key(self, key_type: str) -> Optional[Dict[str, Any]]:
        """Get SSH host key from database."""
        try:
            row = self.db_adapter.fetchone(
                "SELECT key_type, key_data, key_comment, fingerprint, created_at, updated_at "
                "FROM config_ssh_host_keys WHERE key_type = ?",
                (key_type,),
            )
            return row if row else None
        except Exception as e:
            self._logger.error(f"Failed to get SSH host key {key_type}: {e}")
            return None

    def get_all_host_keys(self) -> List[Dict[str, Any]]:
        """Get all SSH host keys from database."""
        try:
            rows = self.db_adapter.fetchall(
                "SELECT key_type, key_data, key_comment, fingerprint, created_at, updated_at "
                "FROM config_ssh_host_keys ORDER BY key_type"
            )
            return rows if rows else []
        except Exception as e:
            self._logger.error(f"Failed to get all SSH host keys: {e}")
            return []

    def delete_host_key(self, key_type: str) -> bool:
        """Delete SSH host key from database."""
        try:
            self.db_adapter.execute(
                "DELETE FROM config_ssh_host_keys WHERE key_type = ?",
                (key_type,),
            )
            self.db_adapter.commit()
            self._logger.info(f"Deleted SSH host key: {key_type}")
            return True
        except Exception as e:
            self._logger.error(f"Failed to delete SSH host key {key_type}: {e}")
            self.db_adapter.rollback()
            return False

    def rotate_host_keys(self) -> bool:
        """Rotate all SSH host keys by generating new ones."""
        if not ASYNCSSH_AVAILABLE:
            self._logger.error("asyncssh is not installed")
            return False
        try:
            key_types = ["rsa", "ecdsa", "ed25519"]
            for key_type in key_types:
                if key_type == "rsa":
                    key = asyncssh.generate_private_key("ssh-rsa", 4096)
                elif key_type == "ecdsa":
                    key = asyncssh.generate_private_key("ecdsa-sha2-nistp521", 521)
                else:
                    key = asyncssh.generate_private_key("ssh-ed25519", 255)

                fingerprint = key.get_fingerprint()
                self.save_host_key(
                    key_type,
                    key.export_private_key().decode("utf-8"),
                    f"CacheInfinity {key_type} host key",
                    fingerprint,
                )
            self._logger.info("SSH host keys rotated successfully")
            return True
        except Exception as e:
            self._logger.error(f"Failed to rotate SSH host keys: {e}")
            return False

    def load_or_generate_host_keys(self) -> List[Path]:
        """Load existing host keys or generate new ones."""
        host_keys: list[Path] = []
        key_types = ["ssh_host_rsa_key", "ssh_host_ecdsa_key", "ssh_host_ed25519_key"]

        for key_type in key_types:
            key_info = self.get_host_key(key_type.replace("ssh_host_", "").replace("_key", ""))
            if key_info:
                key_path = Path(f"/tmp/{key_type}")
                key_path.write_text(key_info["key_data"])
                host_keys.append(key_path)
                _logger.info(f"Loaded SSH host key from database: {key_type}")
                continue

            if not ASYNCSSH_AVAILABLE:
                _logger.error("asyncssh is not installed; cannot generate host keys")
                continue
            try:
                self._generate_and_save_host_key(key_type)
                key_info = self.get_host_key(key_type.replace("ssh_host_", "").replace("_key", ""))
                if key_info:
                    key_path = Path(f"/tmp/{key_type}")
                    key_path.write_text(key_info["key_data"])
                    host_keys.append(key_path)
            except Exception as e:
                _logger.error(f"Failed to generate SSH host key {key_type}: {e}")

        return host_keys

    def _generate_and_save_host_key(self, key_type: str) -> None:
        """Generate a new SSH host key and save to database."""
        if not ASYNCSSH_AVAILABLE:
            raise RuntimeError("asyncssh is not installed")
        if "rsa" in key_type:
            key = asyncssh.generate_private_key("ssh-rsa", 4096)
        elif "ecdsa" in key_type:
            key = asyncssh.generate_private_key("ecdsa-sha2-nistp521", 521)
        elif "ed25519" in key_type:
            key = asyncssh.generate_private_key("ssh-ed25519", 255)
        else:
            return

        fingerprint = key.get_fingerprint()
        key_name = key_type.replace("ssh_host_", "").replace("_key", "")
        self.save_host_key(
            key_name,
            key.export_private_key().decode("utf-8"),
            f"CacheInfinity {key_name} host key",
            fingerprint,
        )


class SSHHostKeyAdmin:
    """Admin interface for SSH host key management."""

    def __init__(self, ssh_key_manager: SSHHostKeyManager):
        self.ssh_key_manager = ssh_key_manager
        self._logger = logging.getLogger(__name__)

    def list_host_keys(self) -> List[Dict[str, Any]]:
        return self.ssh_key_manager.get_all_host_keys()

    def get_host_key_info(self, key_type: str) -> Optional[Dict[str, Any]]:
        return self.ssh_key_manager.get_host_key(key_type)

    def generate_new_host_key(self, key_type: str) -> bool:
        if not ASYNCSSH_AVAILABLE:
            self._logger.error("asyncssh is not installed")
            return False
        if key_type == "rsa":
            key = asyncssh.generate_private_key("ssh-rsa", 4096)
        elif key_type == "ecdsa":
            key = asyncssh.generate_private_key("ecdsa-sha2-nistp521", 521)
        elif key_type == "ed25519":
            key = asyncssh.generate_private_key("ssh-ed25519", 255)
        else:
            raise ValueError(f"Unsupported key type: {key_type}")

        fingerprint = key.get_fingerprint()
        return self.ssh_key_manager.save_host_key(
            key_type,
            key.export_private_key().decode("utf-8"),
            f"CacheInfinity {key_type} host key",
            fingerprint,
        )

    def rotate_all_host_keys(self) -> bool:
        return self.ssh_key_manager.rotate_host_keys()

    def delete_host_key(self, key_type: str) -> bool:
        return self.ssh_key_manager.delete_host_key(key_type)

    def export_host_key(self, key_type: str) -> Optional[str]:
        try:
            key_info = self.get_host_key_info(key_type)
            return key_info["key_data"] if key_info else None
        except Exception as e:
            self._logger.error(f"Failed to export SSH host key {key_type}: {e}")
            return None

    def get_key_fingerprint(self, key_type: str) -> Optional[str]:
        try:
            key_info = self.get_host_key_info(key_type)
            return key_info["fingerprint"] if key_info else None
        except Exception as e:
            self._logger.error(f"Failed to get fingerprint for SSH host key {key_type}: {e}")
            return None


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
        self.db_adapter = db_manager
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
                WHERE username = ? AND purpose = ? AND enabled = TRUE
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
        if self.db_manager.validate_credentials(username, password, purpose="webui"):
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
                WHERE username = ? AND enabled = TRUE
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

    def get_authorized_keys_text(self, username: str) -> str:
        if not username:
            return ""
        try:
            keys = self.user_ssh_key_manager.get_user_ssh_keys(username)
            return render_authorized_keys(keys)
        except Exception as exc:
            _logger.error("Failed to load authorized_keys for %s: %s", username, exc)
            return ""

    def update_authorized_keys_text(self, username: str, content: str) -> bool:
        if not username:
            return False
        is_valid, keys = validate_authorized_keys_content(content)
        if not is_valid:
            _logger.warning("Rejected invalid authorized_keys update for %s", username)
            return False
        try:
            self.user_ssh_key_manager.delete_all_user_ssh_keys(username)
            for key in keys:
                fingerprint = f"SHA256:{hash(key['key_data']) % 1000000:06d}"
                if ASYNCSSH_AVAILABLE:
                    try:
                        parsed_key = asyncssh.import_public_key(f"{key['key_type']} {key['key_data']}")
                        fingerprint = parsed_key.get_fingerprint()
                    except Exception:
                        pass
                self.user_ssh_key_manager.save_user_ssh_key(
                    username,
                    key["key_type"],
                    key["key_data"],
                    fingerprint,
                )
            return True
        except Exception as exc:
            _logger.error("Failed to update authorized_keys for %s: %s", username, exc)
            return False

    def get_authorized_keys_editable(self, username: str, *, purpose: str = "webdav") -> bool:
        if not username:
            return False
        try:
            row = self.db_manager.fetchone(
                "SELECT ssh_keys_editable FROM auth_users WHERE username = ? AND purpose = ?",
                (username, purpose),
            )
            if not row:
                return False
            return bool(row.get("ssh_keys_editable", True))
        except Exception as exc:
            _logger.error("Failed to read ssh_keys_editable for %s: %s", username, exc)
            return False

    def set_authorized_keys_editable(self, username: str, enabled: bool, *, purpose: str = "webdav") -> bool:
        if not username:
            return False
        try:
            self.db_manager.execute(
                "UPDATE auth_users SET ssh_keys_editable = ? WHERE username = ? AND purpose = ?",
                (1 if enabled else 0, username, purpose),
            )
            self.db_manager.commit()
            return True
        except Exception as exc:
            _logger.error("Failed to update ssh_keys_editable for %s: %s", username, exc)
            self.db_manager.rollback()
            return False


__all__ = [
    "ASYNCSSH_AVAILABLE",
    "AuthenticationManager",
    "SSHHostKeyAdmin",
    "SSHHostKeyManager",
    "SessionToken",
    "UserSSHKeyManager",
    "parse_authorized_keys_content",
    "render_authorized_keys",
    "validate_authorized_keys_content",
    "_hash_password",
    "_verify_password_hash",
]
