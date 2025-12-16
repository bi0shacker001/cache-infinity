"""Redis backend implementation for CacheInfinity.

This class provides Redis-based caching for file metadata, checksums, and other
temporary data. It works alongside the SQL database to provide high-performance
caching for frequently accessed data.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional, Union, List, Dict

_logger = logging.getLogger(__name__)


class RedisBackend:
    """Redis backend for caching file metadata and checksums."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        """Initialize Redis backend.
        
        Args:
            redis_url: Redis connection URL
        """
        self._redis_url = redis_url
        self._redis = None
        self._lock = threading.RLock()
        self._connect()

    def _connect(self):
        """Establish Redis connection."""
        try:
            import redis
            self._redis = redis.from_url(self._redis_url)
            # Test the connection
            self._redis.ping()
            _logger.info("Connected to Redis at %s", self._redis_url)
        except ImportError as exc:
            _logger.error("Redis package not available: %s", exc)
            self._redis = None
        except Exception as exc:
            _logger.error("Failed to connect to Redis: %s", exc)
            self._redis = None

    def is_connected(self) -> bool:
        """Check if Redis is connected."""
        if not self._redis:
            return False
        try:
            self._redis.ping()
            return True
        except Exception:
            return False

    def close(self):
        """Close Redis connection."""
        if self._redis:
            try:
                self._redis.close()
            except Exception:
                pass
            self._redis = None

    def _serialize_value(self, value: Any) -> str:
        """Serialize a value to JSON string."""
        try:
            return json.dumps(value)
        except (TypeError, ValueError):
            # Fallback to string representation
            return str(value)

    def _deserialize_value(self, value: str) -> Any:
        """Deserialize a JSON string to a value."""
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            # Fallback to string
            return value

    # File metadata operations
    def set_file_metadata(self, file_path: str, metadata: Dict[str, Any], ttl: int = 3600) -> bool:
        """Set file metadata in Redis.
        
        Args:
            file_path: Path to the file
            metadata: File metadata dictionary
            ttl: Time to live in seconds (default: 1 hour)
            
        Returns:
            True if successful, False otherwise
        """
        if not self._redis:
            return False
        
        try:
            with self._lock:
                key = f"file_metadata:{file_path}"
                value = self._serialize_value(metadata)
                self._redis.setex(key, ttl, value)
                return True
        except Exception as exc:
            _logger.error("Failed to set file metadata for %s: %s", file_path, exc)
            return False

    def get_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get file metadata from Redis.
        
        Args:
            file_path: Path to the file
            
        Returns:
            File metadata dictionary if found, None otherwise
        """
        if not self._redis:
            return None
        
        try:
            with self._lock:
                key = f"file_metadata:{file_path}"
                value = self._redis.get(key)
                if value:
                    return self._deserialize_value(value.decode('utf-8'))
                return None
        except Exception as exc:
            _logger.error("Failed to get file metadata for %s: %s", file_path, exc)
            return None

    def delete_file_metadata(self, file_path: str) -> bool:
        """Delete file metadata from Redis.
        
        Args:
            file_path: Path to the file
            
        Returns:
            True if successful, False otherwise
        """
        if not self._redis:
            return False
        
        try:
            with self._lock:
                key = f"file_metadata:{file_path}"
                result = self._redis.delete(key)
                return result > 0
        except Exception as exc:
            _logger.error("Failed to delete file metadata for %s: %s", file_path, exc)
            return False

    # Checksum operations
    def set_checksum(self, file_path: str, checksum: str, ttl: int = 86400) -> bool:
        """Set checksum for a file in Redis.
        
        Args:
            file_path: Path to the file
            checksum: Checksum value
            ttl: Time to live in seconds (default: 24 hours)
            
        Returns:
            True if successful, False otherwise
        """
        if not self._redis:
            return False
        
        try:
            with self._lock:
                key = f"checksum:{file_path}"
                self._redis.setex(key, ttl, checksum)
                return True
        except Exception as exc:
            _logger.error("Failed to set checksum for %s: %s", file_path, exc)
            return False

    def get_checksum(self, file_path: str) -> Optional[str]:
        """Get checksum for a file from Redis.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Checksum string if found, None otherwise
        """
        if not self._redis:
            return None
        
        try:
            with self._lock:
                key = f"checksum:{file_path}"
                value = self._redis.get(key)
                if value:
                    return value.decode('utf-8')
                return None
        except Exception as exc:
            _logger.error("Failed to get checksum for %s: %s", file_path, exc)
            return None

    # Indexing data operations
    def set_indexing_data(self, target_id: str, data: Dict[str, Any], ttl: int = 7200) -> bool:
        """Set indexing data in Redis.
        
        Args:
            target_id: Target identifier
            data: Indexing data dictionary
            ttl: Time to live in seconds (default: 2 hours)
            
        Returns:
            True if successful, False otherwise
        """
        if not self._redis:
            return False
        
        try:
            with self._lock:
                key = f"indexing:{target_id}"
                value = self._serialize_value(data)
                self._redis.setex(key, ttl, value)
                return True
        except Exception as exc:
            _logger.error("Failed to set indexing data for %s: %s", target_id, exc)
            return False

    def get_indexing_data(self, target_id: str) -> Optional[Dict[str, Any]]:
        """Get indexing data from Redis.
        
        Args:
            target_id: Target identifier
            
        Returns:
            Indexing data dictionary if found, None otherwise
        """
        if not self._redis:
            return None
        
        try:
            with self._lock:
                key = f"indexing:{target_id}"
                value = self._redis.get(key)
                if value:
                    return self._deserialize_value(value.decode('utf-8'))
                return None
        except Exception as exc:
            _logger.error("Failed to get indexing data for %s: %s", target_id, exc)
            return None

    # Cache state operations
    def set_cache_state(self, domain: str, state: Dict[str, Any], ttl: int = 3600) -> bool:
        """Set cache state for a domain in Redis.
        
        Args:
            domain: Domain name
            state: Cache state dictionary
            ttl: Time to live in seconds (default: 1 hour)
            
        Returns:
            True if successful, False otherwise
        """
        if not self._redis:
            return False
        
        try:
            with self._lock:
                key = f"cache_state:{domain}"
                value = self._serialize_value(state)
                self._redis.setex(key, ttl, value)
                return True
        except Exception as exc:
            _logger.error("Failed to set cache state for %s: %s", domain, exc)
            return False

    def get_cache_state(self, domain: str) -> Optional[Dict[str, Any]]:
        """Get cache state for a domain from Redis.
        
        Args:
            domain: Domain name
            
        Returns:
            Cache state dictionary if found, None otherwise
        """
        if not self._redis:
            return None
        
        try:
            with self._lock:
                key = f"cache_state:{domain}"
                value = self._redis.get(key)
                if value:
                    return self._deserialize_value(value.decode('utf-8'))
                return None
        except Exception as exc:
            _logger.error("Failed to get cache state for %s: %s", domain, exc)
            return None

    # Session operations
    def set_session(self, session_id: str, data: Dict[str, Any], ttl: int = 3600) -> bool:
        """Set session data in Redis.
        
        Args:
            session_id: Session identifier
            data: Session data dictionary
            ttl: Time to live in seconds (default: 1 hour)
            
        Returns:
            True if successful, False otherwise
        """
        if not self._redis:
            return False
        
        try:
            with self._lock:
                key = f"session:{session_id}"
                value = self._serialize_value(data)
                self._redis.setex(key, ttl, value)
                return True
        except Exception as exc:
            _logger.error("Failed to set session %s: %s", session_id, exc)
            return False

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session data from Redis.
        
        Args:
            session_id: Session identifier
            
        Returns:
            Session data dictionary if found, None otherwise
        """
        if not self._redis:
            return None
        
        try:
            with self._lock:
                key = f"session:{session_id}"
                value = self._redis.get(key)
                if value:
                    return self._deserialize_value(value.decode('utf-8'))
                return None
        except Exception as exc:
            _logger.error("Failed to get session %s: %s", session_id, exc)
            return None

    def delete_session(self, session_id: str) -> bool:
        """Delete session data from Redis.
        
        Args:
            session_id: Session identifier
            
        Returns:
            True if successful, False otherwise
        """
        if not self._redis:
            return False
        
        try:
            with self._lock:
                key = f"session:{session_id}"
                result = self._redis.delete(key)
                return result > 0
        except Exception as exc:
            _logger.error("Failed to delete session %s: %s", session_id, exc)
            return False

    # General operations
    def exists(self, key: str) -> bool:
        """Check if a key exists in Redis.
        
        Args:
            key: Key to check
            
        Returns:
            True if key exists, False otherwise
        """
        if not self._redis:
            return False
        
        try:
            with self._lock:
                result = self._redis.exists(key)
                return result > 0
        except Exception:
            return False

    def delete(self, key: str) -> bool:
        """Delete a key from Redis.
        
        Args:
            key: Key to delete
            
        Returns:
            True if successful, False otherwise
        """
        if not self._redis:
            return False
        
        try:
            with self._lock:
                result = self._redis.delete(key)
                return result > 0
        except Exception:
            return False

    def keys(self, pattern: str) -> List[str]:
        """Get keys matching a pattern from Redis.
        
        Args:
            pattern: Pattern to match
            
        Returns:
            List of matching keys
        """
        if not self._redis:
            return []
        
        try:
            with self._lock:
                keys = self._redis.keys(pattern)
                return [key.decode('utf-8') for key in keys]
        except Exception:
            return []

    def flushdb(self) -> bool:
        """Flush the Redis database.
        
        Returns:
            True if successful, False otherwise
        """
        if not self._redis:
            return False
        
        try:
            with self._lock:
                self._redis.flushdb()
                return True
        except Exception:
            return False

    def get_pool_stats(self) -> Dict[str, Any]:
        """Get Redis connection statistics.
        
        Returns:
            Dictionary with connection statistics
        """
        if not self._redis:
            return {"connected": False}
        
        try:
            info = self._redis.info()
            return {
                "connected": True,
                "used_memory_human": info.get("used_memory_human", "N/A"),
                "connected_clients": info.get("connected_clients", 0),
                "total_commands_processed": info.get("total_commands_processed", 0),
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
            }
        except Exception:
            return {"connected": False}

__all__ = ["RedisBackend"]