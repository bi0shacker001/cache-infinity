"""Download management (PycURL-based) for CacheInfinity networking."""

from __future__ import annotations

import hashlib
import io
import logging
import os
import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Optional, List, Any, Callable, TYPE_CHECKING
from urllib.parse import urlparse

from core.config import CookieJarDefinition
from cache.checksum import ChecksumCalculator
from storage.staging import StagingArea, StagingDefinition
from db.dbmanage import DatabaseManager

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from storage.datadir import DatadirRegistry


def _import_pycurl():
    try:
        import pycurl  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError(
            "pycurl is required for CacheInfinity networking. "
            "Install project dependencies (including 'pycurl') to enable downloads/indexing."
        ) from exc
    return pycurl


def _import_rclone():
    try:
        from rclone_python import rclone  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError(
            "rclone-python is required for rclone:// transfers. "
            "Install project dependencies (including 'rclone-python') to enable cloud downloads."
        ) from exc
    return rclone


def _is_rclone_url(url: str) -> bool:
    return url.startswith("rclone:") or url.startswith("rclone://")


def _rclone_spec(url: str) -> str:
    if url.startswith("rclone://"):
        parsed = urlparse(url)
        remote = parsed.netloc
        path = parsed.path.lstrip("/")
        return f"{remote}:{path}"
    if url.startswith("rclone:"):
        return url[len("rclone:"):]
    return url


def _normalize_url_handler(handler_value: str | None) -> str:
    if not handler_value:
        return "auto"
    value = str(handler_value).strip().lower()
    if value in ("auto", "default"):
        return "auto"
    if value in ("rclone", "rclone-python"):
        return "rclone"
    if value in ("http", "https", "ftp", "ftps"):
        return value
    return "auto"


def _resolve_url_handler(url: str, handler_value: str | None) -> str:
    handler = _normalize_url_handler(handler_value)
    if handler != "auto":
        return handler
    if _is_rclone_url(url):
        return "rclone"
    if url.startswith("ftp"):
        return "ftp"
    if url.startswith("http"):
        return "http"
    return "auto"


