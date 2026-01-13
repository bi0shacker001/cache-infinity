"""API endpoints for CacheInfinity WebUI."""

from __future__ import annotations

import logging

from flask import Flask, jsonify, request

from ui.backend import ManagementContext, ManagementLayer

_logger = logging.getLogger(__name__)


def _build_management(*, context: object | None, service: object | None) -> ManagementLayer:
    if context is not None:
        management_context = ManagementContext(
            settings=getattr(context, "settings"),
            index_db=getattr(context, "index_db"),
            auth_manager=getattr(context, "auth_manager"),
            datadir_registry=getattr(context, "datadir_registry"),
            staging=getattr(context, "staging"),
            cachelinks=getattr(context, "cachelinks"),
            fetcher=getattr(context, "fetcher"),
            indexer=None,
            checksum_catalog=None,
        )
        return ManagementLayer(management_context)
    if service is not None:
        return ManagementLayer(service)
    raise ValueError("create_api_app requires context or service")


def create_api_app(*, context: object | None = None, service: object | None = None) -> Flask:
    """Create the read-only admin API for the hosting port."""
    management = _build_management(context=context, service=service)
    app = Flask(__name__)

    def _require_admin_auth():
        auth = request.authorization
        if not auth or not auth.username:
            return jsonify({"error": "Authentication required"}), 401, {
                "WWW-Authenticate": 'Basic realm="CacheInfinity Admin API"'
            }
        if not management.rd_user_admin_validate(auth.username, auth.password or ""):
            return jsonify({"error": "Invalid credentials"}), 401, {
                "WWW-Authenticate": 'Basic realm="CacheInfinity Admin API"'
            }
        return None

    @app.route("/api/status", methods=["GET"])
    def get_status():
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            return jsonify(management.system("status"))
        except Exception as exc:
            _logger.error("Failed to get status: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/storage/files", methods=["GET"])
    def list_files():
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            location = request.args.get("location", "datadir")
            path = request.args.get("path", "/")
            sort_by = request.args.get("sort_by")
            sort_order = request.args.get("sort_order")
            view_mode = request.args.get("view_mode")
            show_hidden = request.args.get("show_hidden", "false").lower() == "true"
            search_query = request.args.get("search_query", "")
            result = management.storage(
                "list",
                location=location,
                path=path,
                sort_by=sort_by,
                sort_order=sort_order,
                view_mode=view_mode,
                show_hidden=show_hidden,
                search_query=search_query,
            )
            return jsonify(result)
        except Exception as exc:
            _logger.error("Failed to list files: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/storage/upload", methods=["POST"])
    def upload_file():
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            return jsonify({"error": "Admin API is read-only"}), 405
        except Exception as exc:
            _logger.error("Failed to upload file: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/storage/folder", methods=["POST"])
    def create_folder():
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            return jsonify({"error": "Admin API is read-only"}), 405
        except Exception as exc:
            _logger.error("Failed to create folder: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/storage/entries", methods=["DELETE"])
    def delete_entry():
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            return jsonify({"error": "Admin API is read-only"}), 405
        except Exception as exc:
            _logger.error("Failed to delete entry: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/cachelinks", methods=["GET"])
    def list_cachelinks():
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            return jsonify(management.cachelinks("list"))
        except Exception as exc:
            _logger.error("Failed to list cachelinks: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/shares", methods=["GET"])
    def list_shares():
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            return jsonify(management.shares("list"))
        except Exception as exc:
            _logger.error("Failed to list shares: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/users", methods=["GET"])
    def list_users():
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            return management.users("admin", "list")
        except Exception as exc:
            _logger.error("Failed to list users: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/downloads", methods=["GET"])
    def list_download_queue():
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            statuses = request.args.get("status")
            status_filters = None
            if statuses:
                status_filters = [status.strip() for status in statuses.split(",") if status.strip()]
            limit_param = request.args.get("limit")
            try:
                limit = int(limit_param) if limit_param else 50
            except ValueError:
                limit = 50
            return management.downloads("list", statuses=status_filters, limit=limit)
        except Exception as exc:
            _logger.error("Failed to list downloads: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/rclone/remotes", methods=["GET"])
    def rclone_remotes():
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            return jsonify(management.rclone("remotes"))
        except Exception as exc:
            _logger.error("Failed to list rclone remotes: %s", exc)
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/downloads", methods=["POST"])
    def enqueue_download():
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            return jsonify({"error": "Admin API is read-only"}), 405
        except ValueError as exc:
            _logger.error("Invalid download request: %s", exc)
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            _logger.error("Failed to queue download: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/downloads/<int:job_id>/retry", methods=["POST"])
    def retry_download(job_id: int):
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            return jsonify({"error": "Admin API is read-only"}), 405
        except Exception as exc:  # pragma: no cover - defensive
            _logger.error("Failed to retry download %s: %s", job_id, exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/downloads/<int:job_id>", methods=["DELETE"])
    def delete_download(job_id: int):
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            return jsonify({"error": "Admin API is read-only"}), 405
        except Exception as exc:  # pragma: no cover - defensive
            _logger.error("Failed to delete download %s: %s", job_id, exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/users", methods=["POST"])
    def create_user():
        try:
            auth_error = _require_admin_auth()
            if auth_error:
                return auth_error
            return jsonify({"error": "Admin API is read-only"}), 405
        except Exception as exc:
            _logger.error("Failed to create user: %s", exc)
            return jsonify({"error": str(exc)}), 500

    return app
