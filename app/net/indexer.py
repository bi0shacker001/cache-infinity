"""Indexing and remote listing management for CacheInfinity networking."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from ..auth.credentials import CookieJarDefinition
from ..core.config import IndexingSettings
from ..db.adapter import DatabaseAdapter

_logger = logging.getLogger(__name__)


class RemoteListingFetcher:
    """Fetcher for remote directory listings with support for multiple protocols."""
    
    def __init__(self):
        """Initialize remote listing fetcher."""
        self._session = None
        _logger.info("RemoteListingFetcher initialized")
    
    def fetch(self, url: str, parse_entries: bool = True) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Fetch remote directory listing.
        
        Args:
            url: URL to fetch listing from
            parse_entries: Whether to parse directory entries
            
        Returns:
            Tuple of (entries list, metadata dict)
        """
        try:
            # Determine protocol and fetch accordingly
            if url.startswith('http'):
                return self._fetch_http_listing(url, parse_entries)
            elif url.startswith('ftp'):
                return self._fetch_ftp_listing(url, parse_entries)
            else:
                raise ValueError(f"Unsupported protocol for URL: {url}")
                
        except Exception as exc:
            _logger.error(f"Failed to fetch listing from {url}: {exc}")
            return [], {'error': str(exc), 'url': url}
    
    def _fetch_http_listing(self, url: str, parse_entries: bool, target_id: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Fetch HTTP directory listing with conditional requests."""
        import requests
        from urllib.parse import urljoin, urlparse
        
        try:
            # Check for cached ETag/Last-Modified
            cached_etag = None
            cached_modified = None
            if target_id and self.db_adapter:
                result = self.db_adapter.fetchone("""
                    SELECT etag, last_modified FROM indexing_cache WHERE target_id = ?
                """, (target_id,))
                if result:
                    cached_etag = result.get('etag')
                    cached_modified = result.get('last_modified')
            
            # Make request with headers to mimic browser and conditional requests
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            # Add conditional headers if we have cached values
            if cached_etag:
                headers['If-None-Match'] = cached_etag
            if cached_modified:
                headers['If-Modified-Since'] = cached_modified
            
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            metadata = {
                'status_code': response.status_code,
                'content_type': response.headers.get('content-type', ''),
                'last_modified': response.headers.get('last-modified', ''),
                'content_length': response.headers.get('content-length', ''),
                'etag': response.headers.get('etag', ''),
                'url': url
            }
            
            # Handle 304 Not Modified
            if response.status_code == 304:
                _logger.info(f"Target {target_id}: Listing unchanged (304 Not Modified)")
                return [], {'status': 'not_modified', 'url': url}
            
            # Cache the new ETag/Last-Modified
            if target_id and self.db_adapter:
                self.db_adapter.execute("""
                    INSERT OR REPLACE INTO indexing_cache (target_id, etag, last_modified, cached_at)
                    VALUES (?, ?, ?, ?)
                """, (target_id, metadata['etag'], metadata['last_modified'], int(time.time())))
                self.db_adapter.commit()
            
            if not parse_entries:
                return [], metadata
            
            # Parse HTML directory listing
            entries = self._parse_html_directory(response.text, url)
            return entries, metadata
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 304:
                _logger.info(f"Target {target_id}: Listing unchanged (304 Not Modified)")
                return [], {'status': 'not_modified', 'url': url}
            else:
                _logger.error(f"HTTP error for {url}: {e}")
                return [], {'error': str(e), 'url': url}
        except Exception as exc:
            _logger.error(f"HTTP listing fetch failed for {url}: {exc}")
            return [], {'error': str(exc), 'url': url}
    
    def _fetch_ftp_listing(self, url: str, parse_entries: bool) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Fetch FTP directory listing."""
        import ftplib
        from urllib.parse import urlparse
        
        try:
            parsed = urlparse(url)
            host = parsed.hostname
            port = parsed.port or 21
            path = parsed.path or '/'
            
            # Connect to FTP server
            ftp = ftplib.FTP()
            ftp.connect(host, port, timeout=30)
            ftp.login()  # Anonymous login
            
            # Change to directory
            if path != '/':
                ftp.cwd(path)
            
            # Get directory listing
            entries = []
            metadata = {
                'host': host,
                'port': port,
                'path': path,
                'url': url
            }
            
            if parse_entries:
                file_list = []
                ftp.retrlines('LIST', file_list.append)
                entries = self._parse_ftp_directory(file_list, url)
            
            ftp.quit()
            return entries, metadata
            
        except Exception as exc:
            _logger.error(f"FTP listing fetch failed for {url}: {exc}")
            return [], {'error': str(exc), 'url': url}
    
    def _parse_html_directory(self, html_content: str, base_url: str) -> List[Dict[str, Any]]:
        """Parse HTML directory listing."""
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin
        
        entries = []
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Look for common directory listing patterns
        # This is a simplified parser - real implementation would be more robust
        for link in soup.find_all('a', href=True):
            href = link['href']
            text = link.get_text().strip()
            
            # Skip parent directory links
            if href in ['../', '..', '/']:
                continue
            
            # Determine if it's a directory or file
            is_dir = href.endswith('/') or text.endswith('/')
            name = text.rstrip('/') if text else href.rstrip('/').split('/')[-1]
            
            if name:  # Skip empty names
                entry = {
                    'name': name,
                    'path': href,
                    'is_dir': is_dir,
                    'url': urljoin(base_url, href) if not href.startswith('http') else href
                }
                entries.append(entry)
        
        return entries
    
    def _parse_ftp_directory(self, file_list: List[str], base_url: str) -> List[Dict[str, Any]]:
        """Parse FTP directory listing."""
        entries = []
        
        for line in file_list:
            if not line.strip():
                continue
            
            # Parse FTP LIST format (simplified)
            # Format: permissions links size month day time/year name
            parts = line.split()
            if len(parts) >= 9:
                name = ' '.join(parts[8:])
                is_dir = parts[0].startswith('d')
                
                entry = {
                    'name': name,
                    'path': name,
                    'is_dir': is_dir,
                    'url': f"{base_url.rstrip('/')}/{name}",
                    'size': int(parts[4]) if parts[4].isdigit() else 0,
                    'modified': ' '.join(parts[5:8])
                }
                entries.append(entry)
        
        return entries


class Indexer:
    """Manages indexing of remote sources and listing updates."""
    
    def __init__(self, settings: IndexingSettings, cookie_jars: Dict[str, CookieJarDefinition],
                 db_adapter: Optional[DatabaseAdapter] = None):
        """Initialize indexer.
        
        Args:
            settings: Indexing configuration settings
            cookie_jars: Cookie jar definitions for authenticated domains
            db_adapter: Database adapter for persistence
        """
        self.settings = settings
        self.cookie_jars = cookie_jars
        self.db_adapter = db_adapter
        self._last_index_times: Dict[str, int] = {}
        self._fetcher = RemoteListingFetcher()
        _logger.info("Indexer initialized")
        
    def should_reindex(self, target_id: str) -> bool:
        """Determine if a target should be reindexed.
        
        Args:
            target_id: Identifier for the target
            
        Returns:
            True if reindex should be performed, False otherwise
        """
        last_index = self._last_index_times.get(target_id, 0)
        current_time = int(time.time())
        
        # Check minimum interval
        min_interval = self.settings.min_full_reindex_days * 24 * 3600
        if current_time - last_index < min_interval:
            return False
            
        # Check maximum interval
        max_interval = self.settings.max_full_reindex_days * 24 * 3600
        if current_time - last_index > max_interval:
            return True
            
        # Additional logic could be added here for hotness-based early reindexing
        return False
        
    def index_target(self, target_id: str, url: str, subfolder: str) -> bool:
        """Index a remote target.
        
        Args:
            target_id: Identifier for the target
            url: Remote URL to index
            subfolder: Subfolder within the URL
            
        Returns:
            True if indexing was successful, False otherwise
        """
        try:
            # Update last index time
            self._last_index_times[target_id] = int(time.time())
            
            # Build full URL with subfolder
            full_url = f"{url.rstrip('/')}/{subfolder.lstrip('/')}"
            
            # Fetch remote listing with conditional requests
            entries, metadata = self._fetcher.fetch(full_url, parse_entries=True)
            
            # Handle 304 Not Modified
            if metadata.get('status') == 'not_modified':
                _logger.info(f"Target {target_id}: No changes since last index")
                if self.db_adapter:
                    self.db_adapter.execute("""
                        INSERT OR REPLACE INTO indexing_log (
                            target_id, timestamp, success, entries_processed, error_message
                        ) VALUES (?, ?, ?, ?, ?)
                    """, (target_id, int(time.time()), True, 0, None))
                    self.db_adapter.commit()
                return True
            
            if not entries:
                _logger.warning(f"No entries found for {target_id}: {full_url}")
                if self.db_adapter:
                    self.db_adapter.execute("""
                        INSERT OR REPLACE INTO indexing_log (
                            target_id, timestamp, success, entries_processed, error_message
                        ) VALUES (?, ?, ?, ?, ?)
                    """, (target_id, int(time.time()), True, 0, "No entries found"))
                    self.db_adapter.commit()
                return True
            
            # Process entries and update database
            indexed_count = 0
            for entry in entries:
                try:
                    # Skip if entry is incomplete
                    if not entry.get('name') or 'is_dir' not in entry:
                        continue
                    
                    # Update database with entry
                    if self.db_adapter:
                        self._update_entry_in_database(target_id, entry)
                    
                    indexed_count += 1
                    
                except Exception as exc:
                    _logger.warning(f"Failed to process entry for {target_id}: {exc}")
                    continue
            
            # Log indexing result
            if self.db_adapter:
                self.db_adapter.execute("""
                    INSERT OR REPLACE INTO indexing_log (
                        target_id, timestamp, success, entries_processed, error_message
                    ) VALUES (?, ?, ?, ?, ?)
                """, (target_id, int(time.time()), True, indexed_count, None))
                self.db_adapter.commit()
            
            _logger.info(f"Indexed target {target_id}: {url}/{subfolder} - {indexed_count} entries")
            return True
            
        except Exception as exc:
            _logger.error(f"Failed to index target {target_id}: {exc}")
            if self.db_adapter:
                self.db_adapter.execute("""
                    INSERT OR REPLACE INTO indexing_log (
                        target_id, timestamp, success, entries_processed, error_message
                    ) VALUES (?, ?, ?, ?, ?)
                """, (target_id, int(time.time()), False, 0, str(exc)))
                self.db_adapter.commit()
            return False
    
    def _update_entry_in_database(self, target_id: str, entry: Dict[str, Any]) -> None:
        """Update a single entry in the database."""
        # This would integrate with the IndexDatabase to update entries
        # For now, we'll implement a basic version
        pass
    
    def index_all_targets(self, targets: List[Dict[str, str]]) -> Dict[str, bool]:
        """Index multiple targets.
        
        Args:
            targets: List of target dictionaries with 'id', 'url', 'subfolder' keys
            
        Returns:
            Dictionary mapping target IDs to success status
        """
        results = {}
        
        for target in targets:
            target_id = target.get('id')
            url = target.get('url')
            subfolder = target.get('subfolder', '/')
            
            if not all([target_id, url]):
                _logger.warning(f"Skipping invalid target: {target}")
                results[target_id] = False
                continue
            
            results[target_id] = self.index_target(target_id, url, subfolder)
        
        return results
    
    def get_index_progress(self, target_id: str) -> Dict[str, Any]:
        """Get detailed indexing progress for a target.
        
        Args:
            target_id: Identifier for the target
            
        Returns:
            Dictionary with detailed progress information
        """
        status = self.get_index_status(target_id)
        
        # Add additional progress information
        progress = {
            **status,
            'progress_details': {
                'entries_processed': 0,  # Would be tracked during indexing
                'entries_total': 0,      # Would be known from remote listing
                'time_elapsed': int(time.time()) - status['last_indexed'] if status['last_indexed'] else 0
            }
        }
        
        return progress
            
    def get_index_status(self, target_id: str) -> Dict[str, Any]:
        """Get indexing status for a target.
        
        Args:
            target_id: Identifier for the target
            
        Returns:
            Dictionary with indexing status information
        """
        last_index = self._last_index_times.get(target_id, 0)
        return {
            'target_id': target_id,
            'last_indexed': last_index,
            'should_reindex': self.should_reindex(target_id),
            'next_possible_index': last_index + (self.settings.min_full_reindex_days * 24 * 3600)
        }
        
    def get_all_index_status(self) -> List[Dict[str, Any]]:
        """Get indexing status for all targets.
        
        Returns:
            List of dictionaries with indexing status for all targets
        """
        # This would typically query the database for all targets
        # For now, return status for targets we've indexed
        return [self.get_index_status(target_id) for target_id in self._last_index_times.keys()]
        
    def cleanup_old_indexes(self, max_age_days: int = 90) -> bool:
        """Clean up old index entries.
        
        Args:
            max_age_days: Maximum age of index entries in days
            
        Returns:
            True if cleanup was successful, False otherwise
        """
        try:
            current_time = int(time.time())
            max_age = max_age_days * 24 * 3600
            
            # Remove old entries from memory
            old_targets = [
                target_id for target_id, last_index in self._last_index_times.items()
                if current_time - last_index > max_age
            ]
            
            for target_id in old_targets:
                del self._last_index_times[target_id]
                
            _logger.info(f"Cleaned up {len(old_targets)} old index entries")
            return True
            
        except Exception as exc:
            _logger.error(f"Failed to cleanup old indexes: {exc}")
            return False
    
    def _ensure_database_tables(self) -> None:
        """Ensure required database tables exist."""
        if not self.db_adapter:
            return
        
        try:
            # Create file_access table for access tracking
            self.db_adapter.execute("""
                CREATE TABLE IF NOT EXISTS file_access (
                    file_path TEXT NOT NULL,
                    user TEXT NOT NULL,
                    last_accessed INTEGER NOT NULL,
                    access_count INTEGER DEFAULT 1,
                    PRIMARY KEY (file_path, user)
                )
            """)
            
            # Create indexing_log table for indexing history
            self.db_adapter.execute("""
                CREATE TABLE IF NOT EXISTS indexing_log (
                    target_id TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    success BOOLEAN NOT NULL,
                    entries_processed INTEGER DEFAULT 0,
                    error_message TEXT
                )
            """)
            
            # Create indexing_cache table for conditional requests
            self.db_adapter.execute("""
                CREATE TABLE IF NOT EXISTS indexing_cache (
                    target_id TEXT NOT NULL PRIMARY KEY,
                    etag TEXT,
                    last_modified TEXT,
                    cached_at INTEGER NOT NULL
                )
            """)
            
            # Create indexes for better performance
            self.db_adapter.execute("CREATE INDEX IF NOT EXISTS idx_file_access_path ON file_access(file_path)")
            self.db_adapter.execute("CREATE INDEX IF NOT EXISTS idx_file_access_user ON file_access(user)")
            self.db_adapter.execute("CREATE INDEX IF NOT EXISTS idx_file_access_time ON file_access(last_accessed)")
            self.db_adapter.execute("CREATE INDEX IF NOT EXISTS idx_indexing_log_target ON indexing_log(target_id)")
            self.db_adapter.execute("CREATE INDEX IF NOT EXISTS idx_indexing_log_time ON indexing_log(timestamp)")
            
            self.db_adapter.commit()
            _logger.info("Database tables initialized for indexer")
            
        except Exception as exc:
            _logger.error(f"Failed to initialize database tables: {exc}")
    
    def record_file_access(self, file_path: str, user: str) -> bool:
        """Record file access for hotness tracking.
        
        Args:
            file_path: Path to the accessed file
            user: User who accessed the file
            
        Returns:
            True if access was recorded successfully
        """
        try:
            if not self.db_adapter:
                return False
            
            current_time = int(time.time())
            
            # Insert or update access record
            self.db_adapter.execute("""
                INSERT OR REPLACE INTO file_access (
                    file_path, user, last_accessed, access_count
                ) VALUES (?, ?, ?,
                    COALESCE((SELECT access_count FROM file_access WHERE file_path = ? AND user = ?), 0) + 1
                )
            """, (file_path, user, current_time, file_path, user))
            
            self.db_adapter.commit()
            return True
            
        except Exception as exc:
            _logger.error(f"Failed to record file access for {file_path}: {exc}")
            return False
    
    def calculate_hotness_score(self, file_path: str) -> float:
        """Calculate hotness score for a file based on access patterns.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Hotness score (higher = more popular)
        """
        try:
            if not self.db_adapter:
                return 0.0
            
            current_time = int(time.time())
            window_start = current_time - (self.settings.hot_window_days * 24 * 3600)
            
            # Get access count within hot window
            result = self.db_adapter.fetchone("""
                SELECT access_count, last_accessed
                FROM file_access
                WHERE file_path = ? AND last_accessed >= ?
                ORDER BY last_accessed DESC
                LIMIT 1
            """, (file_path, window_start))
            
            if not result:
                return 0.0
            
            access_count = result['access_count']
            last_accessed = result['last_accessed']
            
            # Calculate time decay factor (older accesses count less)
            time_diff = current_time - last_accessed
            time_decay = max(0.1, 1.0 - (time_diff / (self.settings.hot_window_days * 24 * 3600)))
            
            # Calculate hotness score
            base_score = access_count * time_decay
            age_bonus = min(1.0, access_count / self.settings.hot_radius)
            
            score = base_score + (age_bonus * self.settings.score_weights.get('hot', 0.5))
            
            return score
            
        except Exception as exc:
            _logger.error(f"Failed to calculate hotness score for {file_path}: {exc}")
            return 0.0
    
    def get_hot_files(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get list of hottest files based on access patterns.
        
        Args:
            limit: Maximum number of files to return
            
        Returns:
            List of hot files with their scores
        """
        try:
            if not self.db_adapter:
                return []
            
            current_time = int(time.time())
            window_start = current_time - (self.settings.hot_window_days * 24 * 3600)
            
            results = self.db_adapter.fetchall("""
                SELECT file_path, access_count, last_accessed
                FROM file_access
                WHERE last_accessed >= ?
                ORDER BY access_count DESC, last_accessed DESC
                LIMIT ?
            """, (window_start, limit))
            
            hot_files = []
            for row in results:
                score = self.calculate_hotness_score(row['file_path'])
                hot_files.append({
                    'file_path': row['file_path'],
                    'access_count': row['access_count'],
                    'last_accessed': row['last_accessed'],
                    'hotness_score': score
                })
            
            return sorted(hot_files, key=lambda x: x['hotness_score'], reverse=True)
            
        except Exception as exc:
            _logger.error(f"Failed to get hot files: {exc}")
            return []
    
    def should_reindex_with_budget(self, target_id: str) -> bool:
        """Determine if a target should be reindexed considering budget constraints.
        
        Args:
            target_id: Identifier for the target
            
        Returns:
            True if reindex should be performed, False otherwise
        """
        # Check basic reindex criteria
        if not self.should_reindex(target_id):
            return False
        
        # Check daily budget
        current_time = int(time.time())
        
        # Count successful reindexes performed today
        if self.db_adapter:
            try:
                reindex_count = self.db_adapter.fetchone("""
                    SELECT COUNT(*) as count
                    FROM indexing_log
                    WHERE date(timestamp, 'unixepoch') = date(?, 'unixepoch')
                    AND success = 1
                """, (current_time,))
                
                if reindex_count and reindex_count['count'] >= self.settings.daily_full_reindex_budget:
                    _logger.debug(f"Daily reindex budget exceeded for {target_id}")
                    return False
                
                # Check 14-day budget
                fourteen_days_ago = current_time - (14 * 24 * 3600)
                reindex_count_14d = self.db_adapter.fetchone("""
                    SELECT COUNT(*) as count
                    FROM indexing_log
                    WHERE timestamp >= ?
                    AND success = 1
                """, (fourteen_days_ago,))
                
                if reindex_count_14d and reindex_count_14d['count'] >= self.settings.max_full_reindex_per_14d:
                    _logger.debug(f"14-day reindex budget exceeded for {target_id}")
                    return False
                
                # Check if target has hot files and should be prioritized
                if self.settings.early_full_requires_hot:
                    hot_files = self.get_hot_files(limit=10)
                    if not hot_files:
                        _logger.debug(f"Target {target_id}: Skipping early reindex due to no hot files")
                        return False
                
            except Exception as exc:
                _logger.error(f"Failed to check reindex budget for {target_id}: {exc}")
                return False
        
        return True