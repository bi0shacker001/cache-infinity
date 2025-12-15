"""Archive.org helper utilities."""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import CookieJarDefinition

_LOGGER = logging.getLogger(__name__)
_LOGIN_ENDPOINT = "https://archive.org/account/login"
_DEFAULT_REFRESH_SECONDS = 6 * 60 * 60  # 6 hours


@dataclass
class _Credentials:
    username: str
    password: str


class ArchiveOrgCookieManager:
    """Generates archive.org cookies using stored credentials."""

    def __init__(self, refresh_interval: int = _DEFAULT_REFRESH_SECONDS):
        self.refresh_interval = refresh_interval

    def ensure_cookie(self, definition: CookieJarDefinition) -> None:
        """Ensure the cookie jar exists and is reasonably fresh."""

        if not definition.credfile:
            return
        if not self._needs_refresh(definition.cookie_jar):
            return
        creds = self._read_credentials(definition.credfile)
        self._request_cookie(creds, definition.cookie_jar)
        _LOGGER.info("Refreshed archive.org cookie jar at %s", definition.cookie_jar)

    def _needs_refresh(self, cookie_jar: Path) -> bool:
        if not cookie_jar.exists():
            return True
        if cookie_jar.stat().st_size == 0:
            return True
        age = time.time() - cookie_jar.stat().st_mtime
        return age > self.refresh_interval

    def _read_credentials(self, credfile: Path) -> _Credentials:
        if not credfile.exists():
            raise RuntimeError(f"Credfile {credfile} does not exist")
        data: dict[str, str] = {}
        with credfile.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                data[key.strip()] = value.strip()
        username = data.get("username")
        password = data.get("password")
        if not username or not password:
            raise RuntimeError(f"Credfile {credfile} must define username and password")
        return _Credentials(username=username, password=password)

    def _request_cookie(self, creds: _Credentials, cookie_jar: Path) -> None:
        cookie_jar.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "curl",
            "--silent",
            "--show-error",
            "--dump-header",
            str(cookie_jar),
            "-u",
            f"{creds.username}:{creds.password}",
            "-H",
            "Connection: keep-alive",
            _LOGIN_ENDPOINT,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Failed to refresh archive.org cookies (exit {exc.returncode}): {exc.stderr}"
            ) from exc


__all__ = ["ArchiveOrgCookieManager"]
