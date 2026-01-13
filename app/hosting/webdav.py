"""WebDAV provider for CacheInfinity user-facing services."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, TYPE_CHECKING

try:  # pragma: no cover - optional dependency
    from wsgidav.dav_provider import DAVCollection, DAVNonCollection, DAVProvider
    from wsgidav.dc.base_dc import BaseDomainController
except ModuleNotFoundError:  # pragma: no cover - fallback when optional dependency is absent
    class _MissingWsgiDAV:  # pylint: disable=too-few-public-methods
        """Placeholder base that raises if WsgiDAV isn't installed."""

        def __init__(self, *args, **kwargs):
            raise ModuleNotFoundError(
                "WsgiDAV is not installed; install the 'wsgidav' extra to enable WebDAV"
            )

    class DAVCollection(_MissingWsgiDAV):
        pass

    class DAVNonCollection(_MissingWsgiDAV):
        pass

    class DAVProvider(_MissingWsgiDAV):
        pass

    class BaseDomainController(_MissingWsgiDAV):
        pass

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from auth.credentials import AuthenticationManager
    from cache.cachelinks import CachelinkIndex
    from core.config import Settings
    from core.services import ServiceManager, WebDAVService, WebUIService
    from db.dbmanage import DatabaseManager
    from net.fetcher import Fetcher
    from storage.datadir import DatadirRegistry
    from storage.staging import StagingArea


@dataclass
class HostingContext:
    """Runtime dependencies required by the WebDAV provider."""

    settings: "Settings"
    index_db: "DatabaseManager"
    datadir_registry: "DatadirRegistry"
    staging: "StagingArea"
    cachelinks: "CachelinkIndex"
    fetcher: "Fetcher"
    auth_manager: "AuthenticationManager | None" = None


def _current_webdav_app(service_manager: "ServiceManager"):
    webdav_service: "WebDAVService" = service_manager.context["webdav"]
    return webdav_service.app


def _current_webui_app(service_manager: "ServiceManager"):
    webui_service: "WebUIService" = service_manager.context["webui"]
    return webui_service.app


class _ReloadableApp:
    """WSGI wrapper that delegates to the current CacheInfinity WsgiDAV app."""

    def __init__(self, service_manager: "ServiceManager") -> None:
        self._service_manager = service_manager

    def __call__(self, environ, start_response):
        app = _current_webdav_app(self._service_manager)
        try:
            return app(environ, start_response)
        except Exception:
            path = environ.get("PATH_INFO", "?")
            _logger.exception("Unhandled error when serving %s", path)
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"Internal Server Error"]


class _UIReloadableApp:
    """WSGI wrapper for the Web UI that picks up new state on reloads."""

    def __init__(self, service_manager: "ServiceManager") -> None:
        self._service_manager = service_manager

    def __call__(self, environ, start_response):
        app = _current_webui_app(self._service_manager)
        try:
            return app(environ, start_response)
        except Exception:
            path = environ.get("PATH_INFO", "?")
            _logger.exception("Unhandled error when serving %s", path)
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"Internal Server Error"]


def _normalize_prop_name(name: Any) -> str:
    if isinstance(name, tuple) and len(name) == 2:
        return f"{{{name[0]}}}{name[1]}"
    if hasattr(name, "namespace") and hasattr(name, "name"):
        return f"{{{name.namespace}}}{name.name}"
    return str(name)


def _split_zip_subfolder(subfolder: str) -> tuple[str | None, str]:
    normalized = (subfolder or "/").strip("/")
    if not normalized:
        return None, ""
    parts = [seg for seg in normalized.split("/") if seg]
    for idx, segment in enumerate(parts):
        if segment.endswith(".zip"):
            zip_path = "/".join(parts[: idx + 1])
            inner = "/".join(parts[idx + 1 :])
            return zip_path, inner
    return None, normalized


def build_user_mapping(settings) -> dict[str, dict[str, dict[str, str]]]:
    """Build WsgiDAV user mapping based on share auth configuration."""
    mapping: dict[str, dict[str, dict[str, str]]] = {}
    for share in settings.shares.values():
        share_users: dict[str, dict[str, str]] = {}
        for username, policy in share.users.items():
            if not policy.login:
                continue
            if username == "anonymous":
                share_users[username] = {"auth": "anonymous"}
                continue
            if settings.auth.proxy_header.enabled:
                share_users[username] = {"auth": "external"}
                continue
            if settings.auth.ldap.enabled:
                share_users[username] = {"auth": "ldap"}
                continue
            if settings.auth.oidc.enabled:
                share_users[username] = {"auth": "oidc"}
                continue
            share_users[username] = {"auth": "local"}
        mapping[share.frontend_folder.as_posix()] = share_users
    return mapping


