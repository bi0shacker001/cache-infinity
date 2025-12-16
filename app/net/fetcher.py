"""Download management (curl-based) for CacheInfinity networking."""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from ..core.config import CookieJarDefinition

_logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    """Result of a download operation."""
    
    success: bool
    file_path: Optional[Path]
    size: int
    duration: float
    error_message: Optional[str] = None


class Fetcher:
    """Manages downloads using curl with robust retry and resume capabilities."""
    
    def __init__(self, cookie_jars: Dict[str, CookieJarDefinition], 
                 max_retries: int = 3, retry_delay: int = 5):
        """Initialize fetcher.
        
        Args:
            cookie_jars: Cookie jar definitions for authenticated domains
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds
        """
        self.cookie_jars = cookie_jars
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        _logger.info("Fetcher initialized")
        
    def download_file(self, url: str, destination: Path, 
                     resume: bool = True, timeout: int = 300) -> DownloadResult:
        """Download a file using curl.
        
        Args:
            url: URL to download from
            destination: Path where to save the file
            resume: Whether to resume partial downloads
            timeout: Download timeout in seconds
            
        Returns:
            DownloadResult with operation status
        """
        start_time = time.time()
        domain = self._extract_domain(url)
        
        for attempt in range(self.max_retries + 1):
            try:
                # Prepare curl command
                cmd = self._build_curl_command(url, destination, domain, resume, timeout)
                
                # Execute download
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=timeout + 60  # Extra time for subprocess overhead
                )
                
                # Check if file was created and has content
                if destination.exists() and destination.stat().st_size > 0:
                    duration = time.time() - start_time
                    size = destination.stat().st_size
                    _logger.info(f"Download successful: {destination} ({size} bytes)")
                    return DownloadResult(
                        success=True,
                        file_path=destination,
                        size=size,
                        duration=duration
                    )
                else:
                    raise Exception("Download completed but file is missing or empty")
                    
            except subprocess.CalledProcessError as e:
                error_msg = f"Curl failed: {e.stderr}"
                _logger.warning(f"Download attempt {attempt + 1} failed: {error_msg}")
                
                if attempt < self.max_retries:
                    _logger.info(f"Retrying in {self.retry_delay} seconds...")
                    time.sleep(self.retry_delay)
                else:
                    duration = time.time() - start_time
                    return DownloadResult(
                        success=False,
                        file_path=None,
                        size=0,
                        duration=duration,
                        error_message=error_msg
                    )
                    
            except subprocess.TimeoutExpired:
                error_msg = f"Download timed out after {timeout} seconds"
                _logger.warning(f"Download attempt {attempt + 1} timed out")
                
                if attempt < self.max_retries:
                    _logger.info(f"Retrying in {self.retry_delay} seconds...")
                    time.sleep(self.retry_delay)
                else:
                    duration = time.time() - start_time
                    return DownloadResult(
                        success=False,
                        file_path=None,
                        size=0,
                        duration=duration,
                        error_message=error_msg
                    )
                    
            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                _logger.error(f"Download attempt {attempt + 1} failed: {error_msg}")
                
                if attempt < self.max_retries:
                    _logger.info(f"Retrying in {self.retry_delay} seconds...")
                    time.sleep(self.retry_delay)
                else:
                    duration = time.time() - start_time
                    return DownloadResult(
                        success=False,
                        file_path=None,
                        size=0,
                        duration=duration,
                        error_message=error_msg
                    )
        
        # This should never be reached, but just in case
        duration = time.time() - start_time
        return DownloadResult(
            success=False,
            file_path=None,
            size=0,
            duration=duration,
            error_message="Unknown error"
        )
        
    def _build_curl_command(self, url: str, destination: Path, 
                           domain: str, resume: bool, timeout: int) -> list[str]:
        """Build curl command for download.
        
        Args:
            url: URL to download from
            destination: Path where to save the file
            domain: Domain of the URL
            resume: Whether to resume partial downloads
            timeout: Download timeout in seconds
            
        Returns:
            List of command arguments for subprocess
        """
        cmd = [
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--location",
            "--retry", "3",
            "--retry-delay", "5",
            "--retry-connrefused",
            "--connect-timeout", str(30),
            "--max-time", str(timeout),
            "--continue-at", "-" if resume else "0",
            "--output", str(destination)
        ]
        
        # Add cookie jar if available for domain
        cookie_jar = self.cookie_jars.get(domain)
        if cookie_jar and cookie_jar.cookie_jar.exists():
            cmd.extend(["--cookie", str(cookie_jar.cookie_jar)])
            cmd.extend(["--cookie-jar", str(cookie_jar.cookie_jar)])
            
        # Add URL at the end
        cmd.append(url)
        
        return cmd
        
    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL.
        
        Args:
            url: URL to extract domain from
            
        Returns:
            Domain string
        """
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc
        
    def check_file_availability(self, url: str, timeout: int = 30) -> bool:
        """Check if a file is available for download.
        
        Args:
            url: URL to check
            timeout: Timeout in seconds
            
        Returns:
            True if file is available, False otherwise
        """
        try:
            cmd = [
                "curl",
                "--silent",
                "--head",
                "--fail",
                "--connect-timeout", str(timeout),
                "--max-time", str(timeout),
                url
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 10
            )
            
            return result.returncode == 0
            
        except Exception as e:
            _logger.warning(f"Failed to check file availability: {e}")
            return False
            
    def get_download_progress(self, destination: Path) -> Dict[str, Any]:
        """Get download progress information.
        
        Args:
            destination: Path to the download file
            
        Returns:
            Dictionary with progress information
        """
        if not destination.exists():
            return {
                'status': 'not_started',
                'size': 0,
                'progress': 0
            }
            
        size = destination.stat().st_size
        return {
            'status': 'in_progress',
            'size': size,
            'progress': size  # For now, just return size
        }