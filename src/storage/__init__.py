"""Storage module for CacheInfinity."""

from .backend import BackendRegistry, BackendStorage
from .staging import StagingArea
from .archive import ArchiveOrgCookieManager

__all__ = [
    "BackendRegistry",
    "BackendStorage",
    "StagingArea",
    "ArchiveOrgCookieManager",
]