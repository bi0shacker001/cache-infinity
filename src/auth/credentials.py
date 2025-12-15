"""Credential loading utilities."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


class CredentialError(RuntimeError):
    """Raised when credential files are invalid."""


@dataclass(frozen=True)
class UserCredentials:
    username: str
    enabled: bool
    password_plain: Optional[str] = None
    password_hash: Optional[str] = None
    digest_ha1: dict[str, str] | None = None

    def validate(self) -> None:
        if not self.enabled:
            return
        if not (self.password_plain or self.password_hash or self.digest_ha1):
            raise CredentialError(
                f"Enabled user '{self.username}' must define password_plain, password_hash, or digest_ha1"
            )


@dataclass
class CredentialStore:
    users: dict[str, UserCredentials]

    def enabled_users(self) -> dict[str, UserCredentials]:
        return {name: user for name, user in self.users.items() if user.enabled}


def load_credentials(path: Path) -> CredentialStore:
    path = Path(path).expanduser()
    if not path.exists():
        raise CredentialError(f"Credential file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        doc = yaml.safe_load(handle) or {}
    if not isinstance(doc, MutableMapping):
        raise CredentialError("Credential file must contain a mapping root")
    users_section = doc.get("users")
    if not isinstance(users_section, Mapping):
        raise CredentialError("Credential file must contain a 'users' mapping")
    users: dict[str, UserCredentials] = {}
    for username, payload in users_section.items():
        if not isinstance(payload, Mapping):
            raise CredentialError(f"User '{username}' must map to a dictionary")
        creds = UserCredentials(
            username=username,
            enabled=bool(payload.get("enabled", True)),
            password_plain=_optional_str(payload.get("password_plain")),
            password_hash=_optional_str(payload.get("password_hash")),
            digest_ha1=_parse_digest(payload.get("digest_ha1")),
        )
        creds.validate()
        users[username] = creds
    return CredentialStore(users=users)


def _optional_str(value: object) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    raise CredentialError(f"Expected string, got {type(value)!r}")


def _parse_digest(entry: object) -> dict[str, str] | None:
    if entry in (None, {}):
        return None
    if not isinstance(entry, Mapping):
        raise CredentialError("digest_ha1 must be a mapping of realm->hash")
    digest = {}
    for realm, digest_hash in entry.items():
        if not isinstance(digest_hash, str):
            raise CredentialError("digest_ha1 values must be strings")
        digest[str(realm)] = digest_hash
    return digest


__all__ = ["CredentialStore", "CredentialError", "UserCredentials", "load_credentials"]
