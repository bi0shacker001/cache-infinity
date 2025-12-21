"""SQLite backend implementation for CacheInfinity database operations."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

_logger = logging.getLogger(__name__)


class SQLiteBackend:
    """SQLite backend implementation.
    
    This class handles all SQLite-specific database operations.
    It provides a clean interface for the database adapter to use.
    """
    
    def __init__(self, db_path: Path):
        """Initialize SQLite backend with database path.
        
        Args:
            db_path: Path to the SQLite database file
        """
        self._db_path = db_path
        self._conn = None
        
    def connect(self):
        """Establish SQLite connection."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        _logger.info(f"Connected to SQLite database at {self._db_path}")
        
    def close(self):
        """Close SQLite connection."""
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
        cursor.close()
        return dict(row) if row else None
        
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
        cursor.close()
        return [dict(row) for row in rows]
        
    def commit(self):
        """Commit the current transaction."""
        if self._conn:
            self._conn.commit()
            
    def rollback(self):
        """Rollback the current transaction."""
        if self._conn:
            self._conn.rollback()
            
    @property
    def path(self) -> Path:
        """Get the database file path."""
        return self._db_path
