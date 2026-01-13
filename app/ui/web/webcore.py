"""Flask-powered WebUI application core."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request

from ui.backend import ManagementContext, ManagementLayer

_LOGGER = logging.getLogger(__name__)


class WebUIApp:
    """Flask application wrapper for the admin WebUI."""

    def __init__(
        self,
        *,
        settings,
        index_db,
        auth_manager,
        datadir_registry,
        staging,
        cachelinks,
        fetcher,
        indexer,
        checksum_catalog,
    ) -> None:
        self.management = ManagementLayer(
            ManagementContext(
                settings=settings,
                index_db=index_db,
                auth_manager=auth_manager,
                datadir_registry=datadir_registry,
                staging=staging,
                cachelinks=cachelinks,
                fetcher=fetcher,
                indexer=indexer,
                checksum_catalog=checksum_catalog,
            )
        )
        assets_dir = Path(__file__).resolve().parent / "assets"
        pages_dir = assets_dir / "pages"
        self.app = Flask(
            __name__,
            static_folder=str(assets_dir),
            static_url_path="/assets",
            template_folder=str(pages_dir),
        )
        self._configure_routes()

    def __call__(self, environ, start_response):
        return self.app(environ, start_response)

    def _current_user(self) -> str | None:
        token = request.cookies.get("ci_session")
        if not token:
            return None
        result = self.management.auth("session_validate", token=token)
        username = result.get("valid") if isinstance(result, dict) else None
        if isinstance(username, str) and username:
            return username
        return None

    def _require_auth(self):
        user = self._current_user()
        if not user:
            if request.method == "GET" and request.path == "/":
                return redirect("/login")
            return jsonify({"error": "Authentication required"}), 401
        return None

    def _configure_routes(self) -> None:
        app = self.app

        @app.before_request
        def _auth_guard():
            if request.path.startswith("/assets/"):
                return None
            if request.path in ("/login", "/logout"):
                return None
            if request.path == "/favicon.ico":
                return ("", 204)
            return self._require_auth()

        @app.route("/", methods=["GET"])
        def index():
            return render_template("index.html")

        @app.route("/login", methods=["GET", "POST"])
        def login():
            if request.method == "POST":
                username = request.form.get("username") or ""
                password = request.form.get("password") or ""
                result = self.management.auth("login", username=username, password=password)
                token = result.get("token") if isinstance(result, dict) else None
                if token:
                    response = redirect("/")
                    secure = request.is_secure
                    response.set_cookie(
                        "ci_session",
                        token,
                        httponly=True,
                        samesite="Lax",
                        secure=secure,
                    )
                    return response
                return render_template("login.html", error="Invalid credentials")
            return render_template("login.html", error=None)

        @app.route("/logout", methods=["GET"])
        def logout():
            response = redirect("/login")
            response.set_cookie("ci_session", "", max_age=0, httponly=True, samesite="Lax")
            return response

        @app.route("/session", methods=["GET"])
        def session_info():
            username = self._current_user()
            if not username:
                return jsonify({"error": "Unauthorized"}), 401
            level = logging.getLevelName(logging.getLogger().getEffectiveLevel())
            if not isinstance(level, str):
                level = "INFO"
            return jsonify({"username": username, "log_level": level})

        @app.route("/status", methods=["GET"])
        def status():
            return jsonify(self.management.system("status"))

        @app.route("/shares", methods=["GET"])
        def shares():
            return jsonify(self.management.shares("list"))

        @app.route("/storage", methods=["GET"])
        def storage_overview():
            return jsonify(self.management.system("storage"))

        @app.route("/storage/entries", methods=["GET", "DELETE"])
        def storage_entries():
            location = request.args.get("location", "datadir")
            relative = request.args.get("relative", "/")
            if request.method == "DELETE":
                return jsonify(self.management.storage("delete", location=location, path=relative))
            return jsonify(
                self.management.storage(
                    "list",
                    location=location,
                    path=relative,
                    sort_by=request.args.get("sort_by"),
                    sort_order=request.args.get("sort_order"),
                    view_mode=request.args.get("view_mode"),
                    show_hidden=request.args.get("show_hidden", "false").lower() == "true",
                    search_query=request.args.get("search_query", ""),
                )
            )

        @app.route("/storage/upload", methods=["POST"])
        def storage_upload():
            location = request.form.get("location", "datadir")
            relative_path = request.form.get("relative_path", "/")
            upload = request.files.get("file")
            if not upload:
                return jsonify({"error": "No file uploaded"}), 400
            payload = {
                "location": location,
                "path": relative_path,
                "filename": upload.filename or "upload.bin",
                "data": base64.b64encode(upload.read()).decode("ascii"),
            }
            return jsonify(self.management.storage("upload", **payload))

        @app.route("/storage/folder", methods=["POST", "DELETE"])
        def storage_folder():
            if request.method == "DELETE":
                location = request.args.get("location", "datadir")
                relative = request.args.get("relative", "/")
                return jsonify(self.management.storage("delete", location=location, path=relative))
            payload = request.get_json(silent=True) or {}
            location = payload.get("location", "datadir")
            return jsonify(
                self.management.storage(
                    "mkdir",
                    location=location,
                    path=payload.get("relative_path", "/"),
                    name=payload.get("name", ""),
                )
            )

        @app.route("/cachelinks", methods=["POST"])
        def cachelinks_create():
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.cachelinks(
                    "create",
                    parent_path=payload.get("parent_path"),
                    name=payload.get("name"),
                    url=payload.get("url"),
                    subfolder=payload.get("subfolder", "/"),
                    url_handler=payload.get("url_handler"),
                )
            )

        @app.route("/cachelinks/tree", methods=["GET"])
        def cachelinks_tree():
            return jsonify(self.management.cachelinks("tree"))

        @app.route("/cachelinks/update", methods=["POST"])
        def cachelinks_update():
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.cachelinks(
                    "update",
                    canonical_id=payload.get("canonical_id"),
                    url=payload.get("url"),
                    subfolder=payload.get("subfolder"),
                    url_handler=payload.get("url_handler"),
                )
            )

        @app.route("/cachelinks/preview", methods=["POST"])
        def cachelinks_preview():
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.cachelinks(
                    "preview",
                    url=payload.get("url"),
                    subfolder=payload.get("subfolder", "/"),
                    url_handler=payload.get("url_handler"),
                )
            )

        @app.route("/cachelinks/folder", methods=["POST", "DELETE"])
        def cachelinks_folder():
            if request.method == "DELETE":
                path = request.args.get("path", "")
                return jsonify(self.management.cachelinks("folder_delete", path=path))
            payload = request.get_json(silent=True) or {}
            return jsonify(self.management.cachelinks("folder_add", path=payload.get("path", "")))

        @app.route("/cachelinks/<path:canonical_id>", methods=["DELETE"])
        def cachelinks_delete(canonical_id: str):
            return jsonify(self.management.cachelinks("delete", canonical_id=canonical_id))

        @app.route("/cookies", methods=["GET"])
        def cookies_list():
            return jsonify(self.management.cookies("list"))

        @app.route("/cookies/upload", methods=["POST"])
        def cookies_upload():
            domain = request.form.get("domain", "")
            cookie_content = request.form.get("cookie_file", "")
            return jsonify(self.management.cookies("upload", domain=domain, content=cookie_content))

        @app.route("/cookies/domain", methods=["POST"])
        def cookies_domain():
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.cookies(
                    "domain_add",
                    domain=payload.get("domain"),
                    cookie_jar=payload.get("cookie_jar"),
                )
            )

        @app.route("/cookies/refresh", methods=["POST"])
        def cookies_refresh():
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.cookies(
                    "refresh",
                    domain=payload.get("domain"),
                    cookie_jar=payload.get("cookie_jar"),
                )
            )

        @app.route("/users", methods=["GET", "POST"])
        def users_admin():
            if request.method == "GET":
                return jsonify(self.management.users("admin", "list"))
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.users(
                    "admin",
                    "manage",
                    username=payload.get("username"),
                    password=payload.get("password"),
                    enabled=payload.get("enabled", True),
                    admin=payload.get("admin", True),
                )
            )

        @app.route("/users/<username>", methods=["DELETE"])
        def users_admin_delete(username: str):
            return jsonify(self.management.users("admin", "delete", username=username))

        @app.route("/webdav-users", methods=["GET", "POST"])
        def users_webdav():
            if request.method == "GET":
                return jsonify(self.management.users("webdav", "list"))
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.users(
                    "webdav",
                    "manage",
                    share=payload.get("share"),
                    username=payload.get("username"),
                    password=payload.get("password"),
                    enabled=payload.get("enabled", True),
                    login=payload.get("login", True),
                    read=payload.get("read", True),
                    write=payload.get("write", True),
                    cache=payload.get("cache", True),
                )
            )

        @app.route("/webdav-users/<share>/<username>", methods=["DELETE"])
        def users_webdav_delete(share: str, username: str):
            return jsonify(self.management.users("webdav", "delete", share=share, username=username))

        @app.route("/keys", methods=["GET", "POST"])
        def api_keys():
            if request.method == "GET":
                return jsonify(self.management.api_keys("list"))
            payload = request.get_json(silent=True) or {}
            return jsonify(self.management.api_keys("generate", username=payload.get("username")))

        @app.route("/keys/<username>", methods=["DELETE"])
        def api_keys_delete(username: str):
            return jsonify(self.management.api_keys("revoke", username=username))

        @app.route("/settings/detail", methods=["GET", "POST"])
        def settings_detail():
            if request.method == "GET":
                return jsonify(self.management.settings("detail"))
            payload = request.get_json(silent=True) or {}
            return jsonify(self.management.settings("update", payload=payload))

        @app.route("/settings/config", methods=["GET", "POST"])
        def settings_config():
            if request.method == "GET":
                return jsonify(self.management.settings("config_get"))
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.settings(
                    "config_update",
                    settings_text=payload.get("settings_text"),
                    cachelinks_text=payload.get("cachelinks_text"),
                )
            )

        @app.route("/downloads", methods=["GET", "POST"])
        def downloads():
            if request.method == "GET":
                statuses = request.args.get("status")
                status_filters = [s.strip() for s in statuses.split(",") if s.strip()] if statuses else None
                limit = request.args.get("limit")
                limit_val = int(limit) if limit and limit.isdigit() else 50
                return jsonify(self.management.downloads("list", statuses=status_filters, limit=limit_val))
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.downloads(
                    "enqueue",
                    url=payload.get("url", ""),
                    destination=payload.get("destination", ""),
                    expected_checksum=payload.get("expected_checksum"),
                    priority=payload.get("priority", 1),
                )
            )

        @app.route("/downloads/<int:job_id>/retry", methods=["POST"])
        def downloads_retry(job_id: int):
            return jsonify(self.management.downloads("retry", job_id=job_id))

        @app.route("/downloads/<int:job_id>", methods=["DELETE"])
        def downloads_delete(job_id: int):
            return jsonify(self.management.downloads("delete", job_id=job_id))

        @app.route("/degraded", methods=["GET"])
        def degraded():
            return jsonify(self.management.maintenance("degraded"))

        @app.route("/reindex", methods=["POST"])
        def reindex():
            payload = request.get_json(silent=True) or {}
            return jsonify(self.management.maintenance("reindex", canonical_id=payload.get("canonical_id")))

        @app.route("/reload", methods=["POST"])
        def reload():
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.system(
                    "reload",
                    allow_switch=payload.get("allow_switch", False),
                    dump=payload.get("dump", False),
                )
            )

        @app.route("/reinit", methods=["POST"])
        def reinit():
            return jsonify(self.management.system("reinit"))

        @app.route("/ssh-host-keys", methods=["GET"])
        def ssh_host_keys():
            return jsonify(self.management.ssh_host_keys("list"))

        @app.route("/ssh-host-keys/generate", methods=["POST"])
        def ssh_host_key_generate():
            payload = request.get_json(silent=True) or {}
            return jsonify(self.management.ssh_host_keys("generate", key_type=payload.get("key_type")))

        @app.route("/ssh-host-keys/rotate", methods=["POST"])
        def ssh_host_key_rotate():
            return jsonify(self.management.ssh_host_keys("rotate"))

        @app.route("/ssh-host-keys/<key_type>", methods=["DELETE"])
        def ssh_host_key_delete(key_type: str):
            return jsonify(self.management.ssh_host_keys("delete", key_type=key_type))

        @app.route("/rclone/remotes", methods=["GET"])
        def rclone_remotes():
            return jsonify(self.management.rclone("remotes"))
