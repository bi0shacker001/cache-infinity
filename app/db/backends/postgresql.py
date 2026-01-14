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
        
    def connect(self, max_retries: int = 10, initial_delay: float = 0.5):
        """Establish PostgreSQL connection with retry logic.
        
        Args:
            max_retries: Maximum number of connection attempts
            initial_delay: Initial delay between retries in seconds
        """
        if not self._dsn:
            raise ValueError("PostgreSQL DSN is required")
            
        try:
            import psycopg
        except ImportError as exc:
            raise ImportError("psycopg package is required for PostgreSQL support") from exc
        
        import time
        
        last_error = None
        for attempt in range(max_retries):
            try:
                _logger.debug(f"PostgreSQL connection attempt {attempt + 1}/{max_retries}")
                self._conn = psycopg.connect(self._dsn)
                self._conn.autocommit = False
                _logger.info("Connected to PostgreSQL database")
                return
            except psycopg.OperationalError as exc:
                last_error = exc
                error_msg = str(exc)
                
                if "database system is starting up" in error_msg:
                    # Database is still starting up - this is expected during container startup
                    if attempt < max_retries - 1:
                        delay = initial_delay * (2 ** attempt)  # Exponential backoff
                        _logger.warning(
                            f"PostgreSQL is starting up, retrying in {delay:.1f}s "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(delay)
                        continue
                elif "connection refused" in error_msg or "could not connect" in error_msg:
                    # Connection refused - server might not be ready yet
                    if attempt < max_retries - 1:
                        delay = initial_delay * (2 ** attempt)
                        _logger.warning(
                            f"PostgreSQL connection refused, retrying in {delay:.1f}s "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(delay)
                        continue
                else:
                    # Other connection errors - log and potentially retry
                    if attempt < max_retries - 1:
                        delay = initial_delay * (2 ** attempt)
                        _logger.warning(
                            f"PostgreSQL connection error: {exc}, retrying in {delay:.1f}s "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(delay)
                        continue
                
                # If we get here, we've exhausted retries or it's a fatal error
                _logger.error(f"PostgreSQL connection failed after {attempt + 1} attempts: {exc}")
                raise
            except Exception as exc:
                # Unexpected error - log and raise immediately
                _logger.error(f"Unexpected error during PostgreSQL connection: {exc}")
                raise
        
        # If we get here, all retries exhausted
        _logger.error(f"PostgreSQL connection failed after {max_retries} attempts")
        if last_error:
            raise last_error
        else:
            raise RuntimeError("PostgreSQL connection failed for unknown reasons")
        
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

    def convert_sql(self, sql: str) -> str:
        """Convert SQLite-style SQL to PostgreSQL-compatible SQL.
        
        Args:
            sql: SQLite-style SQL statement
            
        Returns:
            PostgreSQL-compatible SQL statement
        """
        import logging
        _logger.debug("Converting SQL: %s", sql)
        converted = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        converted = converted.replace("AUTOINCREMENT", "")
        converted = converted.replace("?", "%s")
        # Convert boolean defaults - only for BOOLEAN columns
        # Use regex to replace DEFAULT 1/0 only when preceded by BOOLEAN
        import re
        # Pattern: "BOOLEAN DEFAULT 1" -> "BOOLEAN DEFAULT TRUE"
        converted = re.sub(r'\bBOOLEAN\s+DEFAULT\s+1\b', 'BOOLEAN DEFAULT TRUE', converted, flags=re.IGNORECASE)
        converted = re.sub(r'\bBOOLEAN\s+DEFAULT\s+0\b', 'BOOLEAN DEFAULT FALSE', converted, flags=re.IGNORECASE)
        # Also handle "BOOLEAN NOT NULL DEFAULT 1"
        converted = re.sub(r'\bBOOLEAN\s+NOT\s+NULL\s+DEFAULT\s+1\b', 'BOOLEAN NOT NULL DEFAULT TRUE', converted, flags=re.IGNORECASE)
        converted = re.sub(r'\bBOOLEAN\s+NOT\s+NULL\s+DEFAULT\s+0\b', 'BOOLEAN NOT NULL DEFAULT FALSE', converted, flags=re.IGNORECASE)
        # Convert PRAGMA table_info(table) to PostgreSQL information_schema query
        # Pattern: PRAGMA table_info(table_name)
        pragma_pattern = r'PRAGMA\s+table_info\s*\(\s*(\w+)\s*\)'
        def replace_pragma(match):
            table_name = match.group(1)
            return f"SELECT column_name AS name FROM information_schema.columns WHERE table_name = '{table_name}' ORDER BY ordinal_position"
        converted = re.sub(pragma_pattern, replace_pragma, converted, flags=re.IGNORECASE)
        # Convert sqlite_master queries to information_schema
        # Pattern: SELECT name FROM sqlite_master WHERE type='table' AND name='...'
        sqlite_master_pattern = r"SELECT\s+name\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'\s+AND\s+name\s*=\s*'([^']+)'"
        def replace_sqlite_master(match):
            table_name = match.group(1)
            return f"SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public' AND table_name = '{table_name}'"
        converted = re.sub(sqlite_master_pattern, replace_sqlite_master, converted, flags=re.IGNORECASE)
        # Also handle SELECT name FROM sqlite_master WHERE type='table' (without name)
        sqlite_master_pattern2 = r"SELECT\s+name\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'"
        converted = re.sub(sqlite_master_pattern2, "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public'", converted, flags=re.IGNORECASE)
        # Quote reserved keyword 'user' as a column name
        converted = re.sub(r'\buser\b', '"user"', converted)
        
        # Convert INSERT OR REPLACE to INSERT ... ON CONFLICT for PostgreSQL
        # Pattern: INSERT OR REPLACE INTO table_name (columns) VALUES (values)
        insert_or_replace_pattern = r'INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)'
        def replace_insert_or_replace(match):
            table_name = match.group(1)
            columns = match.group(2)
            values = match.group(3)
            # Try to determine the primary key for ON CONFLICT clause
            # Common patterns: id, target_id, cachelink_id, etc.
            column_list = [col.strip() for col in columns.split(',')]
            primary_key = None
            for col in column_list:
                if col.lower() in ['id', 'target_id', 'cachelink_id', 'file_path', 'domain', 'username', 'token']:
                    primary_key = col
                    break
            if not primary_key and column_list:
                primary_key = column_list[0]  # Use first column as fallback
            if primary_key:
                return f"INSERT INTO {table_name} ({columns}) VALUES ({values}) ON CONFLICT({primary_key}) DO UPDATE SET {', '.join([f'{col} = excluded.{col}' for col in column_list])}"
            else:
                return f"INSERT INTO {table_name} ({columns}) VALUES ({values}) ON CONFLICT DO NOTHING"
        converted = re.sub(insert_or_replace_pattern, replace_insert_or_replace, converted, flags=re.IGNORECASE)
        
        # For INTEGER columns, keep DEFAULT 1/0 as is (no conversion)
        _logger.debug("Converted SQL: %s", converted)
        return converted

    @property
    def dsn(self) -> str:
        """Get the PostgreSQL connection string."""
        return self._dsn