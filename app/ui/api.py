"""API endpoints for CacheInfinity WebUI."""

from __future__ import annotations

import logging

from flask import Flask, jsonify, request

from ui.backend import ManagementLayer

_logger = logging.getLogger(__name__)


class WebUIAPI:
    """WebUI API endpoints."""
    
    def __init__(self, service):
        """Initialize WebUI API.
        
        Args:
            service: Reference to the main CacheInfinity service
        """
        self.service = service
        self.management = ManagementLayer(service)
        _logger.debug("WebUI API initialized")
    
    def register_routes(self, app: Flask) -> None:
        """Register API routes with Flask app.
        
        Args:
            app: Flask application instance
        """
        def _require_admin_auth():
            auth = request.authorization
            if not auth or not auth.username:
                return jsonify({"error": "Authentication required"}), 401, {
                    "WWW-Authenticate": 'Basic realm="CacheInfinity Admin API"'
                }
            if not self.management.rd_user_admin_validate(auth.username, auth.password or ""):
                return jsonify({"error": "Invalid credentials"}), 401, {
                    "WWW-Authenticate": 'Basic realm="CacheInfinity Admin API"'
                }
            return None

        @app.route('/api/status', methods=['GET'])
        def get_status():
            """Get system status."""
            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error
                return jsonify(self.management.system("status"))
            except Exception as exc:
                _logger.error(f"Failed to get status: {exc}")
                return jsonify({'error': str(exc)}), 500
        
        @app.route('/api/storage/files', methods=['GET'])
        def list_files():
            """List files in storage."""
            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error
                location = request.args.get('location', 'datadir')
                path = request.args.get('path', '/')
                sort_by = request.args.get('sort_by')
                sort_order = request.args.get('sort_order')
                view_mode = request.args.get('view_mode')
                show_hidden = request.args.get('show_hidden', 'false').lower() == 'true'
                search_query = request.args.get('search_query', '')
                result = self.management.storage(
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
                _logger.error(f"Failed to list files: {exc}")
                return jsonify({'error': str(exc)}), 500
        
        @app.route('/api/storage/upload', methods=['POST'])
        def upload_file():
            """Upload a file (read-only API)."""
            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error
                return jsonify({'error': 'Admin API is read-only'}), 405
            except Exception as exc:
                _logger.error(f"Failed to upload file: {exc}")
                return jsonify({'error': str(exc)}), 500
        
        @app.route('/api/storage/folder', methods=['POST'])
        def create_folder():
            """Create a folder (read-only API)."""
            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error
                return jsonify({'error': 'Admin API is read-only'}), 405
            except Exception as exc:
                _logger.error(f"Failed to create folder: {exc}")
                return jsonify({'error': str(exc)}), 500
        
        @app.route('/api/storage/entries', methods=['DELETE'])
        def delete_entry():
            """Delete a file or folder (read-only API)."""
            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error
                return jsonify({'error': 'Admin API is read-only'}), 405
            except Exception as exc:
                _logger.error(f"Failed to delete entry: {exc}")
                return jsonify({'error': str(exc)}), 500
        
        @app.route('/api/cachelinks', methods=['GET'])
        def list_cachelinks():
            """List all cachelinks."""
            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error
                return jsonify(self.management.cachelinks("list"))
            except Exception as exc:
                _logger.error(f"Failed to list cachelinks: {exc}")
                return jsonify({'error': str(exc)}), 500

        @app.route('/api/shares', methods=['GET'])
        def list_shares():
            """List configured WebDAV shares and user policies."""
            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error
                return jsonify(self.management.shares("list"))
            except Exception as exc:
                _logger.error(f"Failed to list shares: {exc}")
                return jsonify({'error': str(exc)}), 500

        @app.route('/api/users', methods=['GET'])
        def list_users():
            """List all users."""
            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error
                return self.management.users("admin", "list")
            except Exception as exc:
                _logger.error(f"Failed to list users: {exc}")
                return jsonify({'error': str(exc)}), 500

        @app.route('/api/downloads', methods=['GET'])
        def list_download_queue():
            """Inspect queued and in-progress downloads."""
            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error
                statuses = request.args.get('status')
                status_filters = None
                if statuses:
                    status_filters = [status.strip() for status in statuses.split(',') if status.strip()]
                limit_param = request.args.get('limit')
                try:
                    limit = int(limit_param) if limit_param else 50
                except ValueError:
                    limit = 50
                return self.management.downloads("list", statuses=status_filters, limit=limit)
            except Exception as exc:
                _logger.error(f"Failed to list downloads: {exc}")
                return jsonify({'error': str(exc)}), 500

        @app.route('/api/rclone/remotes', methods=['GET'])
        def rclone_remotes():
            """Expose configured rclone remotes via rclone rc."""

            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error
                return jsonify(self.management.rclone("remotes"))
            except Exception as exc:
                _logger.error("Failed to list rclone remotes: %s", exc)
                return jsonify({'error': str(exc)}), 400

        @app.route('/api/downloads', methods=['POST'])
        def enqueue_download():
            """Queue a new download request."""

            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error

                payload = request.get_json(force=True, silent=True) or {}
                url = (payload.get('url') or '').strip()
                destination = (payload.get('destination') or '').strip()
                checksum = (payload.get('expected_checksum') or '').strip() or None
                try:
                    priority = int(payload.get('priority') or 1)
                except (TypeError, ValueError):
                    priority = 1

                if not url or not destination:
                    return jsonify({'error': 'url and destination are required'}), 400

                return self.management.downloads(
                    "enqueue",
                    url=url,
                    destination=destination,
                    expected_checksum=checksum,
                    priority=priority,
                )
            except ValueError as exc:
                _logger.error(f"Invalid download request: {exc}")
                return jsonify({'error': str(exc)}), 400
            except Exception as exc:
                _logger.error(f"Failed to queue download: {exc}")
                return jsonify({'error': str(exc)}), 500

        @app.route('/api/downloads/<int:job_id>/retry', methods=['POST'])
        def retry_download(job_id: int):
            """Reset a failed download to pending."""

            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error
                return self.management.downloads("retry", job_id=job_id)
            except Exception as exc:  # pragma: no cover - defensive
                _logger.error("Failed to retry download %s: %s", job_id, exc)
                return jsonify({'error': str(exc)}), 500

        @app.route('/api/downloads/<int:job_id>', methods=['DELETE'])
        def delete_download(job_id: int):
            """Remove a queued download."""

            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error
                return self.management.downloads("delete", job_id=job_id)
            except Exception as exc:  # pragma: no cover - defensive
                _logger.error("Failed to delete download %s: %s", job_id, exc)
                return jsonify({'error': str(exc)}), 500
        
        @app.route('/api/users', methods=['POST'])
        def create_user():
            """Create or update a user (read-only API)."""
            try:
                auth_error = _require_admin_auth()
                if auth_error:
                    return auth_error
                return jsonify({'error': 'Admin API is read-only'}), 405
            except Exception as exc:
                _logger.error(f"Failed to create user: {exc}")
                return jsonify({'error': str(exc)}), 500


def create_api_app(service) -> Flask:
    """Create and configure the read-only admin API Flask application.
    
    Args:
        service: CacheInfinity service instance
        
    Returns:
        Configured Flask application with all API routes registered
    """
    app = Flask(__name__)
    api = WebUIAPI(service)
    api.register_routes(app)
    return app
