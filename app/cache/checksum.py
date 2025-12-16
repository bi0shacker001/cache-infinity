"""Checksum utilities for CacheInfinity."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)


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
    
    def verify_checksum(self, file_path: Path, expected_checksum: str, algorithm: str = 'sha256') -> bool:
        """Verify file checksum.
        
        Args:
            file_path: Path to file
            expected_checksum: Expected checksum value
            algorithm: Checksum algorithm ('sha256' or 'md5')
            
        Returns:
            True if checksum matches, False otherwise
        """
        if algorithm.lower() == 'sha256':
            calculated = self.calculate_sha256(file_path)
        elif algorithm.lower() == 'md5':
            calculated = self.calculate_md5(file_path)
        else:
            _logger.error(f"Unsupported checksum algorithm: {algorithm}")
            return False
        
        if calculated is None:
            return False
        
        return calculated.lower() == expected_checksum.lower()