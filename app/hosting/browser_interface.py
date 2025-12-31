"""User browser interface components for CacheInfinity."""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


class BrowserInterface:
    """Provides user-facing browser interface functionality.
    
    This class handles the user-facing aspects of the CacheInfinity service,
    providing a clean interface for browser-based interactions.
    """
    
    def __init__(self):
        """Initialize browser interface."""
        _logger.info("Browser interface initialized")
        
    def get_user_friendly_status(self) -> dict:
        """Get user-friendly status information.
        
        Returns:
            Dictionary with user-friendly status information
        """
        return {
            'service_name': 'CacheInfinity',
            'status': 'running',
            'features': [
                'WebDAV file access',
                'On-demand caching',
                'Remote content indexing',
                'User authentication'
            ]
        }
        
    def get_help_information(self) -> dict:
        """Get help information for users.
        
        Returns:
            Dictionary with help information
        """
        return {
            'getting_started': [
                'Connect to the WebDAV service using your client',
                'Browse available shares and folders',
                'Access remote content - it will be cached automatically',
                'Upload and manage your files'
            ],
            'tips': [
                'Files are cached on-demand for optimal performance',
                'Remote content appears immediately in the folder tree',
                'Use the admin interface for configuration and monitoring'
            ]
        }