@contextmanager
def _rclone_env(config_path: Optional[Path]):
    if not config_path:
        yield
        return
    key = "RCLONE_CONFIG"
    previous = os.environ.get(key)
    os.environ[key] = str(config_path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


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
    """Manages downloads using PycURL with robust retry and resume capabilities."""
    
    def __init__(
        self,
        cookie_jars: Dict[str, CookieJarDefinition],
        max_retries: int = 3,
        retry_delay: int = 5,
        verify_checksums: bool = True,
        max_concurrent: int = 3,
        staging_definition: Optional[StagingDefinition] = None,
        zip_caching_limits: Optional[Dict[str, Any]] = None,
    ):
        """Initialize fetcher.
         
        Args:
            cookie_jars: Cookie jar definitions for authenticated domains
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds
            verify_checksums: Whether to verify checksums after download
            max_concurrent: Maximum number of concurrent downloads
            staging_definition: Staging area configuration
            zip_caching_limits: Zip caching configuration limits
        """
        self.cookie_jars = cookie_jars
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.verify_checksums = verify_checksums
        self.max_concurrent = max_concurrent
        self.checksum_calc = ChecksumCalculator()
        self._active_downloads = 0
        self._download_lock = threading.Lock()
        self._progress_callbacks: List[Callable[[str, DownloadProgress], None]] = []
        
        # Initialize staging area for zip caching
        self.staging_definition = staging_definition or StagingDefinition()
        self.staging_area = StagingArea(self.staging_definition)
        
        # Initialize zip cache manager
        self.zip_caching_limits = zip_caching_limits or {
            "max_zip_total_gb": 100,
            "one_zip_cache_at_a_time": False
        }
        self.zip_cache_manager = self.staging_area.get_zip_cache_manager(
            self.zip_caching_limits,
            self._download_zip_callback,
        )
        
        _logger.debug("Fetcher initialized with zip caching support")
        
    def add_progress_callback(self, callback: Callable[[str, DownloadProgress], None]) -> None:
        """Add a callback to receive download progress updates.
        
        Args:
            callback: Function that takes (url, progress) as arguments
        """
        self._progress_callbacks.append(callback)
    
    def download_file(
        self,
        url: str,
        destination: Path,
        resume: bool = True,
        timeout: int = 300,
        expected_checksum: Optional[str] = None,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
        url_handler: Optional[str] = None,
    ) -> DownloadResult:
        """Download a file using PycURL with cookie support and checksum verification.
        
        Args:
            url: URL to download from
            destination: Path where to save the file
            resume: Whether to resume partial downloads
            timeout: Download timeout in seconds
            expected_checksum: Expected SHA-256 checksum for verification
            progress_callback: Optional callback for progress updates
            
        Returns:
            DownloadResult with operation status and checksum
        """
        handler = _resolve_url_handler(url, url_handler)
        if handler == "rclone":
            return self._download_rclone(
                url=url,
                destination=destination,
                expected_checksum=expected_checksum,
            )
        pycurl = _import_pycurl()
        start_time = time.time()
        domain = self._extract_domain(url)

        while True:
            with self._download_lock:
                if self._active_downloads < self.max_concurrent:
                    self._active_downloads += 1
                    break
            time.sleep(0.2)

        try:
            last_error: str | None = None
            for attempt in range(self.max_retries + 1):
                try:
                    if destination.exists() and not resume:
                        destination.unlink()
                    ok, status_code, error_message = self._download_once(
                        pycurl,
                        url=url,
                        destination=destination,
                        domain=domain,
                        resume=resume,
                        timeout=timeout,
                        progress_callback=progress_callback,
                    )

                    if ok:
                        if not destination.exists() or destination.stat().st_size <= 0:
                            raise RuntimeError("Download reported success but destination is empty")

                        duration = time.time() - start_time
                        size = destination.stat().st_size

                        checksum = None
                        verified = False
                        if self.verify_checksums and size > 0:
                            try:
                                checksum = self.checksum_calc.calculate_sha256(destination)
                                if expected_checksum:
                                    verified = checksum.lower() == expected_checksum.lower()
                                    if not verified:
                                        _logger.warning(
                                            "Checksum verification failed for %s: expected %s, got %s",
                                            destination,
                                            expected_checksum,
                                            checksum,
                                        )
                                else:
                                    _logger.debug("Calculated checksum for %s: %s", destination, checksum)
                            except Exception as exc:
                                _logger.warning("Failed to calculate checksum for %s: %s", destination, exc)

                        _logger.debug("Download successful: %s (%d bytes)", destination, size)
                        return DownloadResult(
                            success=True,
                            file_path=destination,
                            size=size,
                            duration=duration,
                            error_message=None,
                            checksum=checksum,
                            verified=verified if expected_checksum else True,
                        )

                    last_error = error_message or f"HTTP {status_code}" if status_code else "Download failed"

                    is_retryable_http = status_code in (408, 429) or (status_code is not None and 500 <= status_code <= 599)
                    is_retryable = status_code is None or is_retryable_http

                    if attempt < self.max_retries and is_retryable:
                        delay = self.retry_delay * (2 ** attempt)
                        _logger.debug("Download attempt %d failed (%s), retrying in %ds", attempt + 1, last_error, delay)
                        time.sleep(delay)
                        continue

                    duration = time.time() - start_time
                    return DownloadResult(
                        success=False,
                        file_path=None,
                        size=0,
                        duration=duration,
                        error_message=last_error,
                    )
                except Exception as exc:
                    last_error = f"Download attempt {attempt + 1} failed: {exc}"
                    _logger.warning(last_error)
                    if attempt < self.max_retries:
                        delay = self.retry_delay * (2 ** attempt)
                        _logger.debug("Retrying in %ds...", delay)
                        time.sleep(delay)
                    else:
                        duration = time.time() - start_time
                        return DownloadResult(
                            success=False,
                            file_path=None,
                            size=0,
                            duration=duration,
                            error_message=last_error,
                        )

            duration = time.time() - start_time
            return DownloadResult(success=False, file_path=None, size=0, duration=duration, error_message=last_error)
        finally:
            with self._download_lock:
                self._active_downloads -= 1
    
    def _download_once(
        self,
        pycurl,
        *,
        url: str,
        destination: Path,
        domain: str,
        resume: bool,
        timeout: int,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
    ) -> tuple[bool, int | None, str | None]:
        destination.parent.mkdir(parents=True, exist_ok=True)

        cookie_definition = self.cookie_jars.get(domain)
        cookie_content = self._load_cookie_content(cookie_definition)
        userpwd = None

        start_ts = time.time()
        existing_size = destination.stat().st_size if resume and destination.exists() else 0
        mode = "ab" if existing_size > 0 else "wb"

        header_lines: list[bytes] = []
        downloaded_ref: dict[str, float] = {"dlnow": float(existing_size)}

        def _emit_progress(dltotal: float, dlnow: float) -> int:
            now = time.time()
            elapsed = max(0.001, now - start_ts)
            downloaded_ref["dlnow"] = dlnow
            progress = DownloadProgress(
                downloaded=int(dlnow),
                total=int(dltotal) if dltotal else 0,
                speed=float(dlnow) / elapsed,
                elapsed=elapsed,
                status="downloading",
            )
            for callback in self._progress_callbacks:
                try:
                    callback(url, progress)
                except Exception:
                    pass
            if progress_callback:
                try:
                    progress_callback(progress)
                except Exception:
                    pass
            return 0

        curl = pycurl.Curl()
        try:
            with destination.open(mode) as handle:
                curl.setopt(pycurl.URL, url)
                curl.setopt(pycurl.FOLLOWLOCATION, 1)
                curl.setopt(pycurl.MAXREDIRS, 10)
                curl.setopt(pycurl.CONNECTTIMEOUT, 30)
                curl.setopt(pycurl.TIMEOUT, timeout)
                curl.setopt(pycurl.NOSIGNAL, 1)
                curl.setopt(pycurl.USERAGENT, "CacheInfinity/0.1")
                curl.setopt(pycurl.WRITEDATA, handle)
                curl.setopt(pycurl.HEADERFUNCTION, header_lines.append)
                curl.setopt(pycurl.SSL_VERIFYPEER, 1)
                curl.setopt(pycurl.SSL_VERIFYHOST, 2)
                curl.setopt(pycurl.LOW_SPEED_LIMIT, 1024)
                curl.setopt(pycurl.LOW_SPEED_TIME, 30)
                curl.setopt(pycurl.IPRESOLVE, pycurl.IPRESOLVE_V4)
                curl.setopt(pycurl.ACCEPT_ENCODING, "")

                if existing_size > 0:
                    curl.setopt(pycurl.RESUME_FROM, int(existing_size))

                if cookie_content:
                    self._apply_cookies(curl, pycurl, cookie_content)

                if userpwd:
                    curl.setopt(pycurl.USERPWD, userpwd)

                curl.setopt(pycurl.NOPROGRESS, 0)
                curl.setopt(pycurl.XFERINFOFUNCTION, lambda dlt, dln, ult, uln: _emit_progress(dlt, dln))

                curl.perform()
                status_code = int(curl.getinfo(pycurl.RESPONSE_CODE) or 0)
        except pycurl.error as exc:
            errno, message = exc.args
            return False, None, f"PycURL error {errno}: {message}"
        finally:
            curl.close()

        if status_code in (200, 206):
            return True, status_code, None
        return False, status_code, f"HTTP {status_code}"

    def _download_rclone(
        self,
        *,
        url: str,
        destination: Path,
        expected_checksum: Optional[str] = None,
    ) -> DownloadResult:
        start_time = time.time()
        remote = _rclone_spec(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            try:
                # Get Rclone configuration from database
                rclone_config_path = self._get_rclone_config_path()
                with _rclone_env(rclone_config_path):
                    rclone = _import_rclone()
                    if hasattr(rclone, "copyto"):
                        rclone.copyto(remote, str(destination))
                    elif hasattr(rclone, "copy"):
                        rclone.copy(remote, str(destination.parent))
                    else:
                        raise RuntimeError("rclone-python missing copy/copyto support")

                if not destination.exists():
                    raise RuntimeError("Rclone transfer finished but destination is missing")
                size = destination.stat().st_size
                duration = time.time() - start_time
                checksum = None
                verified = False
                if self.verify_checksums and size > 0:
                    try:
                        checksum = self.checksum_calc.calculate_sha256(destination)
                        if expected_checksum:
                            verified = checksum.lower() == expected_checksum.lower()
                    except Exception as exc:
                        _logger.warning("Failed to calculate checksum for %s: %s", destination, exc)
                return DownloadResult(
                    success=True,
                    file_path=destination,
                    size=size,
                    duration=duration,
                    checksum=checksum,
                    verified=verified,
                )
            except Exception as exc:
                last_error = str(exc)
                _logger.warning("Rclone download failed (attempt %d/%d): %s", attempt + 1, self.max_retries + 1, exc)
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
        return DownloadResult(
            success=False,
            file_path=None,
            size=0,
            duration=time.time() - start_time,
            error_message=last_error or "Rclone transfer failed",
        )
        
    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL.
        
        Args:
            url: URL to extract domain from
            
        Returns:
            Domain string
        """
        return urlparse(url).netloc

    def _load_cookie_content(self, cookie_definition: CookieJarDefinition | None) -> str:
        if not cookie_definition:
            return ""
        return cookie_definition.cookie_content or ""

    def _apply_cookies(self, curl, pycurl, cookie_content: str) -> None:
        curl.setopt(pycurl.COOKIELIST, "ALL")
        for line in cookie_content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            curl.setopt(pycurl.COOKIELIST, stripped)
    
    def check_file_availability(self, url: str, timeout: int = 30) -> bool:
        """Check if a file is available for download.
        
        Args:
            url: URL to check
            timeout: Timeout in seconds
            
        Returns:
            True if file is available, False otherwise
        """
        pycurl = _import_pycurl()
        try:
            curl = pycurl.Curl()
            try:
                sink = io.BytesIO()
                curl.setopt(pycurl.URL, url)
                curl.setopt(pycurl.NOBODY, 1)
                curl.setopt(pycurl.FOLLOWLOCATION, 1)
                curl.setopt(pycurl.MAXREDIRS, 10)
                curl.setopt(pycurl.CONNECTTIMEOUT, min(30, timeout))
                curl.setopt(pycurl.TIMEOUT, timeout)
                curl.setopt(pycurl.NOSIGNAL, 1)
                curl.setopt(pycurl.USERAGENT, "CacheInfinity/0.1")
                curl.setopt(pycurl.WRITEDATA, sink)
                curl.perform()
                status_code = int(curl.getinfo(pycurl.RESPONSE_CODE) or 0)
                return 200 <= status_code < 400
            finally:
                curl.close()
        except Exception as exc:
            _logger.warning("Failed to check file availability: %s", exc)
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
    
    def refresh_cookies(self, domain: str) -> tuple[bool, str | None]:
        """Refresh cookies for a specific domain using credentials.
        
        Args:
            domain: Domain to refresh cookies for
            
        Returns:
            Tuple of (success, cookie_content)
        """
        _logger.warning("Cookie refresh is disabled; credentials are not stored on disk.")
        return False, None

    def _format_cookie_list(self, cookie_list: list[str] | None) -> str:
        if not cookie_list:
            return ""
        header = [
            "# Netscape HTTP Cookie File",
            "# This file is generated by CacheInfinity.",
        ]
        lines = header + [line for line in cookie_list if line and not line.startswith("#")]
        return "\n".join(lines) + "\n"
    
    
    def batch_download(
        self,
        downloads: List[Dict[str, Any]],
        *,
        max_concurrent: int = 3,
        progress_callback: Callable[[str, DownloadProgress], None] | None = None,
    ) -> Dict[str, DownloadResult]:
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

                def _progress(progress: DownloadProgress) -> None:
                    if progress_callback:
                        try:
                            progress_callback(url, progress)
                        except Exception:
                            pass

                return url, self.download_file(
                    url, destination,
                    resume=download_spec.get('resume', True),
                    timeout=download_spec.get('timeout', 300),
                    expected_checksum=expected_checksum,
                    url_handler=download_spec.get("url_handler"),
                    progress_callback=_progress,
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
    
    def download_with_staging(
        self,
        url: str,
        destination: Path,
        expected_checksum: Optional[str] = None,
        staging_dir: Optional[Path] = None,
        url_handler: Optional[str] = None,
    ) -> bool:
        """Download with enhanced staging and datadir integration.
        
        Args:
            url: URL to download from
            destination: Path relative to datadir where file should be stored
            expected_checksum: Expected SHA-256 checksum for verification
            staging_dir: Optional staging directory
            
        Returns:
            True if download and caching was successful
        """
        try:
            # Create staging file
            import tempfile
            staging_dir = staging_dir or destination.parent
            staging_dir.mkdir(parents=True, exist_ok=True)
            
            with tempfile.NamedTemporaryFile(dir=staging_dir, delete=False, suffix='.tmp') as staging_file:
                staging_path = Path(staging_file.name)
            
            # Download to staging area
            _logger.debug(f"Starting download: {url} -> {destination}")
            result = self.download_file(
                url=url,
                destination=staging_path,
                resume=True,
                timeout=300,
                expected_checksum=expected_checksum,
                url_handler=url_handler,
            )
            
            if not result.success:
                _logger.error(f"Download failed for {url}: {result.error_message}")
                # Clean up staging file
                if staging_path.exists():
                    staging_path.unlink()
                return False
            
            # Verify checksum if required
            if expected_checksum and not result.verified:
                _logger.error(f"Checksum verification failed for {url}")
                staging_path.unlink()
                return False
            
            # Move from staging to datadir
            destination.parent.mkdir(parents=True, exist_ok=True)
            
            # Atomic move from staging to datadir
            import shutil
            shutil.move(str(staging_path), str(destination))
            
            _logger.debug(f"Successfully downloaded and cached: {url} -> {destination}")
            return True
            
        except Exception as exc:
            _logger.error(f"Download with staging failed for {url}: {exc}")
            # Clean up staging file if it exists
            if 'staging_path' in locals() and staging_path.exists():
                staging_path.unlink()
            return False
    
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
    
    def get_active_downloads(self) -> int:
        """Get number of currently active downloads."""
        with self._download_lock:
            return self._active_downloads
    
    def set_max_concurrent(self, max_concurrent: int) -> None:
        """Set maximum number of concurrent downloads."""
        with self._download_lock:
            self.max_concurrent = max_concurrent
    
    def download_with_retry_strategy(self, url: str, destination: Path,
                                    retry_strategy: str = 'exponential',
                                    expected_checksum: Optional[str] = None) -> DownloadResult:
        """Download with configurable retry strategy.
        
        Args:
            url: URL to download from
            destination: Path where to save the file
            retry_strategy: Retry strategy ('exponential', 'linear', 'fixed')
            expected_checksum: Expected SHA-256 checksum for verification
            
        Returns:
            DownloadResult with operation status and checksum
        """
        original_max_retries = self.max_retries
        original_retry_delay = self.retry_delay
        
        try:
            if retry_strategy == 'exponential':
                # Already configured for exponential backoff
                pass
            elif retry_strategy == 'linear':
                # Linear backoff: delay increases linearly
                self.max_retries = 5
                self.retry_delay = 10
            elif retry_strategy == 'fixed':
                # Fixed delay between retries
                self.max_retries = 3
                self.retry_delay = 30
            else:
                _logger.warning(f"Unknown retry strategy: {retry_strategy}, using exponential")
            
            return self.download_file(url, destination, expected_checksum=expected_checksum)
            
        finally:
            # Restore original settings
            self.max_retries = original_max_retries
            self.retry_delay = original_retry_delay
    
    def download_zip_file(self, zip_url: str, destination: Path,
                         member_path: Optional[str] = None,
                         expected_checksum: Optional[str] = None) -> DownloadResult:
        """Download and extract a zip file using the zip cache manager.
        
        This method handles the zip caching logic according to the SPEC requirements:
        - Size limits for whole-zip vs individual-file mode
        - One-zip-at-a-time locking when enabled
        - Automatic mode selection based on file sizes
        
        Args:
            zip_url: URL to the zip file
            destination: Path where the extracted file should be stored
            member_path: Specific file within the zip to extract (for individual-file mode)
            expected_checksum: Expected SHA-256 checksum for verification
            
        Returns:
            DownloadResult with operation status and checksum
        """
        start_time = time.time()
        
        try:
            # Use the zip cache manager to handle the download and extraction
            result_path = self.zip_cache_manager.handle_zip_file(
                zip_url=zip_url,
                destination=destination,
                member_path=member_path
            )
            
            if result_path and result_path.exists():
                size = result_path.stat().st_size
                duration = time.time() - start_time
                
                # Verify checksum if required
                checksum = None
                verified = False
                if self.verify_checksums and size > 0:
                    try:
                        checksum = self.checksum_calc.calculate_sha256(result_path)
                        if expected_checksum:
                            verified = checksum.lower() == expected_checksum.lower()
                            if not verified:
                                _logger.warning(
                                    "Checksum verification failed for %s: expected %s, got %s",
                                    result_path, expected_checksum, checksum
                                )
                        else:
                            _logger.debug("Calculated checksum for %s: %s", result_path, checksum)
                    except Exception as exc:
                        _logger.warning("Failed to calculate checksum for %s: %s", result_path, exc)
                
                _logger.debug("Zip file download successful: %s (%d bytes)", result_path, size)
                return DownloadResult(
                    success=True,
                    file_path=result_path,
                    size=size,
                    duration=duration,
                    checksum=checksum,
                    verified=verified if expected_checksum else True,
                )
            else:
                duration = time.time() - start_time
                return DownloadResult(
                    success=False,
                    file_path=None,
                    size=0,
                    duration=duration,
                    error_message="Failed to extract zip file",
                )
                
        except Exception as exc:
            duration = time.time() - start_time
            _logger.error(f"Zip file download failed for {zip_url}: {exc}")
            return DownloadResult(
                success=False,
                file_path=None,
                size=0,
                duration=duration,
                error_message=str(exc),
            )

    def _download_zip_callback(self, url: str, destination: Path) -> DownloadResult:
        """Download a zip file for the zip cache manager."""
        return self.download_file(url, destination)
    
    def is_zip_file_url(self, url: str) -> bool:
        """Check if a URL points to a zip file.
        
        Args:
            url: URL to check
            
        Returns:
            True if the URL appears to point to a zip file
        """
        url_lower = url.lower()
        # Check if URL ends with .zip or contains .zip in the path
        return url_lower.endswith('.zip') or '.zip/' in url_lower
    
    def get_zip_member_path(self, subfolder: str) -> tuple[Optional[str], Optional[str]]:
        """Extract zip path and member path from subfolder specification.
        
        For zip-folder mode, the subfolder contains a directory segment ending in `.zip`,
        followed by an internal prefix.
        
        Example: "shareware_apps_r.zip/shareware_apps_r/"
        
        Args:
            subfolder: Subfolder specification from cachelink
            
        Returns:
            Tuple of (zip_path, member_path) or (None, None) if not a zip folder
        """
        if not subfolder or not isinstance(subfolder, str):
            return None, None
        
        # Split the subfolder into components
        parts = subfolder.strip('/').split('/')
        
        # Look for a part that ends with .zip
        for i, part in enumerate(parts):
            if part.endswith('.zip'):
                # Found zip file, the rest is the member path
                zip_path = '/'.join(parts[:i+1])
                member_path = '/'.join(parts[i+1:]) if i+1 < len(parts) else ""
                # Preserve trailing slash if present in original
                if subfolder.endswith('/') and member_path:
                    member_path += '/'
                return zip_path, member_path
        
        return None, None
    
    def should_use_zip_caching(self, url: str, subfolder: str) -> bool:
        """Determine if zip caching should be used for this URL and subfolder.
        
        Args:
            url: URL to check
            subfolder: Subfolder specification from cachelink
            
        Returns:
            True if zip caching should be used
        """
        # Check if URL points to a zip file
        if self.is_zip_file_url(url):
            return True
        
        # Check if subfolder contains zip-folder mode
        zip_path, member_path = self.get_zip_member_path(subfolder)
        return zip_path is not None
    
    def download_with_zip_caching(self, url: str, destination: Path,
                                 subfolder: str = "/",
                                 member_path: Optional[str] = None,
                                 expected_checksum: Optional[str] = None) -> DownloadResult:
        """Download file with automatic zip caching support.
        
        This method automatically detects if zip caching should be used and
        delegates to the appropriate download method.
        
        Args:
            url: URL to download from
            destination: Path where the file should be stored
            subfolder: Subfolder specification from cachelink
            member_path: Specific file within zip to extract
            expected_checksum: Expected SHA-256 checksum for verification
            
        Returns:
            DownloadResult with operation status and checksum
        """
        # Check if this should use zip caching
        if self.should_use_zip_caching(url, subfolder):
            _logger.debug(f"Using zip caching for {url}")
            return self.download_zip_file(
                zip_url=url,
                destination=destination,
                member_path=member_path,
                expected_checksum=expected_checksum
            )
        else:
            # Use regular download
            _logger.debug(f"Using regular download for {url}")
            return self.download_file(
                url=url,
                destination=destination,
                expected_checksum=expected_checksum
            )


def start_download_queue_thread(
    fetcher: Fetcher,
    index_db: DatabaseManager,
    datadir_registry: "DatadirRegistry",
    stop_event: threading.Event,
    *,
    interval_seconds: int = 300,
    limit: int = 10,
    max_concurrent: int = 3,
) -> threading.Thread:
    """Start a background thread to process queued downloads."""

    def _loop() -> None:
        while not stop_event.is_set():
            try:
                pending, id_by_url = _collect_pending_downloads(index_db, datadir_registry, limit=limit)
                if pending:
                    _logger.debug("Processing %d pending downloads", len(pending))

                    def _progress(url: str, progress) -> None:
                        job_id = id_by_url.get(url)
                        if job_id is None:
                            return
                        try:
                            _update_download_progress(index_db, job_id, progress.downloaded)
                        except Exception:
                            _logger.debug("Progress update failed for job %s", job_id)

                    results = fetcher.batch_download(
                        pending,
                        max_concurrent=max_concurrent,
                        progress_callback=_progress,
                    )
                    _update_pending_downloads(index_db, datadir_registry, pending, results)
                else:
                    _logger.debug("No pending downloads to process")
            except Exception as exc:  # pragma: no cover - defensive
                _logger.error("Fetcher queue failed: %s", exc, exc_info=True)
            stop_event.wait(interval_seconds)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread


def _collect_pending_downloads(
    index_db: DatabaseManager,
    datadir_registry: "DatadirRegistry",
    *,
    limit: int,
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    jobs = index_db.claim_pending_downloads(limit=limit)
    downloads: list[dict[str, Any]] = []
    id_by_url: dict[str, int] = {}
    for job in jobs:
        dest_rel = PurePosixPath(str(job.get("destination", "")).lstrip("/"))
        dest_path = datadir_registry.primary.resolve(dest_rel)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        url = job.get("url")
        if isinstance(url, str):
            id_by_url[url] = job.get("id")
        downloads.append(
            {
                "id": job.get("id"),
                "url": url,
                "destination": str(dest_path),
                "checksum": job.get("expected_checksum"),
                "resume": True,
                "timeout": 300,
                "relative_path": dest_rel,
            }
        )
    return downloads, id_by_url


def _update_pending_downloads(
    index_db: DatabaseManager,
    datadir_registry: "DatadirRegistry",
    jobs: list[dict[str, Any]],
    results: Dict[str, DownloadResult],
) -> None:
    for job in jobs:
        job_id = job.get("id")
        url = job.get("url")
        if job_id is None or not url:
            continue
        result = results.get(url)
        if not result:
            continue

        if result.success and getattr(result, "verified", True):
            rel = job.get("relative_path")
            if isinstance(rel, PurePosixPath):
                datadir_path = datadir_registry.primary.resolve(rel)
                if datadir_path.exists():
                    _record_backend_checksum(index_db, rel, datadir_path)
            index_db.update_download_status(
                job_id,
                status="completed",
                bytes_downloaded=getattr(result, "size", 0),
                error_message="",
                actual_checksum=getattr(result, "checksum", None),
                verified=getattr(result, "verified", None),
                completed_at=int(time.time()),
            )
        else:
            message = getattr(result, "error_message", "") or "download failed"
            if result.success and not getattr(result, "verified", True):
                message = "checksum verification failed"
            index_db.update_download_status(
                job_id,
                status="failed",
                bytes_downloaded=getattr(result, "size", 0),
                error_message=message,
                actual_checksum=getattr(result, "checksum", None),
                verified=getattr(result, "verified", None),
                completed_at=int(time.time()),
            )


def _update_download_progress(index_db: DatabaseManager, job_id: int, downloaded: int) -> None:
    index_db.update_download_status(
        job_id,
        status="in_progress",
        bytes_downloaded=max(0, int(downloaded)),
        error_message="",
        verified=None,
    )


def _record_backend_checksum(
    index_db: DatabaseManager,
    datadir_rel: PurePosixPath,
    datadir_path: Path,
) -> None:
    try:
        digest = hashlib.sha256()
        with open(datadir_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        index_db.record_backend_checksum(datadir_rel, "sha256", digest.hexdigest(), source="download")
    except Exception:
        return
