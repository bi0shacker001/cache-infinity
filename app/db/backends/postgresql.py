"""PostgreSQL backend implementation for CacheInfinity database operations."""

from __future__ import annotations

import logging
import threading

_logger = logging.getLogger(__name__)


class PostgreSQLBackend:
    """PostgreSQL backend implementation.
    
    This class handles all PostgreSQL-specific database operations.
    It provides a clean interface for the database adapter to use.
    """
    
    def __init__(self, dsn: str):
        """Initialize PostgreSQL backend with connection string.
        
        Args:
            dsn: PostgreSQL connection string (Data Source Name)
        """
        self._dsn = dsn
        self._conn = None
        self._lock = threading.RLock()
        
    def connect(self):
        """Establish PostgreSQL connection."""
        if not self._dsn:
            raise ValueError("PostgreSQL DSN is required")
            
        try:
            import psycopg
        except ImportError as exc:
            raise ImportError("psycopg package is required for PostgreSQL support") from exc
            
        self._conn = psycopg.connect(self._dsn)
        self._conn.autocommit = False
        _logger.info("Connected to PostgreSQL database")
        
    def close(self):
        """Close PostgreSQL connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            
    def execute(self, sql: str, params: tuple = ()):
        """Execute a SQL statement.
        
        Args:
            sql: SQL statement to execute
            params: Parameters for the SQL statement
            
        Returns:
            Cursor object
        """
        if not self._conn:
            raise RuntimeError("Database connection not established")
            
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        return cursor
        
    def executemany(self, sql: str, params_list: list[tuple]):
        """Execute a SQL statement with multiple parameter sets.
        
        Args:
            sql: SQL statement to execute
            params_list: List of parameter tuples
        """
        if not self._conn:
            raise RuntimeError("Database connection not established")
            
        cursor = self._conn.cursor()
        cursor.executemany(sql, params_list)
        cursor.close()
        
    def fetchone(self, sql: str, params: tuple = ()):
        """Execute a query and return a single row.
        
        Args:
            sql: SQL query to execute
            params: Parameters for the SQL query
            
        Returns:
            Single row as a dict, or None if no rows
        """
        cursor = self.execute(sql, params)
        row = cursor.fetchone()
        description = cursor.description
        cursor.close()
        if row is None:
            return None
        
        # Convert to dict using column names
        columns = [col.name for col in description]
        return {col: value for col, value in zip(columns, row)}
        
    def fetchall(self, sql: str, params: tuple = ()):
        """Execute a query and return all rows.
        
        Args:
            sql: SQL query to execute
            params: Parameters for the SQL query
            
        Returns:
            List of rows as dicts
        """
        cursor = self.execute(sql, params)
        rows = cursor.fetchall()
        description = cursor.description
        cursor.close()
        
        # Convert to list of dicts using column names
        columns = [col.name for col in description]
        return [{col: value for col, value in zip(columns, row)} for row in rows]
        
    def commit(self):
        """Commit the current transaction."""
        if self._conn:
            self._conn.commit()
            
    def rollback(self):
        """Rollback the current transaction."""
        if self._conn:
            self._conn.rollback()
            
    def health_check(self) -> bool:
        """Perform a health check on the PostgreSQL connection.
        
        Returns:
            True if connection is healthy, False otherwise
        """
        try:
            if not self._conn:
                return False
            self._conn.execute("SELECT 1")
            return True
        except Exception:
            return False
    
    def get_pool_stats(self) -> dict:
        """Get connection pool statistics.
        
        Returns:
            Dictionary with connection pool statistics
        """
        try:
            if not self._conn:
                return {"connected": False, "pool_size": 0, "available_connections": 0, "in_use_connections": 0}
            
            # For psycopg3, we don't have built-in pool stats, so we return basic info
            return {
                "connected": True,
                "pool_size": 0,  # psycopg3 doesn't expose pool size directly
                "available_connections": 0,
                "in_use_connections": 0,
                "connection_status": "active" if self._conn.closed == 0 else "closed"
            }
        except Exception:
            return {"connected": False, "pool_size": 0, "available_connections": 0, "in_use_connections": 0}

    @property
    def dsn(self) -> str:
        """Get the PostgreSQL connection string."""
        return self._dsn