class CacheInfinityDomainController(BaseDomainController):
    """Domain controller that validates WebDAV users against the database."""

    def __init__(self, wsgidav_app, config):
        super().__init__(wsgidav_app, config)
        self._config = config
        self._context = config.get("cacheinfinity_context")
        self._auth_manager = getattr(self._context, "auth_manager", None)
        self._user_mapping = config.get("simple_dc", {}).get("user_mapping", {})

    def getDomainRealm(self, path_info, environ):
        realm = self._resolve_realm(path_info)
        return realm or "CacheInfinity"

    def requireAuthentication(self, realm, environ):
        users = self._user_mapping.get(realm) or {}
        if not isinstance(users, dict):
            users = {}
        return "anonymous" not in users

    # WsgiDAV 4.x snake_case API compatibility -------------------------------
    def get_domain_realm(self, path_info, environ):
        return self.getDomainRealm(path_info, environ)

    def require_authentication(self, realm, environ):
        return self.requireAuthentication(realm, environ)

    def basic_auth_user(self, realm, username, password, environ):
        return self.basic_authentication(realm, username, password, environ)

    def supports_http_digest_auth(self):
        return True

    def isValidUser(self, realm, username, password, environ):
        entry = self._get_user_entry(realm, username)
        if not entry or not self._context:
            return False
        mode = entry.get("auth", "local")
        if mode == "anonymous":
            return True
        if mode == "external":
            return True
        if mode == "ldap":
            return bool(self._context.index_db.validate_ldap_credentials(username, password, purpose="webdav"))
        if mode == "oidc":
            return bool(self._context.index_db.validate_oidc_credentials(username, password))
        if self._auth_manager:
            return bool(self._auth_manager.authenticate_user(username, password, purpose="webdav"))
        return bool(self._context.index_db.validate_credentials(username, password, purpose="webdav"))

    def basic_authentication(self, realm, username, password, environ):
        return self.isValidUser(realm, username, password, environ)

    def digest_authentication(self, realm, username, environ):
        entry = self._get_user_entry(realm, username)
        if not entry or not self._context:
            return False
        if entry.get("auth") in {"anonymous", "external"}:
            return False
        if not self._auth_manager:
            return False
        stored = self._auth_manager.db_adapter.get_user_credentials(username, purpose="webdav")
        if not stored:
            return False
        digest_map = stored.get("digest_ha1")
        if isinstance(digest_map, dict):
            ha1 = digest_map.get(realm) or digest_map.get("/")
            if ha1:
                return ha1
        password_plain = stored.get("password_plain")
        if password_plain:
            ha1 = hashlib.md5(f"{username}:{realm}:{password_plain}".encode("utf-8")).hexdigest()
            return ha1
        return False

    def digest_auth_user(self, realm, username, environ):
        return self.digest_authentication(realm, username, environ)

    def _resolve_realm(self, path_info):
        for realm in sorted(self._user_mapping.keys(), key=len, reverse=True):
            if realm == "/":
                return realm
            if path_info == realm or path_info.startswith(realm.rstrip("/") + "/"):
                return realm
        return None

    def _get_user_entry(self, realm, username):
        users = self._user_mapping.get(realm) or {}
        if not isinstance(users, dict):
            return None
        return users.get(username)


