"""Flask-powered WebUI application core."""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, stream_with_context

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
            if (
                request.method == "GET"
                and request.accept_mimetypes["text/html"] >= request.accept_mimetypes["application/json"]
            ):
                return redirect("/login")
            return jsonify({"error": "Authentication required"}), 401
        return None

    def _configure_routes(self) -> None:
        app = self.app

        @app.context_processor
        def _theme_context():
            return {"theme": self._resolve_theme()}

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

        @app.route("/overview", methods=["GET"])
        def overview():
            return render_template("overview.html")

        @app.route("/storage", methods=["GET"])
        def storage_page():
            if request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]:
                return jsonify(self.management.system("storage"))
            return render_template("storage.html")

        @app.route("/cachelinks", methods=["GET", "POST"])
        def cachelinks_create():
            if request.method == "GET":
                if request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]:
                    return jsonify(self.management.cachelinks("list"))
                return render_template("cachelinks.html")
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.cachelinks(
                    "create",
                    parent_path=payload.get("parent_path"),
                    name=payload.get("name"),
                    url=payload.get("url"),
                    subfolder=payload.get("subfolder", "/"),
                    url_handler=payload.get("url_handler"),
                    rclone_remote=payload.get("rclone_remote"),
                    rclone_path=payload.get("rclone_path"),
                    bandwidth_limit=payload.get("bandwidth_limit"),
                    transfer_concurrency=payload.get("transfer_concurrency"),
                    checkers=payload.get("checkers"),
                    timeout=payload.get("timeout"),
                    retries=payload.get("retries"),
                )
            )

        @app.route("/settings", methods=["GET"])
        def settings_page():
            return render_template("settings.html")

        @app.route("/users", methods=["GET", "POST"])
        def users_admin():
            if request.method == "GET":
                if request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]:
                    return jsonify(self.management.users("admin", "list"))
                return render_template("users.html")
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

        @app.route("/cookies", methods=["GET"])
        def cookies_page():
            if request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]:
                return jsonify(self.management.cookies("list"))
            return render_template("cookies.html")

        @app.route("/maintenance", methods=["GET"])
        def maintenance_page():
            return render_template("maintenance.html")

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

        @app.route("/events/overview", methods=["GET"])
        def overview_events():
            return self._stream_overview_events()

        @app.route("/shares", methods=["GET"])
        def shares():
            return jsonify(self.management.shares("list"))

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
                    rclone_remote=payload.get("rclone_remote"),
                    rclone_path=payload.get("rclone_path"),
                    bandwidth_limit=payload.get("bandwidth_limit"),
                    transfer_concurrency=payload.get("transfer_concurrency"),
                    checkers=payload.get("checkers"),
                    timeout=payload.get("timeout"),
                    retries=payload.get("retries"),
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

        @app.route("/settings/ssh-keys/users", methods=["GET"])
        def settings_ssh_users():
            return jsonify(self.management.ssh_user_keys("list"))

        @app.route("/settings/ssh-keys/<username>", methods=["GET", "POST"])
        def settings_ssh_user_keys(username: str):
            if request.method == "GET":
                return jsonify(self.management.ssh_user_keys("get", username=username))
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.ssh_user_keys(
                    "update",
                    username=username,
                    authorized_keys=payload.get("authorized_keys", ""),
                )
            )

        @app.route("/settings/ssh-keys/<username>/editable", methods=["POST"])
        def settings_ssh_user_editable(username: str):
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.ssh_user_keys(
                    "set_editable",
                    username=username,
                    enabled=bool(payload.get("enabled", True)),
                )
            )

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

        @app.route("/shutdown", methods=["POST"])
        def shutdown():
            return jsonify(self.management.system("shutdown"))

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

        @app.route("/rclone/test", methods=["POST"])
        def rclone_test():
            payload = request.get_json(silent=True) or {}
            return jsonify(
                self.management.rclone(
                    "test",
                    remote=payload.get("remote"),
                    path=payload.get("path"),
                )
            )

    def _resolve_theme(self) -> str | None:
        notheme = request.args.get("notheme", "").lower()
        if notheme in ("1", "true", "yes", "on"):
            return None
        theme = getattr(self.management.ctx.settings, "ui", None)
        value = theme.theme if theme else "lavender"
        return value or "lavender"

    def _overview_payload(self) -> dict:
        status = self.management.system("status")
        downloads = self.management.downloads("list", statuses=None, limit=8)
        return {
            "status": status,
            "downloads": downloads.get("downloads", []),
        }

    @staticmethod
    def _format_sse(payload: dict, *, event: str | None = None) -> str:
        line = f"data: {json.dumps(payload, separators=(',', ':'))}"
        if event:
            line = f"event: {event}\n{line}"
        return f"{line}\n\n"

    def _stream_overview_events(self) -> Response:
        interval = request.args.get("interval", "4")
        try:
            interval_value = max(1.0, min(float(interval), 60.0))
        except ValueError:
            interval_value = 4.0

        def _generate():
            try:
                while True:
                    try:
                        payload = self._overview_payload()
                    except Exception as exc:
                        payload = {"error": str(exc), "status": None, "downloads": []}
                    yield self._format_sse(payload)
                    time.sleep(interval_value)
            except GeneratorExit:
                return

        return Response(
            stream_with_context(_generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
