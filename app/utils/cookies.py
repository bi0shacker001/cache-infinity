"""Cookie jar normalization and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CookieValidationError(ValueError):
    """Raised when a cookie jar fails validation."""

    message: str

    def __str__(self) -> str:
        return self.message


def normalize_cookie_content(content: str) -> str:
    """Normalize cookie jar content to Unix newlines with trailing newline."""
    normalized = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if normalized:
        normalized = f"{normalized}\n"
    return normalized


def validate_cookie_content(domain: str, content: str) -> str:
    """Validate a Netscape cookies.txt payload and return normalized text."""
    safe_domain = (domain or "").strip().lower().lstrip(".")
    if not safe_domain:
        raise CookieValidationError("Domain name required")

    normalized = normalize_cookie_content(content)
    lines = []
    for line in normalized.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") and not stripped.startswith("#HttpOnly_"):
            continue
        lines.append(stripped)

    if not lines:
        raise CookieValidationError("Cookie jar contains no entries")

    errors = []
    for idx, line in enumerate(lines, start=1):
        parts = line.split("\t")
        if len(parts) < 7:
            errors.append(f"Line {idx}: expected 7 tab-separated fields")
            continue

        raw_domain = parts[0]
        if raw_domain.startswith("#HttpOnly_"):
            raw_domain = raw_domain[len("#HttpOnly_"):]
        entry_domain = raw_domain.lstrip(".").lower()

        if not entry_domain:
            errors.append(f"Line {idx}: empty domain")
        elif not (
            entry_domain == safe_domain or entry_domain.endswith(f".{safe_domain}")
        ):
            errors.append(f"Line {idx}: domain {entry_domain} not in {safe_domain}")

        include_sub = parts[1].upper()
        if include_sub not in {"TRUE", "FALSE"}:
            errors.append(f"Line {idx}: invalid includeSubdomains flag")

        secure_flag = parts[3].upper()
        if secure_flag not in {"TRUE", "FALSE"}:
            errors.append(f"Line {idx}: invalid secure flag")

        expires = parts[4]
        try:
            int(expires)
        except ValueError:
            errors.append(f"Line {idx}: invalid expiry timestamp")

    if errors:
        raise CookieValidationError("; ".join(errors))

    return normalized
