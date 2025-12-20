"""Command-line interface for CacheInfinity administration."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import socket
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _require_file(path: str) -> Path:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    return file_path


class LocalControlClient:
    def __init__(self, socket_path: Path) -> None:
        self._socket_path = socket_path

    def request(self, payload: dict) -> dict:
        if not self._socket_path.exists():
            raise RuntimeError(f"Local control socket not found: {self._socket_path}")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(str(self._socket_path))
            sock.sendall(json.dumps(payload).encode("utf-8"))
            sock.shutdown(socket.SHUT_WR)
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        response = json.loads(data.decode("utf-8") or "{}")
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "Unknown error"))
        return response.get("result", {})


def _runtime_root() -> Path:
    candidates = [Path("/run"), Path("/var/run")]
    for base in candidates:
        if base.exists() and os.access(base, os.W_OK | os.X_OK):
            return base / "cacheinfinity"
    tmp_base = Path(os.getenv("TMPDIR") or "/tmp")
    return tmp_base / "cacheinfinity"


def _handle_status(client: LocalControlClient, args) -> int:
    _print_json(client.request({"command": "status"}))
    return 0


def _handle_storage(client: LocalControlClient, args) -> int:
    if args.action == "list":
        result = client.request(
            {
                "command": "storage",
                "action": "list",
                "args": {
                    "location": args.location,
                    "path": args.path,
                    "sort_by": args.sort_by,
                    "sort_order": args.sort_order,
                    "view_mode": args.view_mode,
                    "show_hidden": args.show_hidden,
                    "search_query": args.search_query,
                },
            }
        )
        _print_json(result)
        return 0
    if args.action == "upload":
        data = _require_file(args.file).read_bytes()
        client.request(
            {
                "command": "storage",
                "action": "upload",
                "args": {
                    "location": args.location,
                    "path": args.path,
                    "filename": Path(args.file).name,
                    "data": base64.b64encode(data).decode("ascii"),
                },
            }
        )
        print("ok")
        return 0
    if args.action == "mkdir":
        client.request(
            {
                "command": "storage",
                "action": "mkdir",
                "args": {
                    "location": args.location,
                    "path": args.path,
                    "name": args.name,
                },
            }
        )
        print("ok")
        return 0
    if args.action == "delete":
        client.request(
            {
                "command": "storage",
                "action": "delete",
                "args": {
                    "location": args.location,
                    "path": args.path,
                },
            }
        )
        print("ok")
        return 0
    raise ValueError("Unknown storage action")


def _handle_cachelinks(client: LocalControlClient, args) -> int:
    if args.action == "list":
        result = client.request({"command": "cachelinks", "action": "list"})
        _print_json(result.get("cachelinks", result))
        return 0
    if args.action == "tree":
        _print_json(client.request({"command": "cachelinks", "action": "tree"}))
        return 0
    if args.action == "create":
        result = client.request(
            {
                "command": "cachelinks",
                "action": "create",
                "args": {
                    "parent_path": args.parent_path,
                    "name": args.name,
                    "url": args.url,
                    "subfolder": args.subfolder,
                },
            }
        )
        _print_json(result)
        return 0
    if args.action == "update":
        client.request(
            {
                "command": "cachelinks",
                "action": "update",
                "args": {
                    "canonical_id": args.canonical_id,
                    "url": args.url,
                    "subfolder": args.subfolder,
                },
            }
        )
        print("ok")
        return 0
    if args.action == "delete":
        client.request(
            {
                "command": "cachelinks",
                "action": "delete",
                "args": {"canonical_id": args.canonical_id},
            }
        )
        print("ok")
        return 0
    if args.action == "preview":
        _print_json(
            client.request(
                {
                    "command": "cachelinks",
                    "action": "preview",
                    "args": {"url": args.url, "subfolder": args.subfolder},
                }
            )
        )
        return 0
    if args.action == "folder-add":
        client.request(
            {
                "command": "cachelinks",
                "action": "folder-add",
                "args": {"path": args.path},
            }
        )
        print("ok")
        return 0
    if args.action == "folder-delete":
        client.request(
            {
                "command": "cachelinks",
                "action": "folder-delete",
                "args": {"path": args.path},
            }
        )
        print("ok")
        return 0
    raise ValueError("Unknown cachelinks action")


def _handle_cookies(client: LocalControlClient, args) -> int:
    if args.action == "list":
        _print_json(client.request({"command": "cookies", "action": "list"}).get("cookies", []))
        return 0
    if args.action == "upload":
        if args.file:
            content = _require_file(args.file).read_text(encoding="utf-8")
        else:
            content = args.content or ""
        client.request(
            {
                "command": "cookies",
                "action": "upload",
                "args": {"domain": args.domain, "content": content},
            }
        )
        print("ok")
        return 0
    if args.action == "credentials":
        client.request(
            {
                "command": "cookies",
                "action": "credentials",
                "args": {
                    "domain": args.domain,
                    "username": args.username,
                    "password": args.password,
                },
            }
        )
        print("ok")
        return 0
    if args.action == "refresh":
        client.request(
            {
                "command": "cookies",
                "action": "refresh",
                "args": {"domain": args.domain},
            }
        )
        print("ok")
        return 0
    if args.action == "domain-add":
        client.request(
            {
                "command": "cookies",
                "action": "domain-add",
                "args": {
                    "domain": args.domain,
                    "credfile": args.credfile,
                    "cookie_jar": args.cookie_jar,
                    "credfile_path": args.credfile_path,
                },
            }
        )
        print("ok")
        return 0
    raise ValueError("Unknown cookies action")


def _handle_users(client: LocalControlClient, args) -> int:
    if args.user_type == "admin":
        if args.action == "list":
            result = client.request(
                {"command": "users", "action": "list", "args": {"user_type": "admin"}}
            )
            _print_json(result.get("users", []))
            return 0
        if args.action == "set":
            client.request(
                {
                    "command": "users",
                    "action": "set",
                    "args": {
                        "user_type": "admin",
                        "username": args.username,
                        "password": args.password,
                        "enabled": args.enabled,
                        "admin": args.admin,
                    },
                }
            )
            print("ok")
            return 0
        if args.action == "disable":
            client.request(
                {
                    "command": "users",
                    "action": "disable",
                    "args": {"user_type": "admin", "username": args.username},
                }
            )
            print("ok")
            return 0
    elif args.user_type == "webdav":
        if args.action == "list":
            _print_json(
                client.request({"command": "users", "action": "list", "args": {"user_type": "webdav"}})
            )
            return 0
        if args.action == "set":
            client.request(
                {
                    "command": "users",
                    "action": "set",
                    "args": {
                        "user_type": "webdav",
                        "username": args.username,
                        "password": args.password,
                        "enabled": args.enabled,
                        "share": args.share,
                        "login": args.login,
                        "read": args.read,
                        "write": args.write,
                        "cache": args.cache,
                    },
                }
            )
            print("ok")
            return 0
        if args.action == "disable":
            client.request(
                {
                    "command": "users",
                    "action": "disable",
                    "args": {
                        "user_type": "webdav",
                        "username": args.username,
                        "share": args.share,
                    },
                }
            )
            print("ok")
            return 0
    raise ValueError("Unknown users action")


def _handle_webdav(client: LocalControlClient, args) -> int:
    if args.action == "list":
        _print_json(client.request({"command": "webdav", "action": "list"}))
        return 0
    if args.action == "set":
        client.request(
            {
                "command": "webdav",
                "action": "set",
                "args": {
                    "share": args.share,
                    "username": args.username,
                    "password": args.password,
                    "enabled": args.enabled,
                    "login": args.login,
                    "read": args.read,
                    "write": args.write,
                    "cache": args.cache,
                },
            }
        )
        print("ok")
        return 0
    if args.action == "remove":
        client.request(
            {
                "command": "webdav",
                "action": "remove",
                "args": {"share": args.share, "username": args.username},
            }
        )
        print("ok")
        return 0
    raise ValueError("Unknown webdav action")


def _handle_keys(client: LocalControlClient, args) -> int:
    if args.action == "list":
        result = client.request({"command": "keys", "action": "list"})
        _print_json(result.get("keys", []))
        return 0
    if args.action == "generate":
        _print_json(
            client.request({"command": "keys", "action": "generate", "args": {"username": args.username}})
        )
        return 0
    if args.action == "revoke":
        client.request({"command": "keys", "action": "revoke", "args": {"username": args.username}})
        print("ok")
        return 0
    raise ValueError("Unknown keys action")


def _handle_settings(client: LocalControlClient, args) -> int:
    if args.action == "detail":
        _print_json(client.request({"command": "settings", "action": "detail"}))
        return 0
    if args.action == "update-detail":
        payload = json.loads(_require_file(args.file).read_text(encoding="utf-8"))
        client.request({"command": "settings", "action": "update-detail", "args": {"payload": payload}})
        print("ok")
        return 0
    if args.action == "get-config":
        _print_json(client.request({"command": "settings", "action": "get-config"}))
        return 0
    if args.action == "update-config":
        settings_text = _require_file(args.settings_file).read_text(encoding="utf-8") if args.settings_file else None
        cachelinks_text = _require_file(args.cachelinks_file).read_text(encoding="utf-8") if args.cachelinks_file else None
        client.request(
            {
                "command": "settings",
                "action": "update-config",
                "args": {
                    "settings_text": settings_text,
                    "cachelinks_text": cachelinks_text,
                },
            }
        )
        print("ok")
        return 0
    raise ValueError("Unknown settings action")


def _handle_maintenance(client: LocalControlClient, args) -> int:
    if args.action == "degraded":
        _print_json(client.request({"command": "maintenance", "action": "degraded"}).get("degraded", []))
        return 0
    if args.action == "reindex":
        client.request(
            {
                "command": "maintenance",
                "action": "reindex",
                "args": {"canonical_id": args.canonical_id},
            }
        )
        print("ok")
        return 0
    if args.action == "reload":
        client.request(
            {
                "command": "maintenance",
                "action": "reload",
                "args": {"allow_switch": args.allow_switch, "dump": args.dump},
            }
        )
        print("ok")
        return 0
    if args.action == "reinit":
        client.request({"command": "maintenance", "action": "reinit"})
        print("ok")
        return 0
    if args.action == "shutdown":
        client.request({"command": "maintenance", "action": "shutdown"})
        print("ok")
        return 0
    raise ValueError("Unknown maintenance action")


def create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CacheInfinity admin CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show system status")

    storage = subparsers.add_parser("storage", help="Manage storage")
    storage_sub = storage.add_subparsers(dest="action", required=True)
    storage_list = storage_sub.add_parser("list", help="List storage entries")
    storage_list.add_argument("--location", default="datadir")
    storage_list.add_argument("--path", default="/")
    storage_list.add_argument("--sort-by")
    storage_list.add_argument("--sort-order", default="asc")
    storage_list.add_argument("--view-mode")
    storage_list.add_argument("--show-hidden", action="store_true")
    storage_list.add_argument("--search-query")

    storage_upload = storage_sub.add_parser("upload", help="Upload file")
    storage_upload.add_argument("--location", default="datadir")
    storage_upload.add_argument("--path", required=True)
    storage_upload.add_argument("--file", required=True)

    storage_mkdir = storage_sub.add_parser("mkdir", help="Create folder")
    storage_mkdir.add_argument("--location", default="datadir")
    storage_mkdir.add_argument("--path", required=True)
    storage_mkdir.add_argument("--name", required=True)

    storage_delete = storage_sub.add_parser("delete", help="Delete entry")
    storage_delete.add_argument("--location", default="datadir")
    storage_delete.add_argument("--path", required=True)

    cachelinks = subparsers.add_parser("cachelinks", help="Manage cachelinks")
    cachelinks_sub = cachelinks.add_subparsers(dest="action", required=True)
    cachelinks_sub.add_parser("list", help="List cachelinks")
    cachelinks_sub.add_parser("tree", help="Show cachelink tree")
    cachelinks_create = cachelinks_sub.add_parser("create", help="Create cachelink")
    cachelinks_create.add_argument("--parent-path", required=True)
    cachelinks_create.add_argument("--name", required=True)
    cachelinks_create.add_argument("--url", required=True)
    cachelinks_create.add_argument("--subfolder", default="/")
    cachelinks_update = cachelinks_sub.add_parser("update", help="Update cachelink")
    cachelinks_update.add_argument("--canonical-id", required=True)
    cachelinks_update.add_argument("--url")
    cachelinks_update.add_argument("--subfolder")
    cachelinks_delete = cachelinks_sub.add_parser("delete", help="Delete cachelink")
    cachelinks_delete.add_argument("--canonical-id", required=True)
    cachelinks_preview = cachelinks_sub.add_parser("preview", help="Preview cachelink")
    cachelinks_preview.add_argument("--url", required=True)
    cachelinks_preview.add_argument("--subfolder", default="/")
    cachelinks_folder_add = cachelinks_sub.add_parser("folder-add", help="Add cachelink folder")
    cachelinks_folder_add.add_argument("--path", required=True)
    cachelinks_folder_delete = cachelinks_sub.add_parser("folder-delete", help="Delete cachelink folder")
    cachelinks_folder_delete.add_argument("--path", required=True)

    cookies = subparsers.add_parser("cookies", help="Manage cookies")
    cookies_sub = cookies.add_subparsers(dest="action", required=True)
    cookies_sub.add_parser("list", help="List cookies")
    cookies_upload = cookies_sub.add_parser("upload", help="Upload cookies")
    cookies_upload.add_argument("--domain", required=True)
    cookies_upload.add_argument("--file")
    cookies_upload.add_argument("--content")
    cookies_creds = cookies_sub.add_parser("credentials", help="Update cookie credentials")
    cookies_creds.add_argument("--domain", required=True)
    cookies_creds.add_argument("--username", required=True)
    cookies_creds.add_argument("--password", required=True)
    cookies_refresh = cookies_sub.add_parser("refresh", help="Refresh cookies")
    cookies_refresh.add_argument("--domain", required=True)
    cookies_domain = cookies_sub.add_parser("domain-add", help="Add cookie domain")
    cookies_domain.add_argument("--domain", required=True)
    cookies_domain.add_argument("--cookie-jar")
    cookies_domain.add_argument("--credfile", action="store_true")
    cookies_domain.add_argument("--credfile-path")

    users = subparsers.add_parser("users", help="Manage users")
    users.add_argument("--type", dest="user_type", choices=["admin", "webdav"], required=True)
    users_sub = users.add_subparsers(dest="action", required=True)
    users_sub.add_parser("list", help="List users")
    users_set = users_sub.add_parser("set", help="Create or update user")
    users_set.add_argument("--username", required=True)
    users_set.add_argument("--password")
    users_set.add_argument("--enabled", action="store_true")
    users_set.add_argument("--admin", action="store_true")
    users_set.add_argument("--share")
    users_set.add_argument("--login", action="store_true")
    users_set.add_argument("--read", action="store_true")
    users_set.add_argument("--write", action="store_true")
    users_set.add_argument("--cache", action="store_true")
    users_disable = users_sub.add_parser("disable", help="Disable user")
    users_disable.add_argument("--username", required=True)
    users_disable.add_argument("--share")

    webdav = subparsers.add_parser("webdav", help="Manage WebDAV shares and permissions")
    webdav_sub = webdav.add_subparsers(dest="action", required=True)
    webdav_sub.add_parser("list", help="List WebDAV users")
    webdav_set = webdav_sub.add_parser("set", help="Create or update WebDAV mapping")
    webdav_set.add_argument("--share", required=True)
    webdav_set.add_argument("--username", required=True)
    webdav_set.add_argument("--password")
    webdav_set.add_argument("--enabled", action="store_true")
    webdav_set.add_argument("--login", action="store_true")
    webdav_set.add_argument("--read", action="store_true")
    webdav_set.add_argument("--write", action="store_true")
    webdav_set.add_argument("--cache", action="store_true")
    webdav_remove = webdav_sub.add_parser("remove", help="Remove WebDAV mapping")
    webdav_remove.add_argument("--share", required=True)
    webdav_remove.add_argument("--username", required=True)

    keys = subparsers.add_parser("keys", help="Manage API keys")
    keys_sub = keys.add_subparsers(dest="action", required=True)
    keys_sub.add_parser("list", help="List API keys")
    keys_generate = keys_sub.add_parser("generate", help="Generate API key")
    keys_generate.add_argument("--username", required=True)
    keys_revoke = keys_sub.add_parser("revoke", help="Revoke API key")
    keys_revoke.add_argument("--username", required=True)

    settings = subparsers.add_parser("settings", help="Manage settings")
    settings_sub = settings.add_subparsers(dest="action", required=True)
    settings_sub.add_parser("detail", help="Show settings detail")
    settings_update_detail = settings_sub.add_parser("update-detail", help="Update settings detail from JSON")
    settings_update_detail.add_argument("--file", required=True)
    settings_sub.add_parser("get-config", help="Get config payload")
    settings_update_config = settings_sub.add_parser("update-config", help="Update config payload")
    settings_update_config.add_argument("--settings-file")
    settings_update_config.add_argument("--cachelinks-file")

    maintenance = subparsers.add_parser("maintenance", help="Maintenance operations")
    maintenance_sub = maintenance.add_subparsers(dest="action", required=True)
    maintenance_sub.add_parser("degraded", help="List degraded targets")
    maintenance_reindex = maintenance_sub.add_parser("reindex", help="Trigger reindex")
    maintenance_reindex.add_argument("--canonical-id", required=True)
    maintenance_reload = maintenance_sub.add_parser("reload", help="Reload configuration and reinitialize service")
    maintenance_reload.add_argument("--allow-switch", action="store_true", help="Allow switching databases on reload")
    maintenance_reload.add_argument("--dump", action="store_true", help="Dump bootstrap.yml before switching databases")
    maintenance_sub.add_parser("reinit", help="Restart the running service process")
    maintenance_sub.add_parser("shutdown", help="Stop the running service process")

    return parser


def main() -> int:
    parser = create_argument_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    runtime_path = _runtime_root() / "runtime.json"
    if not runtime_path.exists():
        raise RuntimeError(f"No running server found (missing {runtime_path})")
    payload = json.loads(runtime_path.read_text(encoding="utf-8"))
    pid = payload.get("pid")
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
        except OSError:
            runtime_path.unlink(missing_ok=True)
            raise RuntimeError("Stale runtime file (server not running)")
    socket_path = Path(payload.get("socket_path", ""))
    if not socket_path:
        raise RuntimeError("Invalid runtime info: socket_path missing")
    client = LocalControlClient(socket_path)
    if args.command == "status":
        return _handle_status(client, args)
    if args.command == "storage":
        return _handle_storage(client, args)
    if args.command == "cachelinks":
        return _handle_cachelinks(client, args)
    if args.command == "cookies":
        return _handle_cookies(client, args)
    if args.command == "users":
        return _handle_users(client, args)
    if args.command == "webdav":
        return _handle_webdav(client, args)
    if args.command == "keys":
        return _handle_keys(client, args)
    if args.command == "settings":
        return _handle_settings(client, args)
    if args.command == "maintenance":
        return _handle_maintenance(client, args)
    raise SystemExit("Unknown command")


if __name__ == "__main__":
    raise SystemExit(main())
