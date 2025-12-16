"""Management utilities for CacheInfinity WebUI."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

_logger = logging.getLogger(__name__)


class WebUIManager:
    """Manages WebUI functionality."""
    
    def __init__(self, service):
        """Initialize WebUI manager.
        
        Args:
            service: Reference to the main CacheInfinity service
        """
        self.service = service
        _logger.info("WebUI manager initialized")
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get dashboard data for WebUI.
        
        Returns:
            Dictionary with dashboard information
        """
        try:
            # Get system status
            status = {
                'service_status': 'running',
                'version': '1.0.0',
                'uptime': '00:00:00'
            }
            
            # Get storage information
            storage_info = {
                'primary_path': str(self.service.storage_registry.primary.definition.backend_cache_root),
                'staging_path': str(self.service.staging.definition.staging_mount_root),
                'primary_ready': True,
                'staging_ready': True
            }
            
            # Get database status
            db_status = {
                'healthy': self.service.index_db.health_check(),
                'stats': self.service.index_db.get_pool_stats()
            }
            
            # Get user information
            users = self.service.list_admin_users()
            
            return {
                'status': status,
                'storage': storage_info,
                'database': db_status,
                'users': users
            }
        except Exception as exc:
            _logger.error(f"Failed to get dashboard data: {exc}")
            return {'error': str(exc)}
    
    def get_file_browser_data(self, location: str, path: str) -> Dict[str, Any]:
        """Get file browser data.
        
        Args:
            location: Storage location
            path: Path to browse
            
        Returns:
            Dictionary with file browser data
        """
        try:
            # This would implement actual file browsing
            files = [
                {'name': 'test.txt', 'is_dir': False, 'size': 1024, 'modified': '2024-01-01'},
                {'name': 'test_dir', 'is_dir': True, 'size': 0, 'modified': '2024-01-01'}
            ]
            
            return {
                'location': location,
                'path': path,
                'files': files,
                'parent_path': str(Path(path).parent) if path != '/' else '/'
            }
        except Exception as exc:
            _logger.error(f"Failed to get file browser data: {exc}")
            return {'error': str(exc)}
    
    def get_cachelink_data(self) -> Dict[str, Any]:
        """Get cachelink management data.
        
        Returns:
            Dictionary with cachelink information
        """
        try:
            # This would implement actual cachelink management
            cachelinks = [
                {
                    'canonical_id': 'games/psx/map0001',
                    'url': 'https://example.com',
                    'subfolder': '/',
                    'mode': 'plain'
                }
            ]
            
            return {
                'cachelinks': cachelinks,
                'total': len(cachelinks)
            }
        except Exception as exc:
            _logger.error(f"Failed to get cachelink data: {exc}")
            return {'error': str(exc)}
    
    def get_user_management_data(self) -> Dict[str, Any]:
        """Get user management data.
        
        Returns:
            Dictionary with user information
        """
        try:
            users = self.service.list_admin_users()
            
            return {
                'users': users,
                'total': len(users)
            }
        except Exception as exc:
            _logger.error(f"Failed to get user management data: {exc}")
            return {'error': str(exc)}