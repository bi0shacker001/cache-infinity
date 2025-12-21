"""MariaDB backend implementation for CacheInfinity database operations."""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional, Iterable

_logger = logging.getLogger(__name__)


class MariaDBBackend:
    """MariaDB backend implementation.
    
    This class handles all MariaDB-specific database operations.
    It provides a clean interface for the database adapter to use.
    """
    
    def __init__(self, dsn: str):
        """Initialize MariaDB backend with connection string.
        
        Args:
            dsn: MariaDB connection string (Data Source Name)
        """
        self._dsn = dsn
        self._conn = None
        self._lock = threading.RLock()
        
    def connect(self):
        """Establish MariaDB connection."""
        if not self._dsn:
            raise ValueError("MariaDB DSN is required")
            
        try:
            import mariadb
        except ImportError as exc:
            raise ImportError("mariadb package is required for MariaDB support") from exc
            
        try:
            # Parse DSN to extract connection parameters
            import urllib.parse
            parsed = urllib.parse.urlparse(self._dsn)
            
            # Extract connection parameters from DSN
            params = {}
            if parsed.username:
                params['user'] = parsed.username
            if parsed.password:
                params['password'] = parsed.password
            if parsed.hostname:
                params['host'] = parsed.hostname
            if parsed.port:
                params['port'] = parsed.port
            if parsed.path and len(parsed.path) > 1:
                params['database'] = parsed.path[1:]  # Remove leading slash
            
            # Handle query parameters
            if parsed.query:
                query_params = urllib.parse.parse_qs(parsed.query)
                for key, value in query_params.items():
                    if value and len(value) > 0:
                        params[key] = value[0]
            
            self._conn = mariadb.connect(**params)
            self._conn.autocommit = False
            _logger.info("Connected to MariaDB database")
            
        except Exception as exc:
            _logger.error(f"Failed to connect to MariaDB: {exc}")
            raise
    
    def close(self):
        """Close MariaDB connection."""
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
        columns = [col[0] for col in description]
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
        columns = [col[0] for col in description]
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
        """Perform a health check on the MariaDB connection.
        
        Returns:
            True if connection is healthy, False otherwise
        """
        try:
            if not self._conn:
                return False
            cursor = self._conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
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
            
            # For MariaDB connector, we don't have built-in pool stats, so we return basic info
            return {
                "connected": True,
                "pool_size": 0,  # MariaDB connector doesn't expose pool size directly
                "available_connections": 0,
                "in_use_connections": 0,
                "connection_status": "active" if self._conn.open else "closed"
            }
        except Exception:
            return {"connected": False, "pool_size": 0, "available_connections": 0, "in_use_connections": 0}

    @property
    def dsn(self) -> str:
        """Get the MariaDB connection string."""
        return self._dsn