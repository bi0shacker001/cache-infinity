"""Download management (PycURL-based) for CacheInfinity networking."""

from __future__ import annotations

import io
import logging
import os
import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, List, Any, Callable
from urllib.parse import urlparse

from auth.credentials import CookieJarDefinition
from cache.checksum import ChecksumCalculator

_logger = logging.getLogger(__name__)


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
        rclone_config_path: Optional[Path] = None,
        rclone_enabled: bool = False,
    ):
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
        self._active_downloads = 0
        self._download_lock = threading.Lock()
        self._progress_callbacks: List[Callable[[str, DownloadProgress], None]] = []
        self._rclone_config_path = rclone_config_path
        self._rclone_enabled = rclone_enabled
        _logger.debug("Fetcher initialized")
        
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
                with _rclone_env(self._rclone_config_path if self._rclone_enabled else None):
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
