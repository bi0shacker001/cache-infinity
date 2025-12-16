"""PostgreSQL backend implementation for CacheInfinity database operations."""

from __future__ import annotations

import logging
from typing import Any, Optional

from .sqlite import SQLiteBackend

_logger = logging.getLogger(__name__)


class PostgreSQLBackend(SQLiteBackend):
    """PostgreSQL backend implementation.
    
    This class extends SQLiteBackend to provide PostgreSQL-specific functionality.
    Most operations are handled by the parent class, but PostgreSQL-specific
    optimizations and features can be added here.
    """
    
    def __init__(self, dsn: str):
        """Initialize PostgreSQL backend with connection string.
        
        Args:
            dsn: PostgreSQL connection string
        """
        super().__init__(None)  # Don't initialize SQLite connection
        self._dsn = dsn
        self._conn = None
        
    def connect(self):
        """Establish PostgreSQL connection."""
        try:
            import psycopg
            self._conn = psycopg.connect(self._dsn)
            self._conn.autocommit = False
            _logger.info("Connected to PostgreSQL database")
        except ImportError as exc:
            raise ImportError("psycopg package is required for PostgreSQL support") from exc
            
    def close(self):
        """Close PostgreSQL connection."""
        if self._conn:
            self._conn.close()
            self._conn = None