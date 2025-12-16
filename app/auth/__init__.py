"""Authentication module for CacheInfinity."""

from .credentials import CredentialStore, load_credentials

__all__ = [
    "CredentialStore",
    "load_credentials",
]