class WebDAVProvider(DAVProvider):
    """Custom WebDAV provider for CacheInfinity."""

    def __init__(self, context: HostingContext):
        super().__init__()
        self.context = context
        self._zip_cache_lock = threading.Lock()
        _logger.info("WebDAV provider initialized")

    def get_resource_inst(self, path: str, environ: Dict[str, Any]) -> Optional[Any]:
        share_ctx = self._resolve_share(path)
        if not share_ctx:
            return None
        share, share_rel, frontend_root = share_ctx
        policy = self._resolve_user_policy(share, environ)
        if not policy or not policy.read:
            return None
        method = environ.get("REQUEST_METHOD", "GET").upper()
        if method in {"PUT", "DELETE", "MKCOL", "MOVE", "COPY", "PROPPATCH"}:
            if not share.writable or not policy.write:
                return None

        datadir_rel = self._map_to_datadir(share, share_rel)
        if datadir_rel is None:
            return None

        datadir_path = self.context.datadir_registry.primary.resolve(datadir_rel)
        if datadir_path.exists():
            cache_state = "local-only"
            if self.context.index_db.lookup_backend_checksum(datadir_rel):
                cache_state = "cached"
            if datadir_path.is_dir():
                return DatadirDirectoryResource(
                    path,
                    environ,
                    self,
                    self.context,
                    share,
                    share_rel,
                    datadir_rel,
                    frontend_root=frontend_root,
                    policy=policy,
                    cache_state=cache_state,
                )
            return DatadirFileResource(
                path,
                environ,
                self,
                self.context,
                datadir_rel,
                cache_state=cache_state,
            )

        if not self._cachelink_overlay_enabled(share, policy):
            if method in {"PUT", "MKCOL"}:
                return self._build_datadir_resource(
                    path,
                    environ,
                    share,
                    share_rel,
                    frontend_root,
                    datadir_rel,
                    policy,
                )
            return None

        descriptor, subpath = self._find_cachelink_for_path(share, datadir_rel)
        if not descriptor:
            if self._has_cachelink_children(share, datadir_rel):
                return CachelinkDirectoryResource(
                    path,
                    environ,
                    self,
                    self.context,
                    share,
                    share_rel,
                    datadir_rel,
                    frontend_root=frontend_root,
                    policy=policy,
                )
            return None

        if subpath is None or subpath == PurePosixPath("."):
            return CachelinkDirectoryResource(
                path,
                environ,
                self,
                self.context,
                share,
                share_rel,
                datadir_rel,
                frontend_root=frontend_root,
                policy=policy,
                descriptor=descriptor,
                subpath=PurePosixPath(""),
            )

        entry, is_dir = self._lookup_cachelink_entry(descriptor, subpath)
        if is_dir:
            return CachelinkDirectoryResource(
                path,
                environ,
                self,
                self.context,
                share,
                share_rel,
                datadir_rel,
                frontend_root=frontend_root,
                policy=policy,
                descriptor=descriptor,
                subpath=subpath,
            )
        if entry:
            base_rel = datadir_rel
            sub_parts = subpath.parts if subpath not in (PurePosixPath(""), PurePosixPath(".")) else ()
            if sub_parts:
                base_rel = PurePosixPath(*datadir_rel.parts[: -len(sub_parts)])
            return CachelinkFileResource(
                path,
                environ,
                self,
                self.context,
                descriptor,
                datadir_rel,
                base_rel,
                entry,
                subpath,
                allow_cache=policy.cache,
            )
        if method in {"PUT", "MKCOL"}:
            return self._build_datadir_resource(
                path,
                environ,
                share,
                share_rel,
                frontend_root,
                datadir_rel,
                policy,
            )
        return None

    def get_content_length(self, path: str, environ: Dict[str, Any]) -> Optional[int]:
        resource = self.get_resource_inst(path, environ)
        return resource.get_content_length() if resource else None

    def get_last_modified(self, path: str, environ: Dict[str, Any]) -> Optional[int]:
        resource = self.get_resource_inst(path, environ)
        return resource.get_last_modified() if resource else None

    def get_etag(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        resource = self.get_resource_inst(path, environ)
        return resource.get_etag() if resource else None

    def get_dav_getlastmodified(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        try:
            resource = self.get_resource_inst(path, environ)
            if resource and hasattr(resource, "get_last_modified"):
                timestamp = resource.get_last_modified()
                if timestamp:
                    import datetime
                    dt = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
                    return dt.isoformat()
        except Exception:
            return None
        return None

    def get_dav_creationdate(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        return self.get_dav_getlastmodified(path, environ)

    def get_dav_resourcetype(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        resource = self.get_resource_inst(path, environ)
        if resource:
            from wsgidav.dav_provider import DirectoryResource
            if isinstance(resource, DirectoryResource):
                return "collection"
        return None

    def get_dav_displayname(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        if path.endswith("/"):
            path = path[:-1]
        return path.split("/")[-1] if path else ""

    def get_dav_getcontenttype(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        try:
            resource = self.get_resource_inst(path, environ)
            if resource and hasattr(resource, "get_content_length"):
                import mimetypes
                mime_type, _ = mimetypes.guess_type(path)
                return mime_type or "application/octet-stream"
        except Exception:
            return None
        return None

    def is_collection(self, path: str, environ: Dict[str, Any]) -> bool:
        resource = self.get_resource_inst(path, environ)
        if resource:
            from wsgidav.dav_provider import DirectoryResource
            return isinstance(resource, DirectoryResource)
        return False

    def get_member_list(self, path: str, environ: Dict[str, Any]) -> List[str]:
        share_ctx = self._resolve_share(path)
        if not share_ctx:
            return []
        share, share_rel, frontend_root = share_ctx
        policy = self._resolve_user_policy(share, environ)
        if not policy or not policy.read:
            return []

        datadir_rel = self._map_to_datadir(share, share_rel)
        if datadir_rel is None:
            return []

        members: set[str] = set()
        members.update(
            self._datadir_members(frontend_root, share_rel, datadir_rel)
        )
        if self._cachelink_overlay_enabled(share, policy):
            members.update(
                self._cachelink_members(frontend_root, share_rel, datadir_rel, share)
            )
        return sorted(members)

    def _resolve_share(self, path: str) -> Optional[tuple[Any, PurePosixPath, str]]:
        candidates = []
        for share in self.context.settings.shares.values():
            prefix = share.frontend_folder.as_posix()
            if prefix != "/" and prefix.endswith("/"):
                prefix = prefix.rstrip("/")
            if prefix == "/":
                if path.startswith("/"):
                    candidates.append((len(prefix), share, prefix))
                continue
            if path == prefix or path.startswith(prefix + "/"):
                candidates.append((len(prefix), share, prefix))
        if not candidates:
            return None
        _, share, prefix = sorted(candidates, key=lambda item: item[0], reverse=True)[0]
        remainder = path[len(prefix):].lstrip("/")
        share_rel = PurePosixPath(remainder)
        if ".." in share_rel.parts:
            return None
        return share, share_rel, prefix or "/"

    def _resolve_user_policy(self, share, environ: Dict[str, Any]):
        username = (
            environ.get("REMOTE_USER")
            or environ.get("wsgidav.auth.user_name")
            or environ.get("HTTP_REMOTE_USER")
            or ""
        )
        if not username:
            username = "anonymous"
        return share.users.get(username)

    def _build_datadir_resource(
        self,
        path: str,
        environ: Dict[str, Any],
        share,
        share_rel: PurePosixPath,
        frontend_root: str,
        datadir_rel: PurePosixPath,
        policy,
    ):
        if path.endswith("/") or environ.get("REQUEST_METHOD", "").upper() == "MKCOL":
            return DatadirDirectoryResource(
                path,
                environ,
                self,
                self.context,
                share,
                share_rel,
                datadir_rel,
                frontend_root=frontend_root,
                policy=policy,
                cache_state="local-only",
            )
        return DatadirFileResource(
            path,
            environ,
            self,
            self.context,
            datadir_rel,
            cache_state="local-only",
        )

    def _map_to_datadir(self, share, share_rel: PurePosixPath) -> Optional[PurePosixPath]:
        base = share.datadir_folder.as_posix().strip("/")
        base_path = PurePosixPath(base) if base else PurePosixPath("")
        if share_rel == PurePosixPath("."):
            share_rel = PurePosixPath("")
        if ".." in share_rel.parts:
            return None
        if base_path == PurePosixPath("."):
            base_path = PurePosixPath("")
        if str(share_rel) in ("", "."):
            return base_path
        return base_path / share_rel

    def _cachelink_overlay_enabled(self, share, policy) -> bool:
        return bool(share.cachelink_overlay and policy.cache)

    def _datadir_members(
        self,
        frontend_root: str,
        share_rel: PurePosixPath,
        datadir_rel: PurePosixPath,
    ) -> list[str]:
        try:
            datadir_path = self.context.datadir_registry.primary.resolve(datadir_rel)
            if not datadir_path.exists() or not datadir_path.is_dir():
                return []
            members = []
            for item in datadir_path.iterdir():
                members.append(self._frontend_path(frontend_root, share_rel, item.name))
            return members
        except Exception:
            return []

    def _cachelink_members(
        self,
        frontend_root: str,
        share_rel: PurePosixPath,
        datadir_rel: PurePosixPath,
        share,
    ) -> list[str]:
        members: set[str] = set()
        datadir_parts = self._path_parts(datadir_rel)
        for descriptor in self._cachelinks_for_share(share):
            mount_path = self._descriptor_mount_path(descriptor)
            mount_parts = self._path_parts(mount_path)
            if self._is_prefix(datadir_rel, mount_path):
                remainder = mount_parts[len(datadir_parts):]
                if remainder:
                    members.add(self._frontend_path(frontend_root, share_rel, remainder[0]))
            if self._is_prefix(mount_path, datadir_rel):
                subpath = PurePosixPath(*datadir_parts[len(mount_parts):])
                for child in self._cachelink_children(descriptor, subpath).keys():
                    members.add(self._frontend_path(frontend_root, share_rel, child))
        return list(members)

    def _cachelinks_for_share(self, share) -> list[Any]:
        base = share.datadir_folder.as_posix().strip("/")
        base_path = PurePosixPath(base) if base else PurePosixPath("")
        matches = []
        for descriptor in self.context.cachelinks.cachelinks.values():
            mount_path = self._descriptor_mount_path(descriptor)
            if self._is_prefix(base_path, mount_path):
                matches.append(descriptor)
        return matches

    def _descriptor_mount_path(self, descriptor) -> PurePosixPath:
        return PurePosixPath("/".join(descriptor.path_segments))

    def _is_prefix(self, prefix: PurePosixPath, path: PurePosixPath) -> bool:
        prefix_parts = () if prefix in (PurePosixPath(""), PurePosixPath(".")) else prefix.parts
        path_parts = () if path in (PurePosixPath(""), PurePosixPath(".")) else path.parts
        if len(prefix_parts) > len(path_parts):
            return False
        return path_parts[:len(prefix_parts)] == prefix_parts

    def _path_parts(self, path: PurePosixPath) -> tuple[str, ...]:
        if path in (PurePosixPath(""), PurePosixPath(".")):
            return ()
        return path.parts

    def _has_cachelink_children(self, share, datadir_rel: PurePosixPath) -> bool:
        datadir_parts = self._path_parts(datadir_rel)
        for descriptor in self._cachelinks_for_share(share):
            mount_path = self._descriptor_mount_path(descriptor)
            mount_parts = self._path_parts(mount_path)
            if self._is_prefix(datadir_rel, mount_path):
                remainder = mount_parts[len(datadir_parts):]
                if remainder:
                    return True
        return False

    def _find_cachelink_for_path(self, share, datadir_rel: PurePosixPath) -> tuple[Any | None, PurePosixPath | None]:
        best = None
        best_len = -1
        for descriptor in self._cachelinks_for_share(share):
            mount_path = self._descriptor_mount_path(descriptor)
            if self._is_prefix(mount_path, datadir_rel):
                if len(self._path_parts(mount_path)) > best_len:
                    best = descriptor
                    best_len = len(self._path_parts(mount_path))
        if not best:
            return None, None
        mount_path = self._descriptor_mount_path(best)
        remainder = self._path_parts(datadir_rel)[len(self._path_parts(mount_path)):]
        return best, PurePosixPath(*remainder) if remainder else PurePosixPath("")

    def _lookup_cachelink_entry(self, descriptor, subpath: PurePosixPath) -> tuple[Any | None, bool]:
        entries = self.context.index_db.list_entries_for_descriptor(descriptor)
        normalized = self._normalize_entry_path(subpath)
        has_child = False
        for entry in entries:
            entry_path = self._normalize_entry_path(entry.path)
            if entry_path == normalized:
                return entry, bool(entry.is_dir)
            if self._is_prefix(normalized, entry_path):
                has_child = True
        return (None, has_child)

    def _cachelink_children(self, descriptor, subpath: PurePosixPath) -> dict[str, bool]:
        entries = self.context.index_db.list_entries_for_descriptor(descriptor)
        normalized = self._normalize_entry_path(subpath)
        children: dict[str, bool] = {}
        normalized_parts = self._path_parts(normalized)
        for entry in entries:
            entry_path = self._normalize_entry_path(entry.path)
            if not self._is_prefix(normalized, entry_path):
                continue
            remainder = self._path_parts(entry_path)[len(normalized_parts):]
            if not remainder:
                continue
            name = remainder[0]
            is_dir = bool(entry.is_dir) or len(remainder) > 1
            children[name] = children.get(name, False) or is_dir
        return children

    def _normalize_entry_path(self, value: PurePosixPath | str) -> PurePosixPath:
        text = str(value or "").strip("/")
        if not text:
            return PurePosixPath("")
        return PurePosixPath(text)

    def _frontend_path(self, frontend_root: str, share_rel: PurePosixPath, child: str) -> str:
        base = PurePosixPath(frontend_root or "/")
        rel = share_rel if share_rel != PurePosixPath(".") else PurePosixPath("")
        full = base
        if rel and rel != PurePosixPath(""):
            full = full / rel
        if child:
            full = full / child
        return full.as_posix()

    def _resolve_datadir_rel_for_write(self, path: str, environ: Dict[str, Any]) -> Optional[PurePosixPath]:
        share_ctx = self._resolve_share(path)
        if not share_ctx:
            return None
        share, share_rel, _ = share_ctx
        policy = self._resolve_user_policy(share, environ)
        if not policy or not policy.write or not share.writable:
            return None
        return self._map_to_datadir(share, share_rel)


class DatadirFileResource(DAVNonCollection):
    """File resource backed by datadir storage."""

    def __init__(
        self,
        path: str,
        environ: Dict[str, Any],
        provider: WebDAVProvider,
        service,
        datadir_rel: PurePosixPath,
        *,
        cache_state: str = "cached",
    ) -> None:
        super().__init__(path, environ)
        self.path = path
        self.provider = provider
        self.context = service
        self.datadir_rel = datadir_rel
        self.cache_state = cache_state
        self._write_handle = None

    def exists(self):
        return self._resolve_path().is_file()

    def _resolve_path(self):
        return self.context.datadir_registry.primary.resolve(self.datadir_rel)

    def get_content_length(self) -> int:
        try:
            file_path = self._resolve_path()
            return file_path.stat().st_size
        except Exception:
            return 0

    def get_last_modified(self) -> int:
        try:
            file_path = self._resolve_path()
            return int(file_path.stat().st_mtime)
        except Exception:
            return 0

    def get_etag(self) -> str:
        return f'"{hash(self.path)}"'

    def get_content(self):
        try:
            file_path = self._resolve_path()
            return open(file_path, "rb")
        except Exception as exc:
            _logger.error("Failed to get content for %s: %s", self.path, exc)
            return None

    def get_content_type(self):
        import mimetypes
        mime_type, _ = mimetypes.guess_type(self.path)
        return mime_type or "application/octet-stream"

    def begin_write(self, content_type=None):
        file_path = self._resolve_path()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_handle = open(file_path, "wb")
        return self._write_handle

    def end_write(self, with_errors, **_kwargs):
        if self._write_handle:
            self._write_handle.close()
            self._write_handle = None
        if with_errors:
            try:
                file_path = self._resolve_path()
                if file_path.exists():
                    file_path.unlink()
            except Exception:
                return

    def delete(self):
        file_path = self._resolve_path()
        if not file_path.exists():
            raise RuntimeError("File does not exist")
        file_path.unlink()

    def copy_recursive(self, dest_path, **_kwargs):
        dest_rel = self.provider._resolve_datadir_rel_for_write(dest_path, self.environ)
        if dest_rel is None:
            raise RuntimeError("Destination is not writable")
        src_path = self._resolve_path()
        if not src_path.exists():
            raise RuntimeError("Source file does not exist")
        dest_path_obj = self.context.datadir_registry.primary.resolve(dest_rel)
        dest_path_obj.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(src_path, dest_path_obj)

    def move_recursive(self, dest_path, **_kwargs):
        dest_rel = self.provider._resolve_datadir_rel_for_write(dest_path, self.environ)
        if dest_rel is None:
            raise RuntimeError("Destination is not writable")
        src_path = self._resolve_path()
        if not src_path.exists():
            raise RuntimeError("Source file does not exist")
        dest_path_obj = self.context.datadir_registry.primary.resolve(dest_rel)
        dest_path_obj.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.move(str(src_path), str(dest_path_obj))

    def get_property_value(self, name: Any):
        key = _normalize_prop_name(name)
        if key == "{urn:cacheinfinity}cache-state":
            return self.cache_state
        if key == "{urn:cacheinfinity}size-on-disk":
            try:
                file_path = self._resolve_path()
                return str(file_path.stat().st_size)
            except Exception:
                return "0"
        return None


class CachelinkFileResource(DAVNonCollection):
    """File resource from cachelink overlay with on-demand caching."""

    def __init__(
        self,
        path: str,
        environ: Dict[str, Any],
        provider: WebDAVProvider,
        service,
        descriptor,
        datadir_rel: PurePosixPath,
        base_rel: PurePosixPath,
        entry,
        subpath: PurePosixPath,
        *,
        allow_cache: bool,
    ) -> None:
        super().__init__(path, environ)
        self.path = path
        self.provider = provider
        self.context = service
        self.descriptor = descriptor
        self.datadir_rel = datadir_rel
        self.base_rel = base_rel
        self.entry = entry
        self.subpath = subpath
        self.allow_cache = allow_cache
        self._write_handle = None

    def exists(self):
        datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        return datadir_path.exists() or self.entry is not None

    def get_content_length(self) -> int:
        size = getattr(self.entry, "size", None)
        return int(size) if size else 0

    def get_last_modified(self) -> int:
        modified = getattr(self.entry, "modified", None)
        if not modified:
            return 0
        try:
            return int(modified.timestamp())
        except Exception:
            return 0

    def get_etag(self) -> str:
        checksum = getattr(self.entry, "checksum", None)
        return f'"{checksum}"' if checksum else f'"{hash(self.path)}"'

    def get_content(self):
        try:
            datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
            if datadir_path.exists():
                self._record_access()
                return open(datadir_path, "rb")
            if not self.allow_cache:
                return None
            if self.descriptor.mode.value == "zip":
                handle = self._handle_zip_download(datadir_path)
                if handle:
                    return handle
                return None

            remote_url = self._build_remote_url()
            if not remote_url:
                _logger.error("Could not build remote URL for %s", self.path)
                return None
            staging_path = self.context.staging.reserve_tempfile(self.subpath.name or "download")
            result = self.context.fetcher.download_file(
                remote_url,
                staging_path,
                url_handler=self.descriptor.url_handler,
                rclone_options={
                    "bandwidth_limit": self.descriptor.bandwidth_limit,
                    "transfer_concurrency": self.descriptor.transfer_concurrency,
                    "checkers": self.descriptor.checkers,
                    "timeout": self.descriptor.timeout,
                    "retries": self.descriptor.retries,
                },
            )
            if not result.success:
                _logger.error("Failed to download %s: %s", self.path, result.error_message)
                self._mark_reindex_on_failure(result.error_message or "")
                try:
                    if staging_path.exists():
                        staging_path.unlink()
                except OSError:
                    pass
                return None
            datadir_path.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.move(str(staging_path), str(datadir_path))
            self._record_backend_checksum(datadir_path)
            self._record_access()
            return open(datadir_path, "rb")
        except Exception as exc:
            _logger.error("Failed to get content for %s: %s", self.path, exc)
            return None

    def get_content_type(self):
        import mimetypes
        mime_type, _ = mimetypes.guess_type(self.path)
        return mime_type or "application/octet-stream"

    def begin_write(self, content_type=None):
        datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        datadir_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_handle = open(datadir_path, "wb")
        return self._write_handle

    def end_write(self, with_errors, **_kwargs):
        if self._write_handle:
            self._write_handle.close()
            self._write_handle = None
        if with_errors:
            try:
                datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
                if datadir_path.exists():
                    datadir_path.unlink()
            except Exception:
                return

    def delete(self):
        datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        if not datadir_path.exists():
            raise RuntimeError("File does not exist")
        datadir_path.unlink()

    def copy_recursive(self, dest_path, **_kwargs):
        dest_rel = self.provider._resolve_datadir_rel_for_write(dest_path, self.environ)
        if dest_rel is None:
            raise RuntimeError("Destination is not writable")
        src_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        if not src_path.exists():
            raise RuntimeError("Source file does not exist")
        dest_path_obj = self.context.datadir_registry.primary.resolve(dest_rel)
        dest_path_obj.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(src_path, dest_path_obj)

    def move_recursive(self, dest_path, **_kwargs):
        dest_rel = self.provider._resolve_datadir_rel_for_write(dest_path, self.environ)
        if dest_rel is None:
            raise RuntimeError("Destination is not writable")
        src_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        if not src_path.exists():
            raise RuntimeError("Source file does not exist")
        dest_path_obj = self.context.datadir_registry.primary.resolve(dest_rel)
        dest_path_obj.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.move(str(src_path), str(dest_path_obj))

    def get_property_value(self, name: Any):
        key = _normalize_prop_name(name)
        if key == "{urn:cacheinfinity}cache-state":
            datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
            if not datadir_path.exists():
                return "remote"
            return "cached" if self.context.index_db.lookup_backend_checksum(self.datadir_rel) else "local-only"
        if key == "{urn:cacheinfinity}size-on-disk":
            datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
            try:
                return str(datadir_path.stat().st_size) if datadir_path.exists() else "0"
            except Exception:
                return "0"
        return None

    def _build_remote_url(self) -> str | None:
        relative = self.subpath.as_posix().lstrip("/")
        if not relative:
            return None
        base = self.descriptor.download_root.rstrip("/")
        subfolder = self.descriptor.subfolder.strip("/")
        if self.descriptor.mode.value == "zip":
            zip_path, inner = _split_zip_subfolder(subfolder)
            if not zip_path:
                return None
            inner = inner.strip("/")
            if inner:
                return f"{base}/{zip_path}/{inner}/{relative}"
            return f"{base}/{zip_path}/{relative}"
        if subfolder:
            return f"{base}/{subfolder}/{relative}"
        return f"{base}/{relative}"

    def _record_access(self) -> None:
        try:
            state = self.context.index_db.ensure_target(self.descriptor, self.descriptor.remote_listing_url)
        except Exception:
            return
        relative_path = self.subpath.as_posix().lstrip("/")
        if not relative_path:
            return
        self.context.index_db.record_access(state.id, relative_path)
        parent = PurePosixPath(relative_path).parent
        for _ in range(2):
            if str(parent) == ".":
                break
            self.context.index_db.record_access(state.id, parent.as_posix())
            parent = parent.parent

    def _mark_reindex_on_failure(self, error_message: str) -> None:
        lowered = error_message.lower()
        if "404" not in lowered and "5xx" not in lowered and "http 5" not in lowered:
            return
        try:
            state = self.context.index_db.ensure_target(self.descriptor, self.descriptor.remote_listing_url)
            self.context.index_db.mark_needs_full(state.id)
        except Exception:
            return

    def _record_backend_checksum(self, datadir_path) -> None:
        try:
            sha256 = hashlib.sha256()
            with open(datadir_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    sha256.update(chunk)
            self.context.index_db.record_backend_checksum(
                self.datadir_rel,
                "sha256",
                sha256.hexdigest(),
                source="download",
            )
        except Exception:
            return

    def _handle_zip_download(self, datadir_path: "Path"):
        import shutil
        import tempfile
        import zipfile
        limits = self.context.settings.limits
        max_bytes = max(0, limits.max_zip_total_gb) * 1024**3
        zip_path, inner = _split_zip_subfolder(self.descriptor.subfolder)
        if not zip_path:
            return None
        inner = inner.strip("/")
        base = self.descriptor.download_root.rstrip("/")
        remote_zip_url = f"{base}/{zip_path}"

        lock = self.provider._zip_cache_lock
        use_whole_zip = True
        acquired = False
        if limits.one_zip_cache_at_a_time:
            acquired = lock.acquire(blocking=False)
            if not acquired:
                use_whole_zip = False

        if not use_whole_zip:
            return self._download_single_file(datadir_path)

        staging_zip = self.context.staging.reserve_tempfile("zip")
        result = self.context.fetcher.download_file(
            remote_zip_url,
            staging_zip,
            url_handler=self.descriptor.url_handler,
            rclone_options={
                "bandwidth_limit": self.descriptor.bandwidth_limit,
                "transfer_concurrency": self.descriptor.transfer_concurrency,
                "checkers": self.descriptor.checkers,
                "timeout": self.descriptor.timeout,
                "retries": self.descriptor.retries,
            },
        )
        if not result.success:
            if acquired:
                lock.release()
            _logger.error("Failed to download zip %s: %s", remote_zip_url, result.error_message)
            self._mark_reindex_on_failure(result.error_message or "")
            try:
                if staging_zip.exists():
                    staging_zip.unlink()
            except OSError:
                pass
            return None

        try:
            zip_size = staging_zip.stat().st_size
            if max_bytes and zip_size > max_bytes:
                _logger.info("Zip size exceeds limit; falling back to per-file download")
                release_lock = lock if acquired else None
                return self._download_single_file(datadir_path, cleanup_path=staging_zip, release_lock=release_lock)

            with zipfile.ZipFile(staging_zip, "r") as archive:
                members = [info for info in archive.infolist() if not info.is_dir()]
                prefix = inner.strip("/") + "/" if inner else ""
                requested_member = None
                relative = self.subpath.as_posix().lstrip("/")
                if relative:
                    requested_member = f"{prefix}{relative}" if prefix else relative

                filtered = [
                    info for info in members if info.filename.startswith(prefix)
                ]
                total_uncompressed = sum(info.file_size for info in filtered)
                if max_bytes and total_uncompressed > max_bytes:
                    _logger.info("Zip uncompressed size exceeds limit; falling back to per-file download")
                    release_lock = lock if acquired else None
                    return self._download_single_file(datadir_path, cleanup_path=staging_zip, release_lock=release_lock)

                served_handle = None
                if requested_member:
                    for info in filtered:
                        if info.filename == requested_member:
                            served_handle = tempfile.NamedTemporaryFile(
                                prefix="ci-zip-member-",
                                dir=self.context.staging.base_path,
                                delete=True,
                            )
                            with archive.open(info) as src:
                                shutil.copyfileobj(src, served_handle)
                            served_handle.seek(0)
                            break

                for info in filtered:
                    relative_name = info.filename[len(prefix):] if prefix else info.filename
                    if not relative_name:
                        continue
                    rel_path = PurePosixPath(relative_name)
                    if rel_path.is_absolute() or ".." in rel_path.parts:
                        continue
                    dest_rel = self.base_rel / rel_path
                    dest_path = self.context.datadir_registry.primary.resolve(dest_rel)
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as src, open(dest_path, "wb") as dst:
                        for chunk in iter(lambda: src.read(1024 * 1024), b""):
                            dst.write(chunk)
                    self._record_backend_checksum(dest_path)
            self._record_access()
            if acquired:
                lock.release()
                acquired = False
            if served_handle is not None:
                return served_handle
            return open(datadir_path, "rb") if datadir_path.exists() else None
        finally:
            try:
                if staging_zip.exists():
                    staging_zip.unlink()
            except OSError:
                pass
            if acquired:
                try:
                    lock.release()
                except Exception:
                    pass

    def _download_single_file(self, datadir_path: "Path", *, cleanup_path: "Path | None" = None, release_lock=None):
        if cleanup_path is not None:
            try:
                if cleanup_path.exists():
                    cleanup_path.unlink()
            except OSError:
                pass
        if release_lock is not None:
            try:
                release_lock.release()
            except Exception:
                pass
        remote_url = self._build_remote_url()
        if not remote_url:
            return None
        staging_path = self.context.staging.reserve_tempfile(self.subpath.name or "download")
        result = self.context.fetcher.download_file(
            remote_url,
            staging_path,
            url_handler=self.descriptor.url_handler,
            rclone_options={
                "bandwidth_limit": self.descriptor.bandwidth_limit,
                "transfer_concurrency": self.descriptor.transfer_concurrency,
                "checkers": self.descriptor.checkers,
                "timeout": self.descriptor.timeout,
                "retries": self.descriptor.retries,
            },
        )
        if not result.success:
            _logger.error("Failed to download %s: %s", self.path, result.error_message)
            self._mark_reindex_on_failure(result.error_message or "")
            try:
                if staging_path.exists():
                    staging_path.unlink()
            except OSError:
                pass
            return None
        datadir_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.move(str(staging_path), str(datadir_path))
        self._record_backend_checksum(datadir_path)
        self._record_access()
        return open(datadir_path, "rb")


class CachelinkDirectoryResource(DAVCollection):
    """Directory resource with cachelink overlay."""

    def __init__(
        self,
        path: str,
        environ: Dict[str, Any],
        provider: WebDAVProvider,
        service,
        share,
        share_rel: PurePosixPath,
        datadir_rel: PurePosixPath,
        *,
        descriptor=None,
        subpath: PurePosixPath | None = None,
        frontend_root: str = "/",
        policy=None,
        cache_state: str = "remote",
    ) -> None:
        super().__init__(path, environ)
        self.path = path
        self.provider = provider
        self.context = service
        self.share = share
        self.share_rel = share_rel
        self.datadir_rel = datadir_rel
        self.descriptor = descriptor
        self.subpath = subpath or PurePosixPath("")
        self.frontend_root = frontend_root or "/"
        self.policy = policy
        self.cache_state = cache_state

    def exists(self):
        return True

    def get_content_length(self) -> int:
        return 0

    def get_last_modified(self) -> int:
        return int(time.time())

    def get_etag(self) -> str:
        return f'"{hash(self.path)}"'

    def get_member_names(self):
        members = set()
        datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        if datadir_path.exists() and datadir_path.is_dir():
            for item in datadir_path.iterdir():
                members.add(item.name)
        if self.provider._cachelink_overlay_enabled(self.share, self.policy):
            if self.descriptor:
                for name in self.provider._cachelink_children(self.descriptor, self.subpath).keys():
                    members.add(name)
            else:
                overlay_paths = self.provider._cachelink_members(
                    self.frontend_root,
                    self.share_rel,
                    self.datadir_rel,
                    self.share,
                )
                for full_path in overlay_paths:
                    cleaned = full_path.rstrip("/")
                    if cleaned:
                        members.add(cleaned.split("/")[-1])
        return sorted(members)

    def get_member_list(self):
        members = []
        for name in self.get_member_names():
            child_path = self.path.rstrip("/") + "/" + name
            res = self.provider.get_resource_inst(child_path, self.environ)
            if res:
                members.append(res)
        return members

    def create_collection(self, name):
        if not name:
            raise RuntimeError("Folder name required")
        datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        target = datadir_path / name
        target.mkdir(parents=True, exist_ok=False)

    def create_empty_resource(self, name):
        child_rel = self.datadir_rel / name
        return DatadirFileResource(
            self.path.rstrip("/") + "/" + name,
            self.environ,
            self.provider,
            self.context,
            child_rel,
            cache_state="local-only",
        )

    def delete(self):
        datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        if not datadir_path.exists():
            raise RuntimeError("Directory does not exist")
        if any(datadir_path.iterdir()):
            raise RuntimeError("Directory is not empty")
        datadir_path.rmdir()

    def copy_recursive(self, dest_path, **_kwargs):
        dest_rel = self.provider._resolve_datadir_rel_for_write(dest_path, self.environ)
        if dest_rel is None:
            raise RuntimeError("Destination is not writable")
        src_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        if not src_path.exists():
            raise RuntimeError("Source directory does not exist")
        dest_path_obj = self.context.datadir_registry.primary.resolve(dest_rel)
        import shutil
        shutil.copytree(src_path, dest_path_obj, dirs_exist_ok=True)

    def move_recursive(self, dest_path, **_kwargs):
        dest_rel = self.provider._resolve_datadir_rel_for_write(dest_path, self.environ)
        if dest_rel is None:
            raise RuntimeError("Destination is not writable")
        src_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        if not src_path.exists():
            raise RuntimeError("Source directory does not exist")
        dest_path_obj = self.context.datadir_registry.primary.resolve(dest_rel)
        import shutil
        shutil.move(str(src_path), str(dest_path_obj))

    def get_property_value(self, name: Any):
        key = _normalize_prop_name(name)
        if key == "{urn:cacheinfinity}cache-state":
            return self.cache_state
        if key == "{urn:cacheinfinity}size-on-disk":
            return "0"
        return None


class DatadirDirectoryResource(DAVCollection):
    """Directory resource backed by datadir storage."""

    def __init__(
        self,
        path: str,
        environ: Dict[str, Any],
        provider: WebDAVProvider,
        service,
        share,
        share_rel: PurePosixPath,
        datadir_rel: PurePosixPath,
        *,
        frontend_root: str = "/",
        policy=None,
        cache_state: str = "local-only",
    ) -> None:
        super().__init__(path, environ)
        self.path = path
        self.provider = provider
        self.context = service
        self.share = share
        self.share_rel = share_rel
        self.datadir_rel = datadir_rel
        self.frontend_root = frontend_root or "/"
        self.policy = policy
        self.cache_state = cache_state

    def exists(self):
        datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        return datadir_path.exists() and datadir_path.is_dir()

    def get_content_length(self) -> int:
        return 0

    def get_last_modified(self) -> int:
        try:
            datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
            return int(datadir_path.stat().st_mtime)
        except Exception:
            return int(time.time())

    def get_etag(self) -> str:
        return f'"{hash(self.path)}"'

    def get_member_names(self):
        members = set()
        datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        if datadir_path.exists() and datadir_path.is_dir():
            for item in datadir_path.iterdir():
                members.add(item.name)
        if self.provider._cachelink_overlay_enabled(self.share, self.policy):
            overlay_paths = self.provider._cachelink_members(
                self.frontend_root,
                self.share_rel,
                self.datadir_rel,
                self.share,
            )
            for full_path in overlay_paths:
                cleaned = full_path.rstrip("/")
                if cleaned:
                    members.add(cleaned.split("/")[-1])
        return sorted(members)

    def get_member_list(self):
        members = []
        for name in self.get_member_names():
            child_path = self.path.rstrip("/") + "/" + name
            res = self.provider.get_resource_inst(child_path, self.environ)
            if res:
                members.append(res)
        return members

    def create_collection(self, name):
        if not name:
            raise RuntimeError("Folder name required")
        datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        target = datadir_path / name
        target.mkdir(parents=True, exist_ok=False)

    def create_empty_resource(self, name):
        child_rel = self.datadir_rel / name
        return DatadirFileResource(
            self.path.rstrip("/") + "/" + name,
            self.environ,
            self.provider,
            self.context,
            child_rel,
            cache_state="local-only",
        )

    def delete(self):
        datadir_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        if not datadir_path.exists():
            raise RuntimeError("Directory does not exist")
        if any(datadir_path.iterdir()):
            raise RuntimeError("Directory is not empty")
        datadir_path.rmdir()

    def copy_recursive(self, dest_path, **_kwargs):
        dest_rel = self.provider._resolve_datadir_rel_for_write(dest_path, self.environ)
        if dest_rel is None:
            raise RuntimeError("Destination is not writable")
        src_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        if not src_path.exists():
            raise RuntimeError("Source directory does not exist")
        dest_path_obj = self.context.datadir_registry.primary.resolve(dest_rel)
        import shutil
        shutil.copytree(src_path, dest_path_obj, dirs_exist_ok=True)

    def move_recursive(self, dest_path, **_kwargs):
        dest_rel = self.provider._resolve_datadir_rel_for_write(dest_path, self.environ)
        if dest_rel is None:
            raise RuntimeError("Destination is not writable")
        src_path = self.context.datadir_registry.primary.resolve(self.datadir_rel)
        if not src_path.exists():
            raise RuntimeError("Source directory does not exist")
        dest_path_obj = self.context.datadir_registry.primary.resolve(dest_rel)
        import shutil
        shutil.move(str(src_path), str(dest_path_obj))

    def get_property_value(self, name: Any):
        key = _normalize_prop_name(name)
        if key == "{urn:cacheinfinity}cache-state":
            return self.cache_state
        if key == "{urn:cacheinfinity}size-on-disk":
            return "0"
        return None
