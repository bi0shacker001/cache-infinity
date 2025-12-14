"""WebDAV provider integrating backend storage with cachelink overlays."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import zlib
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlparse

from wsgidav import util
from wsgidav.dav_error import (
    HTTP_BAD_GATEWAY,
    HTTP_FORBIDDEN,
    HTTP_INTERNAL_ERROR,
    HTTP_NOT_FOUND,
    DAVError,
)
from wsgidav.dav_provider import DAVCollection, DAVNonCollection, DAVProvider

from .backend import BackendRegistry
from .cachelinks import CachelinkDescriptor, CachelinkIndex
from .config import ShareDefinition, ShareUserPolicy
from .fetcher import FetchError, Fetcher
from .index_db import IndexDatabase, IndexedEntry, TargetState
from .staging import StagingArea

_LOGGER = logging.getLogger(__name__)


@dataclass
class ProviderContext:
    share: ShareDefinition
    cachelinks: CachelinkIndex
    backend_registry: BackendRegistry
    staging: StagingArea
    index_db: IndexDatabase
    fetcher: Fetcher
    on_descriptor_access: Optional[Callable[[CachelinkDescriptor], None]] = None


class CacheInfinityProvider(DAVProvider):
    """Provider exposing backend files plus cachelink-backed virtual entries."""

    def __init__(self, context: ProviderContext):
        super().__init__()
        self.context = context
        self._tree = _CachelinkTree(context.share, context.cachelinks)
        self._target_states: dict[str, TargetState] = {}

    # Provider interface -------------------------------------------------
    def get_resource_inst(self, path: str, environ):  # type: ignore[override]
        access = _ShareAccess.from_environ(self.context.share, environ)
        backend_rel = _backend_relative_path(self.context.share, path)
        backend_path = self.backend.resolve(backend_rel)

        descriptor_match: _DescriptorMatch | None = None
        index_view: _IndexView | None = None
        virtual_children: Sequence[str] = ()
        if access.can_view_cache:
            descriptor_match = self._tree.match_descriptor(backend_rel)
            if descriptor_match:
                index_view = _IndexView(self.index_db, descriptor_match.descriptor)
            virtual_children = self._tree.virtual_children(backend_rel)

        remote_entry: IndexedEntry | None = None
        has_remote_dir = False
        if descriptor_match and index_view:
            remote_entry = index_view.entry_for(descriptor_match.relative_path)
            has_remote_dir = index_view.has_children(descriptor_match.relative_path)
            if remote_entry and remote_entry.is_dir:
                has_remote_dir = True

        backend_exists = backend_path.exists()
        if backend_exists and backend_path.is_file():
            return BackendFileResource(path, environ, self, backend_rel, backend_path, access)

        if remote_entry and not remote_entry.is_dir:
            return CachelinkFileResource(
                path,
                environ,
                self,
                backend_rel,
                descriptor_match,
                index_view,
                remote_entry,
                access,
            )

        if backend_exists and backend_path.is_dir():
            return CacheInfinityCollection(
                path,
                environ,
                self,
                backend_rel,
                backend_path,
                access,
                descriptor_match,
                index_view,
                virtual_children,
            )

        if has_remote_dir or virtual_children:
            return CacheInfinityCollection(
                path,
                environ,
                self,
                backend_rel,
                backend_path,
                access,
                descriptor_match,
                index_view,
                virtual_children,
            )

        return None

    # Convenience accessors ---------------------------------------------
    @property
    def backend(self) -> BackendRegistry:
        return self.context.backend_registry.primary

    @property
    def staging(self) -> StagingArea:
        return self.context.staging

    @property
    def index_db(self) -> IndexDatabase:
        return self.context.index_db

    @property
    def fetcher(self) -> Fetcher:
        return self.context.fetcher

    def notify_access(self, descriptor: CachelinkDescriptor, subpath: str | None = None) -> None:
        hook = self.context.on_descriptor_access
        if not hook:
            return
        try:
            hook(descriptor)
        except Exception:  # pragma: no cover - defensive logging
            _LOGGER.exception("Failed to notify descriptor access for %s", descriptor.canonical_id)

    def ensure_target_state(self, descriptor: CachelinkDescriptor) -> TargetState:
        state = self._target_states.get(descriptor.canonical_id)
        if state is None:
            state = self.index_db.ensure_target(descriptor, descriptor.remote_listing_url)
            self._target_states[descriptor.canonical_id] = state
        return state

    def record_fetch_failure(self, descriptor: CachelinkDescriptor, message: str) -> None:
        try:
            state = self.ensure_target_state(descriptor)
            self.index_db.mark_failure(state.id, message)
        except Exception:  # pragma: no cover - defensive logging
            _LOGGER.exception("Failed to record fetch failure for %s", descriptor.canonical_id)

    def cache_download(self, staged_path: Path, backend_path: Path, backend_rel: PurePosixPath) -> bool:
        try:
            size = staged_path.stat().st_size
        except FileNotFoundError:
            return False
        if backend_path.exists():
            return True
        backend_root = self.backend.definition.backend_cache_root
        usage = shutil.disk_usage(backend_root)
        if usage.free <= size:
            _LOGGER.warning("Backend full; serving %s but skipping cache write", backend_path)
            return False
        backend_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(staged_path, backend_path)
            digest = _compute_file_checksum(backend_path, "sha256")
            try:
                rel = PurePosixPath(backend_path.relative_to(backend_root).as_posix())
            except ValueError:
                rel = backend_rel
            self.index_db.record_backend_checksum(rel, "sha256", digest)
            return True
        except OSError:
            _LOGGER.exception("Failed to cache %s into backend", backend_path)
            return False


# Resource implementations ------------------------------------------------
class CacheInfinityCollection(DAVCollection):
    """Directory that merges backend content with cachelink overlays."""

    def __init__(
        self,
        path: str,
        environ,
        provider: CacheInfinityProvider,
        backend_rel: PurePosixPath,
        backend_path: Path,
        access: "_ShareAccess",
        descriptor_match: "_DescriptorMatch | None",
        index_view: "_IndexView | None",
        virtual_children: Sequence[str],
    ):
        super().__init__(path, environ)
        self.provider = provider
        self._backend_rel = backend_rel
        self._backend_path = backend_path
        self._access = access
        self._descriptor_match = descriptor_match
        self._index_view = index_view
        self._virtual_children = tuple(sorted(set(virtual_children)))
        if descriptor_match:
            rel = descriptor_match.relative_path.as_posix()
            rel_value = "" if rel in (".", "") else rel
            self.provider.notify_access(descriptor_match.descriptor, rel_value or None)

    def get_member_names(self) -> Iterable[str]:  # type: ignore[override]
        self._access.require_read()
        names: set[str] = set()
        if self._backend_path.exists() and self._backend_path.is_dir():
            try:
                names.update(os.listdir(self._backend_path))
            except OSError:
                _LOGGER.exception("Failed to list backend folder %s", self._backend_path)
        if self._index_view and self._descriptor_match:
            rel = self._descriptor_match.relative_path
            names.update(self._index_view.child_names(rel))
        names.update(self._virtual_children)
        return sorted(names)

    def get_member(self, name):  # type: ignore[override]
        self._access.require_read()
        uri = util.join_uri(self.path, name)
        return self.provider.get_resource_inst(uri, self.environ)

    def create_collection(self, name):  # type: ignore[override]
        self._access.require_write()
        target = self.provider.backend.resolve(self._backend_rel / name)
        target.mkdir(parents=True, exist_ok=True)

    def create_empty_resource(self, name: str):  # type: ignore[override]
        self._access.require_write()
        backend_rel = self._backend_rel / name
        target = self.provider.backend.resolve(backend_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
        return BackendFileResource(
            util.join_uri(self.path, name),
            self.environ,
            self.provider,
            backend_rel,
            target,
            self._access,
        )

    def delete(self):  # type: ignore[override]
        self._access.require_write()
        if self._backend_path.exists():
            shutil.rmtree(self._backend_path)
            return
        if self._descriptor_match or self._virtual_children:
            raise DAVError(
                HTTP_FORBIDDEN, context_info="Cachelink-backed folders are read-only until cached"
            )
        raise DAVError(HTTP_NOT_FOUND, context_info="Directory not found")


class BackendFileResource(DAVNonCollection):
    """Regular backend file."""

    def __init__(
        self,
        path: str,
        environ,
        provider: CacheInfinityProvider,
        backend_rel: PurePosixPath,
        backend_path: Path,
        access: "_ShareAccess",
    ):
        super().__init__(path, environ)
        self.provider = provider
        self._backend_rel = backend_rel
        self._backend_path = backend_path
        self._access = access

    def _stat(self):
        if not self._backend_path.exists():
            raise DAVError(HTTP_NOT_FOUND, context_info="File removed")
        return self._backend_path.stat()

    def get_content_length(self):  # type: ignore[override]
        self._access.require_read()
        return self._stat().st_size

    def get_content(self):  # type: ignore[override]
        self._access.require_read()
        return self._backend_path.open("rb")

    def get_last_modified(self):  # type: ignore[override]
        return self._stat().st_mtime

    def get_etag(self):  # type: ignore[override]
        if not self._backend_path.exists():
            return None
        return util.get_file_etag(str(self._backend_path))

    def support_etag(self):  # type: ignore[override]
        return True

    def begin_write(self, *, content_type=None):  # type: ignore[override]
        self._access.require_write()
        self._backend_path.parent.mkdir(parents=True, exist_ok=True)
        return self._backend_path.open("wb")

    def delete(self):  # type: ignore[override]
        self._access.require_write()
        if self._backend_path.exists():
            self._backend_path.unlink()
        else:
            raise DAVError(HTTP_NOT_FOUND, context_info="File not found")


class CachelinkFileResource(DAVNonCollection):
    """Virtual file backed by a cachelink descriptor."""

    def __init__(
        self,
        path: str,
        environ,
        provider: CacheInfinityProvider,
        backend_rel: PurePosixPath,
        descriptor_match: _DescriptorMatch,
        index_view: _IndexView,
        entry: IndexedEntry,
        access: "_ShareAccess",
    ):
        super().__init__(path, environ)
        self.provider = provider
        self._backend_rel = backend_rel
        self._descriptor_match = descriptor_match
        self._index_view = index_view
        self.entry = entry
        self._access = access
        self.descriptor = descriptor_match.descriptor
        rel = descriptor_match.relative_path.as_posix()
        rel_value = "" if rel in (".", "") else rel
        self.provider.notify_access(descriptor_match.descriptor, rel_value or None)

    def _backend_path(self) -> Path:
        return self.provider.backend.resolve(self._backend_rel)

    def get_content_length(self):  # type: ignore[override]
        self._access.require_read()
        backend_path = self._backend_path()
        if backend_path.exists():
            return backend_path.stat().st_size
        return self.entry.size or 0

    def get_last_modified(self):  # type: ignore[override]
        backend_path = self._backend_path()
        if backend_path.exists():
            return backend_path.stat().st_mtime
        if self.entry.modified:
            return self.entry.modified.timestamp()
        return None

    def get_etag(self):  # type: ignore[override]
        backend_path = self._backend_path()
        if not backend_path.exists():
            return None
        return util.get_file_etag(str(backend_path))

    def support_etag(self):  # type: ignore[override]
        return True

    def get_content(self):  # type: ignore[override]
        self._access.require_read()
        backend_path = self._backend_path()
        if backend_path.exists():
            return backend_path.open("rb")
        staged = self._download_to_staging()
        return _StagingDownloadStream(self.provider, staged, backend_path, self._backend_rel)

    def begin_write(self, *, content_type=None):  # type: ignore[override]
        self._access.require_write()
        backend_path = self._backend_path()
        backend_path.parent.mkdir(parents=True, exist_ok=True)
        return backend_path.open("wb")

    def delete(self):  # type: ignore[override]
        self._access.require_write()
        backend_path = self._backend_path()
        if backend_path.exists():
            backend_path.unlink()
        else:
            raise DAVError(
                HTTP_NOT_FOUND,
                context_info="Remote entries can only be deleted once cached locally",
            )

    def _download_to_staging(self) -> Path:
        staged = self.provider.staging.reserve_tempfile("fetch")
        try:
            self.provider.fetcher.fetch_to_path(self.entry.remote_url, staged)
            checksum = self.entry.checksum
            if checksum:
                self._verify_checksum(staged, checksum)
            elif self._looks_like_myrient_zip():
                crc = _extract_torrentzip_crc(staged)
                if crc:
                    checksum_value = f"crc32:{crc}"
                    self.entry.checksum = checksum_value
                    if self.entry.path:
                        self.provider.index_db.update_entry_checksum(
                            self.descriptor, self.entry.path, "crc32", crc
                        )
            return staged
        except FetchError as exc:  # pragma: no cover - network dependent
            self.provider.record_fetch_failure(self.descriptor, str(exc))
            _LOGGER.warning("Fetch failed for %s: %s", self.entry.remote_url, exc)
            staged.unlink(missing_ok=True)
            raise DAVError(
                HTTP_BAD_GATEWAY,
                context_info=f"Remote source unavailable; try downloading directly from {exc.redirect_url}",
            ) from exc
        except DAVError:
            staged.unlink(missing_ok=True)
            raise
        except Exception as exc:
            staged.unlink(missing_ok=True)
            _LOGGER.exception("Unexpected failure verifying %s", self.entry.remote_url)
            raise DAVError(HTTP_INTERNAL_ERROR, context_info=str(exc)) from exc

    def _verify_checksum(self, staged: Path, checksum: str) -> None:
        if ":" not in checksum:
            return
        algorithm, expected = checksum.split(":", 1)
        expected = expected.strip().lower()
        actual = _compute_file_checksum(staged, algorithm)
        if actual.lower() != expected:
            raise DAVError(
                HTTP_INTERNAL_ERROR,
                context_info=f"Checksum mismatch for {self.path}: expected {checksum}, got {actual}",
            )

    def _looks_like_myrient_zip(self) -> bool:
        parsed = urlparse(self.entry.remote_url)
        host = (parsed.hostname or "").lower()
        return host.endswith("myrient.erista.me") and parsed.path.lower().endswith(".zip")


# Helpers -------------------------------------------------------------------
class _StagingDownloadStream:
    """File-like object that streams data from staging and caches into backend on close."""

    def __init__(
        self,
        provider: CacheInfinityProvider,
        staged_path: Path,
        backend_path: Path,
        backend_rel: PurePosixPath,
    ):
        self._provider = provider
        self._staged_path = staged_path
        self._backend_path = backend_path
        self._backend_rel = backend_rel
        self._file = staged_path.open("rb")
        self._closed = False

    def read(self, size: int | None = -1):
        return self._file.read(size)

    def close(self):
        if self._closed:
            return
        try:
            self._file.close()
        finally:
            try:
                self._provider.cache_download(self._staged_path, self._backend_path, self._backend_rel)
            finally:
                if self._staged_path.exists():
                    try:
                        self._staged_path.unlink()
                    except OSError:
                        _LOGGER.debug("Failed to remove staging file %s", self._staged_path)
        self._closed = True

    def __iter__(self):
        return iter(self._file)

    def __getattr__(self, name):
        return getattr(self._file, name)

    def __enter__(self):
        self._file.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def _compute_file_checksum(path: Path, algorithm: str) -> str:
    algo = algorithm.lower()
    if algo == "crc32":
        checksum = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                if not chunk:
                    break
                checksum = zlib.crc32(chunk, checksum)
        return f"{checksum & 0xFFFFFFFF:08x}"
    try:
        hasher = hashlib.new(algo)
    except ValueError as exc:
        raise ValueError(f"Unsupported checksum algorithm '{algorithm}'") from exc
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


_TORRENTZIP_RE = re.compile(r"TORRENTZIPPED-([0-9A-F]{8})", re.IGNORECASE)


def _extract_torrentzip_crc(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            comment = archive.comment.decode("ascii", errors="ignore")
    except (zipfile.BadZipFile, OSError):
        return None
    match = _TORRENTZIP_RE.search(comment)
    if not match:
        return None
    return match.group(1).upper()


class _ShareAccess:
    """Per-request helper enforcing share-level user policy."""

    def __init__(self, share: ShareDefinition, username: str | None):
        self.share = share
        self.username = username or "anonymous"
        policy = share.users.get(self.username)
        if policy is None and self.username != "anonymous":
            policy = share.users.get("anonymous")
        self.policy = policy or ShareUserPolicy()

    @classmethod
    def from_environ(cls, share: ShareDefinition, environ: Mapping[str, object]) -> "_ShareAccess":
        username = None
        for key in ("http_authenticator.username", "wsgidav.auth.user_name"):
            value = environ.get(key)
            if isinstance(value, str) and value:
                username = value
                break
        return cls(share, username)

    @property
    def can_view_cache(self) -> bool:
        return self.share.cachelink_overlay and self.policy.cache

    def require_read(self) -> None:
        if not self.policy.read:
            raise DAVError(HTTP_FORBIDDEN, context_info="Read access denied for this share")

    def require_write(self) -> None:
        if not (self.share.writable and self.policy.write):
            raise DAVError(HTTP_FORBIDDEN, context_info="Write access denied for this share")


@dataclass(frozen=True)
class _DescriptorMatch:
    descriptor: CachelinkDescriptor
    root: PurePosixPath
    relative_path: PurePosixPath


class _IndexView:
    """Convenience wrapper to query indexed entries for a descriptor."""

    def __init__(self, index_db: IndexDatabase, descriptor: CachelinkDescriptor):
        self._descriptor = descriptor
        entries = index_db.list_entries_for_descriptor(descriptor)
        self._records: list[tuple[str, IndexedEntry]] = []
        self._path_map: Dict[str, IndexedEntry] = {}
        for entry in entries:
            key = _normalize_entry_path(entry.path)
            self._records.append((key, entry))
            self._path_map[key] = entry
        self._child_cache: Dict[str, tuple[str, ...]] = {}

    def entry_for(self, rel_path: PurePosixPath) -> IndexedEntry | None:
        key = _normalize_rel_path(rel_path)
        if not key:
            return None
        return self._path_map.get(key)

    def has_children(self, rel_path: PurePosixPath) -> bool:
        key = _normalize_rel_path(rel_path)
        if key:
            entry = self._path_map.get(key)
            if entry and entry.is_dir:
                return True
            prefix = key + "/"
            for path, _ in self._records:
                if path.startswith(prefix):
                    return True
            return False
        return bool(self._records)

    def child_names(self, rel_path: PurePosixPath) -> Sequence[str]:
        key = _normalize_rel_path(rel_path)
        cached = self._child_cache.get(key)
        if cached is not None:
            return cached
        names: set[str] = set()
        if key:
            prefix = key + "/"
            for path, _ in self._records:
                if not path.startswith(prefix):
                    continue
                remainder = path[len(prefix) :]
                if not remainder:
                    continue
                head = remainder.split("/", 1)[0]
                names.add(head)
        else:
            for path, entry in self._records:
                head = path.split("/", 1)[0] if path else ""
                if not head and entry.path:
                    head = entry.path.split("/", 1)[0]
                if head:
                    names.add(head)
        ordered = tuple(sorted(names))
        self._child_cache[key] = ordered
        return ordered


class _CachelinkTree:
    """Resolves backend-relative paths to cachelink descriptors + virtual directories."""

    def __init__(self, share: ShareDefinition, cachelinks: CachelinkIndex):
        self._share_root = _normalize_absolute(share.backend_folder)
        self._descriptor_roots: dict[PurePosixPath, CachelinkDescriptor] = {}
        self._virtual_children: dict[PurePosixPath, set[str]] = {}

        for descriptor in cachelinks.cachelinks.values():
            root = _normalize_absolute(descriptor.backend_relative_folder)
            if not _is_within(root, self._share_root):
                continue
            self._descriptor_roots[root] = descriptor
            self._register_virtual_ancestors(root)

    def match_descriptor(self, backend_rel: PurePosixPath) -> _DescriptorMatch | None:
        backend_rel = _normalize_absolute(backend_rel)
        current = backend_rel
        while True:
            descriptor = self._descriptor_roots.get(current)
            if descriptor:
                try:
                    relative = backend_rel.relative_to(current)
                except ValueError:
                    relative = PurePosixPath(".")
                if not relative.parts:
                    relative = PurePosixPath(".")
                return _DescriptorMatch(descriptor, current, relative)
            if current == self._share_root or current == PurePosixPath("/"):
                break
            current = current.parent
        return None

    def virtual_children(self, backend_rel: PurePosixPath) -> Sequence[str]:
        backend_rel = _normalize_absolute(backend_rel)
        entries = self._virtual_children.get(backend_rel)
        if not entries:
            return ()
        return tuple(sorted(entries))

    def _register_virtual_ancestors(self, root: PurePosixPath) -> None:
        current = root
        while current != self._share_root:
            parent = current.parent
            if not _is_within(parent, self._share_root):
                break
            self._virtual_children.setdefault(parent, set()).add(current.name)
            current = parent
            if current == parent:
                break


def _backend_relative_path(share: ShareDefinition, request_path: str) -> PurePosixPath:
    requested = PurePosixPath(request_path)
    if not requested.is_absolute():
        requested = PurePosixPath("/") / requested
    base = share.frontend_folder
    try:
        rel_front = requested.relative_to(base)
    except ValueError:
        rel_front = PurePosixPath(".")
    backend_root = share.backend_folder
    if str(rel_front) in (".", ""):
        return backend_root
    return _normalize_absolute(backend_root / rel_front)


def _normalize_absolute(path: PurePosixPath) -> PurePosixPath:
    text = path.as_posix()
    if not text.startswith("/"):
        text = "/" + text
    normalized = text.replace("//", "/")
    if normalized in ("/.", "."):
        normalized = "/"
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return PurePosixPath(normalized or "/")


def _normalize_entry_path(value: str) -> str:
    return value.strip("/")


def _normalize_rel_path(path: PurePosixPath) -> str:
    if path in (PurePosixPath("."), PurePosixPath("/")):
        return ""
    return path.as_posix().lstrip("./")


def _is_within(path: PurePosixPath, ancestor: PurePosixPath) -> bool:
    try:
        path.relative_to(ancestor)
        return True
    except ValueError:
        return path == ancestor


__all__ = ["CacheInfinityProvider", "ProviderContext"]
