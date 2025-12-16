"""Download management (curl-based) for CacheInfinity networking."""

from __future__ import annotations

import hashlib
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, List, Any

from ..cache.checksum import ChecksumCalculator


_logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    """Result of a download operation."""
    
    success: bool
    file_path: Optional[Path]
    size: int
    duration: float
    error_message: Optional[str] = None
    checksum: Optional[str] = None
    verified: bool = False


@dataclass
class DownloadProgress:
    """Download progress information."""
    
    downloaded: int
    total: int
    speed: float  # bytes per second
    elapsed: float  # seconds
    status: str  # 'pending', 'downloading', 'completed', 'failed'


class Fetcher:
    """Manages downloads using curl with robust retry and resume capabilities."""
    
    def __init__(self, cookie_jars: Dict[str, 'CookieJarDefinition'],
                 max_retries: int = 3, retry_delay: int = 5,
                 verify_checksums: bool = True, max_concurrent: int = 3):
        """Initialize fetcher.
        
        Args:
            cookie_jars: Cookie jar definitions for authenticated domains
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds
            verify_checksums: Whether to verify checksums after download
            max_concurrent: Maximum number of concurrent downloads
        """
        self.cookie_jars = cookie_jars
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.verify_checksums = verify_checksums
        self.max_concurrent = max_concurrent
        self.checksum_calc = ChecksumCalculator()
        _logger.info("Fetcher initialized")
        
    def download_file(self, url: str, destination: Path,
                      resume: bool = True, timeout: int = 300,
                      expected_checksum: Optional[str] = None) -> DownloadResult:
        """Download a file using curl with cookie support and checksum verification.
        
        Args:
            url: URL to download from
            destination: Path where to save the file
            resume: Whether to resume partial downloads
            timeout: Download timeout in seconds
            expected_checksum: Expected SHA-256 checksum for verification
            
        Returns:
            DownloadResult with operation status and checksum
        """
        start_time = time.time()
        domain = self._extract_domain(url)
        
        for attempt in range(self.max_retries + 1):
            try:
                # Prepare curl command with enhanced options
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
                    
                    # Calculate checksum if verification is enabled
                    checksum = None
                    verified = False
                    
                    if self.verify_checksums and size > 0:
                        try:
                            checksum = self.checksum_calc.calculate_sha256(destination)
                            
                            if expected_checksum:
                                verified = checksum.lower() == expected_checksum.lower()
                                if not verified:
                                    _logger.warning(
                                        f"Checksum verification failed for {destination}: "
                                        f"expected {expected_checksum}, got {checksum}"
                                    )
                            else:
                                _logger.info(f"Calculated checksum for {destination}: {checksum}")
                                
                        except Exception as e:
                            _logger.warning(f"Failed to calculate checksum for {destination}: {e}")
                    
                    _logger.info(f"Download successful: {destination} ({size} bytes)")
                    return DownloadResult(
                        success=True,
                        file_path=destination,
                        size=size,
                        duration=duration,
                        checksum=checksum,
                        verified=verified if expected_checksum else True
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
        """Build curl command for download with enhanced cookie support.
        
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
            "--output", str(destination),
            "--max-redirs", "10",
            "--location-trusted",  # Allow cookies to be sent to redirect hosts
            "--compressed",  # Support for compressed responses
            "--speed-limit", "1024",  # Minimum speed in bytes/sec (1KB/s)
            "--speed-time", "30",  # Timeout if speed is below limit for 30 seconds
            "--tcp-fastopen",  # Use TCP Fast Open if available
            "--ipv4"  # Prefer IPv4 to avoid IPv6 issues
        ]
        
        # Add cookie jar if available for domain
        cookie_jar = self.cookie_jars.get(domain)
        if cookie_jar and cookie_jar.cookie_jar.exists():
            cmd.extend(["--cookie", str(cookie_jar.cookie_jar)])
            cmd.extend(["--cookie-jar", str(cookie_jar.cookie_jar)])
            
        # Add credentials if available
        if cookie_jar and cookie_jar.credfile and cookie_jar.credfile.exists():
            try:
                # Try to read credentials from credfile
                with open(cookie_jar.credfile, 'r') as f:
                    credentials = f.read().strip()
                    if ':' in credentials:
                        cmd.extend(["--user", credentials])
            except Exception as e:
                _logger.warning(f"Failed to read credentials from {cookie_jar.credfile}: {e}")
            
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
                'downloaded': 0,
                'total': 0,
                'progress': 0,
                'speed': 0,
                'elapsed': 0
            }
            
        size = destination.stat().st_size
        return {
            'status': 'in_progress',
            'downloaded': size,
            'total': 0,  # Would need to be known from Content-Length header
            'progress': size,
            'speed': 0,  # Would need to be calculated over time
            'elapsed': 0
        }
    
    def refresh_cookies(self, domain: str) -> bool:
        """Refresh cookies for a specific domain using credentials.
        
        Args:
            domain: Domain to refresh cookies for
            
        Returns:
            True if cookies were refreshed successfully, False otherwise
        """
        cookie_jar = self.cookie_jars.get(domain)
        if not cookie_jar or not cookie_jar.credfile or not cookie_jar.credfile.exists():
            _logger.warning(f"No credentials available for domain: {domain}")
            return False
        
        try:
            # Read credentials
            with open(cookie_jar.credfile, 'r') as f:
                credentials = f.read().strip()
                if ':' not in credentials:
                    _logger.error(f"Invalid credentials format for domain: {domain}")
                    return False
                
                username, password = credentials.split(':', 1)
            
            # Try to refresh cookies by making a request to a known endpoint
            # This would typically be a login endpoint specific to the domain
            refresh_urls = {
                'archive.org': 'https://archive.org/account/login.php',
                'the-eye.eu': 'https://the-eye.eu/'
            }
            
            refresh_url = refresh_urls.get(domain)
            if not refresh_url:
                _logger.warning(f"No known refresh endpoint for domain: {domain}")
                return False
            
            # Build curl command to refresh cookies
            cmd = [
                "curl",
                "--silent",
                "--show-error",
                "--fail",
                "--location",
                "--connect-timeout", "30",
                "--max-time", "60",
                "--user", f"{username}:{password}",
                "--cookie-jar", str(cookie_jar.cookie_jar),
                refresh_url
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=90
            )
            
            if result.returncode == 0:
                _logger.info(f"Successfully refreshed cookies for domain: {domain}")
                return True
            else:
                _logger.warning(f"Failed to refresh cookies for domain {domain}: {result.stderr}")
                return False
                
        except Exception as e:
            _logger.error(f"Error refreshing cookies for domain {domain}: {e}")
            return False
    
    def batch_download(self, downloads: List[Dict[str, Any]],
                      max_concurrent: int = 3) -> Dict[str, DownloadResult]:
        """Download multiple files concurrently with rate limiting.
        
        Args:
            downloads: List of download specifications with url, destination, etc.
            max_concurrent: Maximum number of concurrent downloads
            
        Returns:
            Dictionary mapping URLs to download results
        """
        import concurrent.futures
        import threading
        
        results = {}
        semaphore = threading.Semaphore(max_concurrent)
        
        def download_with_semaphore(download_spec):
            with semaphore:
                url = download_spec['url']
                destination = Path(download_spec['destination'])
                expected_checksum = download_spec.get('checksum')
                
                return url, self.download_file(
                    url, destination,
                    resume=download_spec.get('resume', True),
                    timeout=download_spec.get('timeout', 300),
                    expected_checksum=expected_checksum
                )
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = [
                executor.submit(download_with_semaphore, download_spec)
                for download_spec in downloads
            ]
            
            for future in concurrent.futures.as_completed(futures):
                url, result = future.result()
                results[url] = result
        
        return results
    
    def download_with_partial_support(self, url: str, destination: Path,
                                    timeout: int = 300, expected_checksum: Optional[str] = None) -> DownloadResult:
        """Download with enhanced partial file support and better error handling.
        
        Args:
            url: URL to download from
            destination: Path where to save the file
            timeout: Download timeout in seconds
            expected_checksum: Expected SHA-256 checksum for verification
            
        Returns:
            DownloadResult with operation status and checksum
        """
        # Check if we have a partial file
        partial_file = destination.with_suffix(destination.suffix + '.part')
        
        # If partial file exists and is recent, try to resume
        if partial_file.exists():
            partial_size = partial_file.stat().st_size
            if partial_size > 0:
                _logger.info(f"Found partial download: {partial_file} ({partial_size} bytes)")
                # Move partial to destination and resume
                partial_file.rename(destination)
        
        # Try normal download
        result = self.download_file(url, destination, resume=True, timeout=timeout, expected_checksum=expected_checksum)
        
        # If download failed but we have a partial file, keep it for next attempt
        if not result.success and destination.exists():
            file_size = destination.stat().st_size
            if file_size > 0:
                _logger.info(f"Keeping partial download: {destination} ({file_size} bytes)")
        
        return result
    
    def classify_download_error(self, error_message: str) -> str:
        """Classify download error for better handling.
        
        Args:
            error_message: Error message from download attempt
            
        Returns:
            Error category (auth, network, timeout, etc.)
        """
        error_lower = error_message.lower()
        
        if any(keyword in error_lower for keyword in ['401', '403', 'unauthorized', 'forbidden', 'authentication']):
            return 'authentication'
        elif any(keyword in error_lower for keyword in ['timeout', 'timed out', 'operation timed out']):
            return 'timeout'
        elif any(keyword in error_lower for keyword in ['network', 'connection', 'refused', 'reset']):
            return 'network'
        elif any(keyword in error_lower for keyword in ['disk', 'space', 'no space', 'quota']):
            return 'disk'
        elif any(keyword in error_lower for keyword in ['checksum', 'verification', 'hash']):
            return 'checksum'
        else:
            return 'unknown'