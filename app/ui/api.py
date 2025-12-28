"""API endpoints for CacheInfinity WebUI."""

from __future__ import annotations

import logging

from flask import Flask, jsonify, request

from .backend import ManagementLayer

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
                return jsonify(self.management.get_system_status())
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
                result = self.management.list_storage_entries(
                    location=location,
                    relative_path=path,
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
                return jsonify({'cachelinks': self.management.describe_cachelinks()})
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
                return jsonify({'shares': self.management.list_shares()})
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
                users = self.management.list_users()
                return jsonify({'users': users})
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
                jobs = self.management.list_download_queue(statuses=status_filters, limit=limit)
                return jsonify({'downloads': jobs})
            except Exception as exc:
                _logger.error(f"Failed to list downloads: {exc}")
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
