"""API endpoints for CacheInfinity WebUI."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, request

_logger = logging.getLogger(__name__)


class WebUIAPI:
    """WebUI API endpoints."""
    
    def __init__(self, service):
        """Initialize WebUI API.
        
        Args:
            service: Reference to the main CacheInfinity service
        """
        self.service = service
        _logger.info("WebUI API initialized")
    
    def register_routes(self, app: Flask) -> None:
        """Register API routes with Flask app.
        
        Args:
            app: Flask application instance
        """
        @app.route('/api/status', methods=['GET'])
        def get_status():
            """Get system status."""
            try:
                status = {
                    'status': 'running',
                    'version': '1.0.0',
                    'uptime': '00:00:00',
                    'storage': {
                        'primary': str(self.service.storage_registry.primary.definition.backend_cache_root),
                        'staging': str(self.service.staging.definition.staging_mount_root)
                    }
                }
                return jsonify(status)
            except Exception as exc:
                _logger.error(f"Failed to get status: {exc}")
                return jsonify({'error': str(exc)}), 500
        
        @app.route('/api/storage/files', methods=['GET'])
        def list_files():
            """List files in storage."""
            try:
                location = request.args.get('location', 'backend_1')
                path = request.args.get('path', '/')
                
                # This would implement actual file listing
                files = [
                    {'name': 'test.txt', 'is_dir': False, 'size': 1024, 'modified': '2024-01-01'},
                    {'name': 'test_dir', 'is_dir': True, 'size': 0, 'modified': '2024-01-01'}
                ]
                
                return jsonify({'files': files, 'location': location, 'path': path})
            except Exception as exc:
                _logger.error(f"Failed to list files: {exc}")
                return jsonify({'error': str(exc)}), 500
        
        @app.route('/api/storage/upload', methods=['POST'])
        def upload_file():
            """Upload a file."""
            try:
                # This would implement actual file upload
                return jsonify({'message': 'Upload successful'})
            except Exception as exc:
                _logger.error(f"Failed to upload file: {exc}")
                return jsonify({'error': str(exc)}), 500
        
        @app.route('/api/storage/folder', methods=['POST'])
        def create_folder():
            """Create a folder."""
            try:
                # This would implement actual folder creation
                return jsonify({'message': 'Folder created'})
            except Exception as exc:
                _logger.error(f"Failed to create folder: {exc}")
                return jsonify({'error': str(exc)}), 500
        
        @app.route('/api/storage/entries', methods=['DELETE'])
        def delete_entry():
            """Delete a file or folder."""
            try:
                # This would implement actual deletion
                return jsonify({'message': 'Entry deleted'})
            except Exception as exc:
                _logger.error(f"Failed to delete entry: {exc}")
                return jsonify({'error': str(exc)}), 500
        
        @app.route('/api/cachelinks', methods=['GET'])
        def list_cachelinks():
            """List all cachelinks."""
            try:
                # This would implement actual cachelink listing
                cachelinks = [
                    {
                        'canonical_id': 'games/psx/map0001',
                        'url': 'https://example.com',
                        'subfolder': '/',
                        'mode': 'plain'
                    }
                ]
                return jsonify({'cachelinks': cachelinks})
            except Exception as exc:
                _logger.error(f"Failed to list cachelinks: {exc}")
                return jsonify({'error': str(exc)}), 500
        
        @app.route('/api/users', methods=['GET'])
        def list_users():
            """List all users."""
            try:
                users = self.service.list_admin_users()
                return jsonify({'users': users})
            except Exception as exc:
                _logger.error(f"Failed to list users: {exc}")
                return jsonify({'error': str(exc)}), 500
        
        @app.route('/api/users', methods=['POST'])
        def create_user():
            """Create or update a user."""
            try:
                data = request.get_json()
                username = data.get('username')
                password = data.get('password')
                enabled = data.get('enabled', True)
                is_admin = data.get('is_admin', False)
                
                self.service.upsert_admin_user(
                    username=username,
                    password=password,
                    enabled=enabled,
                    is_admin=is_admin
                )
                
                return jsonify({'message': f'User {username} updated successfully'})
            except Exception as exc:
                _logger.error(f"Failed to create user: {exc}")
                return jsonify({'error': str(exc)}), 500