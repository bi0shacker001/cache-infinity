"""Download helper using curl."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from .archive import ArchiveOrgCookieManager
from .config import CookieJarDefinition
from urllib.parse import urlparse


class FetchError(RuntimeError):
    """Raised when a download fails even after retries."""

    def __init__(self, url: str, destination: Path, returncode: int, stderr: Optional[str] = None):
        self.url = url
        self.destination = destination
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(self._build_message())

    @property
    def redirect_url(self) -> str:
        """URL clients should be redirected to when a download fails."""

        return self.url

    def _build_message(self) -> str:
        message = f"curl failed for {self.url} -> {self.destination} (exit {self.returncode})"
        if self.stderr:
            message = f"{message}: {self.stderr.strip()}"
        return message


class Fetcher:
    """Wrapper responsible for downloading remote resources."""

    def __init__(self, cookies: dict[str, CookieJarDefinition]):
        self.cookies = cookies
        self._archive_cookies = ArchiveOrgCookieManager()

    def fetch_to_path(self, url: str, destination: Path, cookie_domain: Optional[str] = None) -> None:
        """Download *url* using curl into *destination*."""

        destination.parent.mkdir(parents=True, exist_ok=True)
        domain = cookie_domain or self._match_cookie_domain(url)
        cookie_path = self._prepare_cookie(domain)
        cmd = [
            "curl",
            "-L",
            "--fail",
            "--show-error",
            "--retry",
            "3",
            "--retry-delay",
            "5",
            "--retry-connrefused",
            "--continue-at",
            "-",  # resume partial downloads
            "--speed-time",
            "30",
            "--speed-limit",
            "1024",  # bail if slower than 1KB/s for 30s
            "--output",
            str(destination),
            url,
        ]
        if cookie_path:
            cmd.extend(["-b", str(cookie_path), "-c", str(cookie_path)])
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or ""
            raise FetchError(url, destination, exc.returncode, stderr=stderr) from exc

    def _match_cookie_domain(self, url: str) -> Optional[str]:
        parsed = urlparse(url)
        host = parsed.netloc.split(":")[0].lower()
        for name in self.cookies:
            key = name.lower()
            if host == key or host.endswith("." + key):
                return name
        return None

    def _prepare_cookie(self, cookie_domain: Optional[str]) -> Optional[Path]:
        if not cookie_domain:
            return None
        cookie = self.cookies.get(cookie_domain)
        if not cookie:
            return None
        if cookie.credfile and cookie_domain.endswith("archive.org"):
            try:
                self._archive_cookies.ensure_cookie(cookie)
            except Exception:  # pragma: no cover - depends on network availability
                logging.getLogger(__name__).warning(
                    "Failed to refresh archive.org cookies for %s", cookie_domain, exc_info=True
                )
        return cookie.cookie_jar

    def refresh_cookie(self, domain: str) -> None:
        key = domain.strip()
        cookie = self.cookies.get(key)
        if not cookie:
            raise RuntimeError(f"No cookie definition for domain {domain}")
        if not cookie.credfile:
            raise RuntimeError(f"Domain {domain} does not have a credfile configured")
        self._archive_cookies.ensure_cookie(cookie)


__all__ = ["Fetcher", "FetchError"]
