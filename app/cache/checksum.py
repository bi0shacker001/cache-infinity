"""Checksum utilities for CacheInfinity."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import zipfile
from pathlib import Path
from typing import Optional, List, Dict, Any, Iterator
from urllib.parse import urlparse

_logger = logging.getLogger(__name__)


class ChecksumCatalog:
    """Catalog for managing checksums of files from various sources."""
    
    def __init__(self, config_dir: Path, index_db):
        """Initialize checksum catalog.
        
        Args:
            config_dir: Configuration directory path
            index_db: Database instance for storing checksums
        """
        self.config_dir = config_dir
        self.index_db = index_db
        self.calculator = ChecksumCalculator()
        self.catalog_dir = config_dir / "checksums"
        self.catalog_dir.mkdir(exist_ok=True)
        self._ensure_catalog_tables()
        _logger.info("Checksum catalog initialized")
    
    def _ensure_catalog_tables(self) -> None:
        """Ensure checksum catalog database tables exist."""
        try:
            # The checksum_catalog table is already created by IndexDatabase
            # Just verify it exists and add any missing indexes
            cur = self.index_db._db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='checksum_catalog'")
            result = cur.fetchone()
            cur.close()
            
            if not result:
                _logger.error("checksum_catalog table does not exist")
                raise Exception("checksum_catalog table not found")
            
            # Add additional indexes if they don't exist
            indexes_to_create = [
                ("idx_checksum_catalog_sha256", "checksum_catalog(sha256)"),
                ("idx_checksum_catalog_md5", "checksum_catalog(md5)"),
                ("idx_checksum_catalog_sha1", "checksum_catalog(sha1)"),
            ]
            
            for index_name, index_def in indexes_to_create:
                try:
                    cur = self.index_db._db.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {index_def}")
                    cur.close()
                except Exception as exc:
                    _logger.warning(f"Failed to create index {index_name}: {exc}")
            
            self.index_db._db.commit()
            _logger.info("Checksum catalog database tables verified")
            
        except Exception as exc:
            _logger.error(f"Failed to verify checksum catalog tables: {exc}")
            raise
    
    def import_catalog_file(self, catalog_path: Path, catalog_type: str = "redump") -> bool:
        """Import a checksum catalog file.
        
        Args:
            catalog_path: Path to catalog file (CSV, JSON, or TXT)
            catalog_type: Type of catalog (redump, no-intro, etc.)
            
        Returns:
            True if import was successful
        """
        try:
            _logger.info(f"Importing catalog: {catalog_path} ({catalog_type})")
            
            if catalog_path.suffix.lower() == '.json':
                return self._import_json_catalog(catalog_path, catalog_type)
            elif catalog_path.suffix.lower() in ['.csv', '.txt']:
                return self._import_text_catalog(catalog_path, catalog_type)
            else:
                _logger.error(f"Unsupported catalog format: {catalog_path.suffix}")
                return False
                
        except Exception as exc:
            _logger.error(f"Failed to import catalog {catalog_path}: {exc}")
            return False
    
    def _import_json_catalog(self, catalog_path: Path, catalog_type: str) -> bool:
        """Import a JSON format catalog."""
        try:
            with open(catalog_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Handle different JSON structures
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict):
                entries = data.get('entries', [])
            else:
                _logger.error(f"Invalid JSON catalog format: {catalog_path}")
                return False
            
            imported = 0
            for entry in entries:
                if self._import_catalog_entry(entry, catalog_type, str(catalog_path)):
                    imported += 1
            
            _logger.info(f"Imported {imported} entries from {catalog_path}")
            return True
            
        except Exception as exc:
            _logger.error(f"Failed to import JSON catalog: {exc}")
            return False
    
    def _import_text_catalog(self, catalog_path: Path, catalog_type: str) -> bool:
        """Import a text format catalog (CSV or TXT)."""
        try:
            imported = 0
            with open(catalog_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    entry = self._parse_catalog_line(line, catalog_type)
                    if entry and self._import_catalog_entry(entry, catalog_type, str(catalog_path)):
                        imported += 1
            
            _logger.info(f"Imported {imported} entries from {catalog_path}")
            return True
            
        except Exception as exc:
            _logger.error(f"Failed to import text catalog: {exc}")
            return False
    
    def _parse_catalog_line(self, line: str, catalog_type: str) -> Optional[Dict[str, Any]]:
        """Parse a single line from a text catalog file."""
        # Common patterns for different catalog formats
        patterns = {
            'redump': r'^([a-fA-F0-9]{32})\s+(\d+)\s+(.+)$',  # MD5 size filename
            'no-intro': r'^([a-fA-F0-9]{40})\s+(\d+)\s+(.+)$',  # SHA1 size filename
        }
        
        pattern = patterns.get(catalog_type)
        if not pattern:
            # Try to auto-detect pattern
            if re.match(r'^[a-fA-F0-9]{32}\s+\d+\s+', line):
                pattern = patterns['redump']
            elif re.match(r'^[a-fA-F0-9]{40}\s+\d+\s+', line):
                pattern = patterns['no-intro']
            else:
                return None
        
        match = re.match(pattern, line)
        if not match:
            return None
        
        if catalog_type == 'redump':
            md5, size, filename = match.groups()
            return {
                'filename': filename.strip(),
                'size': int(size),
                'md5': md5.lower()
            }
        elif catalog_type == 'no-intro':
            sha1, size, filename = match.groups()
            return {
                'filename': filename.strip(),
                'size': int(size),
                'sha1': sha1.lower()
            }
        
        return None
    
    def _import_catalog_entry(self, entry: Dict[str, Any], catalog_type: str, source: str) -> bool:
        """Import a single catalog entry into the database."""
        try:
            filename = entry.get('filename') or entry.get('name') or entry.get('path')
            size = entry.get('size')
            
            if not filename:
                return False
            
            # Normalize filename
            filename = Path(filename).name
            
            # Extract checksums
            checksums = {
                'sha256': entry.get('sha256'),
                'sha1': entry.get('sha1'),
                'md5': entry.get('md5'),
                'crc32': entry.get('crc32')
            }
            
            # Only import if we have at least one checksum
            if not any(checksums.values()):
                return False
            
            # Insert into database using existing table structure
            # The existing table has: source, name, normalized_name, algorithm, digest, size
            # We need to insert each checksum algorithm as a separate row
            for algorithm in ['sha256', 'sha1', 'md5', 'crc32']:
                if checksums.get(algorithm):
                    cur = self.index_db._db.execute("""
                        INSERT OR REPLACE INTO checksum_catalog (
                            source, name, normalized_name, algorithm, digest, size
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        source, filename, filename.lower(), algorithm, checksums[algorithm], size
                    ))
                    cur.close()
            
            return True
            
        except Exception as exc:
            _logger.warning(f"Failed to import catalog entry: {exc}")
            return False
    
    def extract_torrentzip_crc(self, zip_path: Path) -> Optional[Dict[str, str]]:
        """Extract CRC32 checksums from TorrentZip comments.
        
        Args:
            zip_path: Path to ZIP file
            
        Returns:
            Dictionary mapping filenames to CRC32 checksums
        """
        try:
            crc_map = {}
            
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Get central directory comments (TorrentZip stores CRCs here)
                for info in zf.infolist():
                    if info.comment:
                        try:
                            comment = info.comment.decode('utf-8')
                            # TorrentZip format: "filename: CRC32"
                            if ':' in comment:
                                crc_value = comment.split(':')[1].strip()
                                if len(crc_value) == 8:  # Valid CRC32 length
                                    crc_map[info.filename] = crc_value.lower()
                        except Exception:
                            continue
            
            return crc_map if crc_map else None
            
        except Exception as exc:
            _logger.warning(f"Failed to extract TorrentZip CRC from {zip_path}: {exc}")
            return None
    
    def get_file_checksums(self, filename: str, size: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get all known checksums for a file.
        
        Args:
            filename: Name of the file
            size: Optional file size for more precise matching
            
        Returns:
            List of checksum records
        """
        try:
            if size:
                results = self.index_db.fetchall("""
                    SELECT filename, size, sha256, sha1, md5, crc32, source, catalog_type
                    FROM checksum_catalog
                    WHERE filename = ? AND size = ?
                    ORDER BY catalog_type, imported_at DESC
                """, (filename, size))
            else:
                results = self.index_db.fetchall("""
                    SELECT filename, size, sha256, sha1, md5, crc32, source, catalog_type
                    FROM checksum_catalog
                    WHERE filename = ?
                    ORDER BY catalog_type, imported_at DESC
                """, (filename,))
            
            return [dict(row) for row in results]
            
        except Exception as exc:
            _logger.error(f"Failed to get file checksums: {exc}")
            return []
    
    def verify_file_integrity(self, file_path: Path, strict: bool = False) -> Dict[str, Any]:
        """Verify file integrity using stored checksums.
        
        Args:
            file_path: Path to file
            strict: If True, require exact size match
            
        Returns:
            Verification result with details
        """
        try:
            filename = file_path.name
            file_size = file_path.stat().st_size
            
            # Get known checksums for this file
            checksums = self.get_file_checksums(filename, file_size if strict else None)
            
            if not checksums:
                return {
                    'verified': False,
                    'reason': 'no_catalog_entry',
                    'file_size': file_size,
                    'catalog_entries': []
                }
            
            # Calculate current checksums
            current_checksums = {
                'sha256': self.calculator.calculate_sha256(file_path),
                'sha1': self.calculator.calculate_sha1(file_path),
                'md5': self.calculator.calculate_md5(file_path)
            }
            
            # Check against catalog entries
            verified = False
            matches = []
            
            for catalog_entry in checksums:
                for algo in ['sha256', 'sha1', 'md5']:
                    catalog_checksum = catalog_entry.get(algo)
                    if catalog_checksum and current_checksums.get(algo):
                        if catalog_checksum.lower() == current_checksums[algo].lower():
                            verified = True
                            matches.append({
                                'algorithm': algo,
                                'catalog_checksum': catalog_checksum,
                                'current_checksum': current_checksums[algo],
                                'source': catalog_entry['source'],
                                'catalog_type': catalog_entry['catalog_type']
                            })
            
            return {
                'verified': verified,
                'file_size': file_size,
                'catalog_entries': checksums,
                'matches': matches,
                'current_checksums': current_checksums
            }
            
        except Exception as exc:
            _logger.error(f"Failed to verify file integrity: {exc}")
            return {
                'verified': False,
                'reason': str(exc),
                'file_size': 0,
                'catalog_entries': []
            }
    
    def get_catalog_stats(self) -> Dict[str, Any]:
        """Get statistics about the checksum catalog."""
        try:
            total_entries = self.index_db.fetchone("""
                SELECT COUNT(*) as count FROM checksum_catalog
            """)['count']
            
            sources = self.index_db.fetchall("""
                SELECT source, catalog_type, COUNT(*) as count
                FROM checksum_catalog
                GROUP BY source, catalog_type
                ORDER BY count DESC
            """)
            
            algorithms = {}
            for algo in ['sha256', 'sha1', 'md5', 'crc32']:
                count = self.index_db.fetchone(f"""
                    SELECT COUNT(*) as count FROM checksum_catalog WHERE {algo} IS NOT NULL
                """)['count']
                algorithms[algo] = count
            
            return {
                'total_entries': total_entries,
                'sources': [dict(row) for row in sources],
                'algorithms': algorithms
            }
            
        except Exception as exc:
            _logger.error(f"Failed to get catalog stats: {exc}")
            return {}
    
    def scan_catalog_directory(self) -> bool:
        """Scan the catalog directory for new catalog files and import them."""
        try:
            imported_count = 0
            
            for catalog_file in self.catalog_dir.glob('**/*'):
                if catalog_file.is_file() and catalog_file.suffix.lower() in ['.json', '.csv', '.txt']:
                    # Try to determine catalog type from filename
                    catalog_type = self._guess_catalog_type(catalog_file)
                    
                    if self.import_catalog_file(catalog_file, catalog_type):
                        imported_count += 1
            
            _logger.info(f"Scanned catalog directory, imported {imported_count} files")
            return True
            
        except Exception as exc:
            _logger.error(f"Failed to scan catalog directory: {exc}")
            return False
    
    def _guess_catalog_type(self, catalog_file: Path) -> str:
        """Guess the catalog type based on filename."""
        name = catalog_file.name.lower()
        
        if 'redump' in name:
            return 'redump'
        elif 'no-intro' in name or 'no_intro' in name:
            return 'no-intro'
        elif 'dat' in name:
            return 'datfile'
        else:
            # Try to detect based on content
            try:
                with open(catalog_file, 'r', encoding='utf-8', errors='ignore') as f:
                    sample = f.read(1024)
                
                if re.search(r'[a-fA-F0-9]{32}\s+\d+\s+', sample):
                    return 'redump'
                elif re.search(r'[a-fA-F0-9]{40}\s+\d+\s+', sample):
                    return 'no-intro'
                else:
                    return 'generic'
            except Exception:
                return 'generic'


class ChecksumCalculator:
    """Calculates checksums for files."""
    
    def __init__(self):
        """Initialize checksum calculator."""
        _logger.info("Checksum calculator initialized")
    
    def calculate_sha256(self, file_path: Path) -> Optional[str]:
        """Calculate SHA256 checksum for a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            SHA256 checksum as hex string, or None if calculation failed
        """
        try:
            sha256_hash = hashlib.sha256()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(chunk)
            return sha256_hash.hexdigest()
        except Exception as exc:
            _logger.error(f"Failed to calculate SHA256 for {file_path}: {exc}")
            return None
    
    def calculate_sha1(self, file_path: Path) -> Optional[str]:
        """Calculate SHA1 checksum for a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            SHA1 checksum as hex string, or None if calculation failed
        """
        try:
            sha1_hash = hashlib.sha1()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    sha1_hash.update(chunk)
            return sha1_hash.hexdigest()
        except Exception as exc:
            _logger.error(f"Failed to calculate SHA1 for {file_path}: {exc}")
            return None
    
    def calculate_md5(self, file_path: Path) -> Optional[str]:
        """Calculate MD5 checksum for a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            MD5 checksum as hex string, or None if calculation failed
        """
        try:
            md5_hash = hashlib.md5()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    md5_hash.update(chunk)
            return md5_hash.hexdigest()
        except Exception as exc:
            _logger.error(f"Failed to calculate MD5 for {file_path}: {exc}")
            return None
    
    def calculate_crc32(self, file_path: Path) -> Optional[str]:
        """Calculate CRC32 checksum for a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            CRC32 checksum as hex string, or None if calculation failed
        """
        try:
            import zlib
            crc32_hash = 0
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    crc32_hash = zlib.crc32(chunk, crc32_hash)
            return format(crc32_hash & 0xffffffff, '08x')
        except Exception as exc:
            _logger.error(f"Failed to calculate CRC32 for {file_path}: {exc}")
            return None
    
    def verify_checksum(self, file_path: Path, expected_checksum: str, algorithm: str = 'sha256') -> bool:
        """Verify file checksum.
        
        Args:
            file_path: Path to file
            expected_checksum: Expected checksum value
            algorithm: Checksum algorithm ('sha256', 'sha1', 'md5', 'crc32')
            
        Returns:
            True if checksum matches, False otherwise
        """
        if algorithm.lower() == 'sha256':
            calculated = self.calculate_sha256(file_path)
        elif algorithm.lower() == 'sha1':
            calculated = self.calculate_sha1(file_path)
        elif algorithm.lower() == 'md5':
            calculated = self.calculate_md5(file_path)
        elif algorithm.lower() == 'crc32':
            calculated = self.calculate_crc32(file_path)
        else:
            _logger.error(f"Unsupported checksum algorithm: {algorithm}")
            return False
        
        if calculated is None:
            return False
        
        return calculated.lower() == expected_checksum.lower()