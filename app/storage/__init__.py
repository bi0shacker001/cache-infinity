"""Storage module for CacheInfinity."""

from .backend import BackendRegistry, BackendStorage
from .staging import StagingArea

# TODO: ArchiveOrgCookieManager will be implemented later for cookie generation

__all__ = [
    "BackendRegistry",
    "BackendStorage",
    "StagingArea",
]