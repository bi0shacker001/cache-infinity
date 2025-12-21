"""Base interface for all database backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, Sequence, Any, Optional, List, Dict, Union


class DatabaseBackend(Protocol):
    """Unified interface for all database backends."""
    
    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the database."""
        pass
    
    @abstractmethod
    def close(self) -> None:
        """Close the database connection."""
        pass
    
    @abstractmethod
    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        """Execute a SQL statement.
        
        Args:
            sql: SQL statement to execute
            params: Parameters for the SQL statement
            
        Returns:
            Cursor object or result
        """
        pass
    
    @abstractmethod
    def executemany(self, sql: str, params_list: List[tuple]) -> None:
        """Execute a SQL statement with multiple parameter sets.
        
        Args:
            sql: SQL statement to execute
            params_list: List of parameter tuples
        """
        pass
    
    @abstractmethod
    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict]:
        """Execute a query and return a single row.
        
        Args:
            sql: SQL query to execute
            params: Parameters for the SQL query
            
        Returns:
            Single row as a dict, or None if no rows
        """
        pass
    
    @abstractmethod
    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> List[Dict]:
        """Execute a query and return all rows.
        
        Args:
            sql: SQL query to execute
            params: Parameters for the SQL query
            
        Returns:
            List of rows as dicts
        """
        pass
    
    @abstractmethod
    def commit(self) -> None:
        """Commit the current transaction."""
        pass
    
    @abstractmethod
    def rollback(self) -> None:
        """Rollback the current transaction."""
        pass
    
    @abstractmethod
    def health_check(self) -> bool:
        """Perform a health check on the database connection.
        
        Returns:
            True if connection is healthy, False otherwise
        """
        pass
    
    @abstractmethod
    def get_pool_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics.
        
        Returns:
            Dictionary with connection pool statistics
        """
        pass


class SQLBackend(DatabaseBackend):
    """Base class for SQL database backends with common functionality."""
    
    def __init__(self):
        self._conn = None
        self._lock = None  # Will be set by subclasses
    
    def _convert_sql(self, sql: str) -> str:
        """Convert SQL syntax for the specific database engine.
        
        Subclasses should override this method to handle engine-specific
        SQL syntax differences.
        
        Args:
            sql: SQL statement with SQLite-style syntax
            
        Returns:
            SQL statement converted for the specific engine
        """
        return sql
    
    def _row_to_dict(self, row) -> Dict:
        """Convert a row to a dictionary.
        
        Subclasses should implement this method to handle their specific
        row format.
        
        Args:
            row: Database row object
            
        Returns:
            Dictionary representation of the row
        """
        raise NotImplementedError("Subclasses must implement _row_to_dict")
    
    def _execute_with_params(self, sql: str, params: Sequence[Any]):
        """Execute SQL with parameters, handling engine-specific parameter styles."""
        converted_sql = self._convert_sql(sql)
        return self.execute(converted_sql, params)
    
    def _fetch_with_params(self, sql: str, params: Sequence[Any], fetch_method: str):
        """Fetch data with parameters, handling engine-specific parameter styles."""
        converted_sql = self._convert_sql(sql)
        if fetch_method == "one":
            return self.fetchone(converted_sql, params)
        elif fetch_method == "all":
            return self.fetchall(converted_sql, params)
        else:
            raise ValueError(f"Unknown fetch method: {fetch_method}")


class RedisBackend(DatabaseBackend):
    """Base interface for Redis caching backend."""
    
    @abstractmethod
    def set_value(self, key: str, value: str, ttl: int | None = None) -> bool:
        """Set a raw value in Redis with optional TTL.
        
        Args:
            key: Redis key
            value: Value to store
            ttl: Time to live in seconds (optional)
            
        Returns:
            True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def get_value(self, key: str) -> Optional[str]:
        """Get a raw value from Redis.
        
        Args:
            key: Redis key
            
        Returns:
            Value if found, None otherwise
        """
        pass
    
    @abstractmethod
    def delete_value(self, key: str) -> bool:
        """Delete a value from Redis.
        
        Args:
            key: Redis key
            
        Returns:
            True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if a key exists in Redis.
        
        Args:
            key: Redis key
            
        Returns:
            True if key exists, False otherwise
        """
        pass
    
    @abstractmethod
    def keys(self, pattern: str) -> List[str]:
        """Get keys matching a pattern from Redis.
        
        Args:
            pattern: Pattern to match
            
        Returns:
            List of matching keys
        """
        pass
    
    @abstractmethod
    def flushdb(self) -> bool:
        """Flush the Redis database.
        
        Returns:
            True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if Redis is connected.
        
        Returns:
            True if connected, False otherwise
        """
        pass


__all__ = ["DatabaseBackend", "SQLBackend", "RedisBackend"]