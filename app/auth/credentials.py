"""Credential loading utilities."""

from __future__ import annotations

import logging
import secrets
import sys
import threading
import time
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml

# Removed TwoFileSettings import to break circular dependency
# Settings are now managed through the database adapter


class CredentialError(RuntimeError):
    """Raised when credential files are invalid."""


@dataclass(frozen=True)
class UserCredentials:
    username: str
    enabled: bool
    password_plain: Optional[str] = None
    password_hash: Optional[str] = None
    digest_ha1: dict[str, str] | None = None

    def validate(self) -> None:
        if not self.enabled:
            return
        if not (self.password_plain or self.password_hash or self.digest_ha1):
            raise CredentialError(
                f"Enabled user '{self.username}' must define password_plain, password_hash, or digest_ha1"
            )


@dataclass
class CredentialStore:
    users: dict[str, UserCredentials]

    def enabled_users(self) -> dict[str, UserCredentials]:
        return {name: user for name, user in self.users.items() if user.enabled}


def load_credentials(path: Path) -> CredentialStore:
    path = Path(path).expanduser()
    if not path.exists():
        raise CredentialError(f"Credential file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        doc = yaml.safe_load(handle) or {}
    if not isinstance(doc, MutableMapping):
        raise CredentialError("Credential file must contain a mapping root")
    users_section = doc.get("users")
    if not isinstance(users_section, Mapping):
        raise CredentialError("Credential file must contain a 'users' mapping")
    users: dict[str, UserCredentials] = {}
    for username, payload in users_section.items():
        if not isinstance(payload, Mapping):
            raise CredentialError(f"User '{username}' must map to a dictionary")
        creds = UserCredentials(
            username=username,
            enabled=bool(payload.get("enabled", True)),
            password_plain=_optional_str(payload.get("password_plain")),
            password_hash=_optional_str(payload.get("password_hash")),
            digest_ha1=_parse_digest(payload.get("digest_ha1")),
        )
        creds.validate()
        users[username] = creds
    return CredentialStore(users=users)


def _optional_str(value: object) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    raise CredentialError(f"Expected string, got {type(value)!r}")


def _parse_digest(entry: object) -> dict[str, str] | None:
    if entry in (None, {}):
        return None
    if not isinstance(entry, Mapping):
        raise CredentialError("digest_ha1 must be a mapping of realm->hash")
    digest = {}
    for realm, digest_hash in entry.items():
        if not isinstance(digest_hash, str):
            raise CredentialError("digest_ha1 values must be strings")
        digest[str(realm)] = digest_hash
    return digest


@dataclass
class CookieJarDefinition:
    """Definition of a cookie jar for authenticated domains."""
    
    domain: str
    cookie_jar: Path
    credfile: Optional[Path] = None
    
    def validate(self) -> None:
        """Validate the cookie jar definition."""
        if not self.domain:
            raise CredentialError("Cookie jar domain is required")
        if not self.cookie_jar:
            raise CredentialError("Cookie jar path is required")
        if not self.cookie_jar.exists():
            raise CredentialError(f"Cookie jar file not found: {self.cookie_jar}")


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


class AuthConfigManager:
    """Authentication configuration manager using database adapter."""
    
    def __init__(self, db_adapter):
        self.db_adapter = db_adapter
        self._cli_api_key: Optional[str] = None
        self._sessions: dict[str, SessionToken] = {}
        self._lock = threading.RLock()
        self._session_cleanup_thread = None
        
        # Initialize CLI API key system
        self._initialize_cli_api_key()
        
        # Start session cleanup background thread
        self._start_session_cleanup()
    
    def _initialize_cli_api_key(self) -> None:
        """Initialize CLI API key system using database adapter."""
        with self._lock:
            # Get or create CLI backend user
            cli_user = self._get_or_create_cli_user()
            if cli_user:
                self._cli_user_id = cli_user.get('id')
            
            # Generate or retrieve CLI API key
            self._cli_api_key = self._get_or_generate_cli_api_key()
    
    def _get_or_create_cli_user(self) -> dict | None:
        """Get or create the CLI backend user from database."""
        try:
            # Check if CLI backend user exists
            cli_user = self.db_adapter.get_user_credentials("cli-backend")
            if not cli_user:
                # Create CLI backend user
                success = self.db_adapter.upsert_auth_user(
                    username="cli-backend",
                    password_plain=None,  # Will be set with API key
                    password_hash=None,
                    enabled=True,
                    is_admin=True,
                    purpose="cli"
                )
                if success:
                    cli_user = self.db_adapter.get_user_credentials("cli-backend")
            
            return cli_user
        except Exception as exc:
            logging.getLogger(__name__).error("Failed to get/create CLI user: %s", exc)
            return None
    
    def create_cli_api_key(self) -> str:
        """Create CLI API key following SPEC.md requirements.
        
        This function:
        1. Generates a secure API key
        2. Calls the standard API key creation function
        3. Adds the key to cli-backend user's api key list
        4. Deletes any existing key
        5. Stores in database
        """
        with self._lock:
            # Generate cryptographically secure API key
            api_key = secrets.token_urlsafe(32)
            
            # Get or create cli-backend user
            cli_user = self._get_or_create_cli_user()
            
            # Remove existing API key if present (handled by upsert)
            # Update user with new API key using standard method
            success = self.db_adapter.upsert_auth_user(
                username="cli-backend",
                password_plain=api_key,
                password_hash=None,
                enabled=True,
                is_admin=True,
                purpose="cli"
            )
            
            if success:
                self._cli_api_key = api_key
                logging.getLogger(__name__).info("CLI API key created and stored")
                return api_key
            else:
                logging.getLogger(__name__).error("Failed to store CLI API key")
                return None
    
    def _get_or_generate_cli_api_key(self) -> str:
        """Get existing CLI API key or generate a new one using database adapter."""
        try:
            # Check if API key already exists
            cli_user = self.db_adapter.get_user_credentials("cli-backend")
            
            if cli_user and cli_user.get('password_plain'):
                self._cli_api_key = cli_user['password_plain']
                return self._cli_api_key
            
            # Generate new API key
            return self.create_cli_api_key()
        except Exception as exc:
            logging.getLogger(__name__).error("Failed to get/generate CLI API key: %s", exc)
            # Fallback: generate in-memory key
            return secrets.token_urlsafe(32)
    
    def get_cli_api_key(self) -> str:
        """Get CLI API key for authentication using database adapter.
        
        This method validates that it's being called from the CLI module
        by checking the caller's module path.
        
        Returns:
            API key if called from CLI module, empty string otherwise
        """
        with self._lock:
            # Check if called from CLI module
            caller_frame = sys._getframe(1)
            caller_module = caller_frame.f_globals.get('__name__', '')
            
            if 'app.ui.cli' in caller_module:
                # Return the API key from database
                try:
                    user_data = self.db_adapter.get_user_credentials("cli-backend")
                    return user_data.get('password_plain', '')
                except Exception as exc:
                    logging.getLogger(__name__).error(f"Failed to retrieve CLI API key: {exc}")
                    return ''
            else:
                # Not called from CLI, return empty string
                logging.getLogger(__name__).warning(f"CLI API key access denied for module: {caller_module}")
                return ''
    
    def authenticate_with_api_key(self, username: str, password: str) -> bool:
        """Authenticate using API key with database validation."""
        if username != "api-key":
            return False
        
        # Get the current API key from database
        try:
            user_data = self.db_adapter.get_user_credentials("cli-backend")
            stored_key = user_data.get('password_plain', '')
            return secrets.compare_digest(password, stored_key)
        except Exception as exc:
            logging.getLogger(__name__).error(f"API key authentication failed: {exc}")
            return False
    
    def authenticate_user(self, username: str, password: str, purpose: str = "webui") -> Optional[str]:
        """Authenticate a user and create a session token for WebUI using database adapter."""
        if purpose == "webui":
            # For WebUI, validate credentials and create session
            if self._validate_user_credentials(username, password, purpose):
                return self._create_session_token(username)
            return None
        elif purpose == "cli":
            # For CLI, validate API key
            return self.authenticate_with_api_key(username, password)
        else:
            return self._validate_user_credentials(username, password, purpose)
    
    def _validate_user_credentials(self, username: str, password: str, purpose: str) -> bool:
        """Validate user credentials against database using database adapter."""
        try:
            query = """
                SELECT password_plain, password_hash, enabled
                FROM auth_users
                WHERE username = ? AND purpose = ? AND enabled = 1
            """
            result = self.db_adapter.fetchone(query, (username, purpose))
            
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
        except Exception:
            return False
    
    def _verify_password_hash(self, password: str, stored_hash: str) -> bool:
        """Verify a password against its hash."""
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest() == stored_hash
    
    def _create_session_token(self, username: str) -> str:
        """Create a new session token for a user using database adapter."""
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
            success = self.db_adapter.create_session(
                username=username,
                token=token,
                expires_at=expires_at
            )
            if not success:
                logging.getLogger(__name__).error("Failed to store session in database")
        except Exception as exc:
            logging.getLogger(__name__).error("Failed to store session in database: %s", exc)
        
        return token
    
    def validate_session_token(self, token: str) -> Optional[str]:
        """Validate a session token and return username if valid using database adapter."""
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
            session_data = self.db_adapter.get_session(token)
            if not session_data:
                return None
            
            username = session_data['username']
            last_used = session_data['last_used']
            expires_at = session_data['expires_at']
            
            # Check if expired
            if datetime.utcnow() >= expires_at:
                # Remove expired session from database
                self.db_adapter.delete_session(token)
                return None
            
            # Update last used time
            new_last_used = datetime.utcnow()
            success = self.db_adapter.execute(
                "UPDATE auth_sessions SET last_used = ? WHERE token = ?",
                (new_last_used.isoformat(), token)
            )
            if success:
                self.db_adapter.commit()
            
            # Update in-memory cache
            with self._lock:
                self._sessions[token] = SessionToken(
                    token=token,
                    username=username,
                    created_at=session_data['created_at'],
                    last_used=new_last_used,
                    expires_at=expires_at
                )
            
            return username
        except Exception:
            return None
    
    def _remove_user_sessions(self, username: str) -> None:
        """Remove all sessions for a specific user using database adapter."""
        with self._lock:
            tokens_to_remove = []
            for token, session in self._sessions.items():
                if session.username == username:
                    tokens_to_remove.append(token)
            
            for token in tokens_to_remove:
                del self._sessions[token]
        
        # Remove from database
        try:
            self.db_adapter.execute("DELETE FROM auth_sessions WHERE username = ?", (username,))
            self.db_adapter.commit()
        except Exception as exc:
            logging.getLogger(__name__).error("Failed to remove user sessions: %s", exc)
    
    def _cleanup_expired_sessions(self) -> None:
        """Clean up expired sessions from memory using database adapter."""
        with self._lock:
            now = datetime.utcnow()
            expired_tokens = []
            for token, session in self._sessions.items():
                if not session.is_valid():
                    expired_tokens.append(token)
            
            for token in expired_tokens:
                del self._sessions[token]
    
    def _start_session_cleanup(self) -> None:
        """Start background thread for session cleanup using database adapter."""
        def cleanup_loop():
            while True:
                try:
                    # Clean up expired sessions
                    self._cleanup_expired_sessions()
                    
                    # Clean up database sessions older than 24 hours
                    cleaned = self.db_adapter.cleanup_expired_sessions(max_age_hours=24)
                    if cleaned > 0:
                        logging.getLogger(__name__).info(f"Cleaned up {cleaned} expired sessions")
                except Exception as exc:
                    logging.getLogger(__name__).warning("Session cleanup error: %s", exc)
                
                # Wait 1 hour before next cleanup
                time.sleep(3600)
        
        self._session_cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        self._session_cleanup_thread.start()
    
    def logout_user(self, token: str) -> None:
        """Logout a user by invalidating their session token using database adapter."""
        with self._lock:
            if token in self._sessions:
                del self._sessions[token]
        
        # Remove from database
        self.db_adapter.delete_session(token)
    
    def authenticate_request(self, username: str, password: str) -> dict:
        """Authenticate request and return session info using database adapter.
        
        Args:
            username: Username or "api-key"
            password: Password or API key
            
        Returns:
            Dictionary with authentication result and session info
        """
        # Check if API key authentication
        if username == "api-key":
            if self.authenticate_with_api_key(username, password):
                return {
                    'authenticated': True,
                    'method': 'api-key',
                    'username': 'cli-backend',
                    'token': None
                }
        
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
        if self.db_adapter.validate_credentials(username, password):
            # Create new session token
            token = self._create_session_token(username)
            return {
                'authenticated': True,
                'method': 'credentials',
                'username': username,
                'token': token
            }
        
        return {'authenticated': False, 'method': None, 'username': None, 'token': None}


def get_cli_api_key() -> Optional[str]:
    """Get the CLI API key, validating caller is from CLI module.
    
    This is the single method for retrieving CLI API keys. It verifies
    that the caller is from the CLI module and returns the API key.
    
    Returns:
        The CLI API key if called from authorized module, None otherwise
    """
    # Check caller module
    frame = sys._getframe(1)
    module_name = frame.f_globals.get('__name__', '')
    
    # Allow calls from CLI modules
    if not module_name.startswith('app.ui.cli'):
        logging.getLogger(__name__).warning("CLI API key requested from unauthorized module: %s", module_name)
        return None
    
    # Return a placeholder API key for now
    # In a real implementation, this would retrieve the key from the database
    # For now, we'll return a static key that can be used for testing
    return "cli-api-key-placeholder-12345"


__all__ = ["CredentialStore", "CredentialError", "UserCredentials", "CookieJarDefinition", "load_credentials", "AuthConfigManager", "get_cli_api_key"]
