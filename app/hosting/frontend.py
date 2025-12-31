"""Interface adapter for frontend user interactions.

This module provides a uniform interface for all frontend actions, serving as a
common base that both webdav.py and browser_interface.py can implement.
Similar to ui.backend for admin UIs, this provides the internal call functionality
for frontend operations.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Protocol

_logger = logging.getLogger(__name__)


class FrontendInterface(Protocol):
    """Protocol defining the common interface for all frontend implementations.
    
    This protocol ensures that all frontend implementations (WebDAV, browser interface, etc.)
    provide a consistent set of methods for user-facing operations.
    """
    
    def get_user_friendly_status(self) -> Dict[str, Any]:
        """Get user-friendly status information.
        
        Returns:
            Dictionary with user-friendly status information
        """
        ...
    
    def get_help_information(self) -> Dict[str, Any]:
        """Get help information for users.
        
        Returns:
            Dictionary with help information
        """
        ...
    
    def get_service_capabilities(self) -> List[str]:
        """Get list of supported features and capabilities.
        
        Returns:
            List of feature descriptions
        """
        ...
    
    def validate_user_access(self, username: str, path: str) -> bool:
        """Validate if a user has access to a specific path.
        
        Args:
            username: Username to validate
            path: Path to check access for
            
        Returns:
            True if user has access, False otherwise
        """
        ...
    
    def get_path_info(self, path: str) -> Dict[str, Any]:
        """Get information about a specific path.
        
        Args:
            path: Path to get information for
            
        Returns:
            Dictionary with path information including type, size, permissions
        """
        ...
    
    def list_directory_contents(self, path: str) -> List[Dict[str, Any]]:
        """List contents of a directory.
        
        Args:
            path: Directory path to list
            
        Returns:
            List of directory entries with metadata
        """
        ...
    
    def get_cache_state(self, path: str) -> str:
        """Get cache state for a specific path.
        
        Args:
            path: Path to check cache state for
            
        Returns:
            Cache state: 'remote', 'cached', 'staging', or 'local-only'
        """
        ...


class FrontendService(ABC):
    """Abstract base class for frontend service implementations.
    
    This class provides the common interface that all frontend services should implement.
    It serves as the internal call functionality layer for frontend operations.
    """
    
    def __init__(self, service):
        """Initialize frontend service.
        
        Args:
            service: Reference to the main CacheInfinity service
        """
        self.service = service
        _logger.debug("Frontend service initialized")
    
    @abstractmethod
    def get_user_friendly_status(self) -> Dict[str, Any]:
        """Get user-friendly status information.
        
        Returns:
            Dictionary with user-friendly status information
        """
    
    @abstractmethod
    def get_help_information(self) -> Dict[str, Any]:
        """Get help information for users.
        
        Returns:
            Dictionary with help information
        """
    
    @abstractmethod
    def get_service_capabilities(self) -> List[str]:
        """Get list of supported features and capabilities.
        
        Returns:
            List of feature descriptions
        """
    
    def validate_user_access(self, username: str, path: str) -> bool:
        """Validate if a user has access to a specific path.
        
        Args:
            username: Username to validate
            path: Path to check access for
            
        Returns:
            True if user has access, False otherwise
        """
        try:
            # Delegate to service for actual validation logic
            return self.service.validate_user_access(username, path)
        except Exception as e:
            _logger.error("Failed to validate user access for %s at %s: %s", username, path, e)
            return False
    
    def get_path_info(self, path: str) -> Dict[str, Any]:
        """Get information about a specific path.
        
        Args:
            path: Path to get information for
            
        Returns:
            Dictionary with path information including type, size, permissions
        """
        try:
            # Delegate to service for actual path info logic
            return self.service.get_path_info(path)
        except Exception as e:
            _logger.error("Failed to get path info for %s: %s", path, e)
            return {}
    
    def list_directory_contents(self, path: str) -> List[Dict[str, Any]]:
        """List contents of a directory.
        
        Args:
            path: Directory path to list
            
        Returns:
            List of directory entries with metadata
        """
        try:
            # Delegate to service for actual directory listing logic
            return self.service.list_directory_contents(path)
        except Exception as e:
            _logger.error("Failed to list directory contents for %s: %s", path, e)
            return []
    
    def get_cache_state(self, path: str) -> str:
        """Get cache state for a specific path.
        
        Args:
            path: Path to check cache state for
            
        Returns:
            Cache state: 'remote', 'cached', 'staging', or 'local-only'
        """
        try:
            # Delegate to service for actual cache state logic
            return self.service.get_cache_state(path)
        except Exception as e:
            _logger.error("Failed to get cache state for %s: %s", path, e)
            return "unknown"


class FrontendManager:
    """Manager for frontend services and operations.
    
    This class provides a centralized way to manage different frontend implementations
    and route requests to the appropriate service.
    """
    
    def __init__(self, service):
        """Initialize frontend manager.
        
        Args:
            service: Reference to the main CacheInfinity service
        """
        self.service = service
        self._frontends: Dict[str, FrontendService] = {}
        _logger.debug("Frontend manager initialized")
    
    def register_frontend(self, name: str, frontend: FrontendService) -> None:
        """Register a frontend service.
        
        Args:
            name: Name of the frontend service
            frontend: Frontend service instance
        """
        self._frontends[name] = frontend
        _logger.debug("Registered frontend service: %s", name)
    
    def get_frontend(self, name: str) -> Optional[FrontendService]:
        """Get a registered frontend service.
        
        Args:
            name: Name of the frontend service
            
        Returns:
            Frontend service instance or None if not found
        """
        return self._frontends.get(name)
    
    def get_all_frontends(self) -> Dict[str, FrontendService]:
        """Get all registered frontend services.
        
        Returns:
            Dictionary of all frontend services
        """
        return self._frontends.copy()
    
    def get_status_summary(self) -> Dict[str, Any]:
        """Get a summary of all frontend services status.
        
        Returns:
            Dictionary with status information for all frontends
        """
        summary = {
            'frontends': {},
            'capabilities': [],
            'help': {}
        }
        
        for name, frontend in self._frontends.items():
            try:
                status = frontend.get_user_friendly_status()
                summary['frontends'][name] = status
                
                capabilities = frontend.get_service_capabilities()
                summary['capabilities'].extend(capabilities)
                
                help_info = frontend.get_help_information()
                summary['help'][name] = help_info
            except Exception as e:
                _logger.error("Failed to get status for frontend %s: %s", name, e)
                summary['frontends'][name] = {'status': 'error', 'error': str(e)}
        
        return summary
    
    def validate_path_access(self, username: str, path: str) -> bool:
        """Validate if a user has access to a path across all frontends.
        
        Args:
            username: Username to validate
            path: Path to check access for
            
        Returns:
            True if user has access through any frontend, False otherwise
        """
        for frontend in self._frontends.values():
            try:
                if frontend.validate_user_access(username, path):
                    return True
            except Exception as e:
                _logger.error("Failed to validate access for %s at %s: %s", username, path, e)
        
        return False
    
    def get_unified_path_info(self, path: str) -> Dict[str, Any]:
        """Get unified path information from all frontends.
        
        Args:
            path: Path to get information for
            
        Returns:
            Unified dictionary with path information
        """
        unified_info = {}
        
        for name, frontend in self._frontends.items():
            try:
                info = frontend.get_path_info(path)
                if info:
                    unified_info[name] = info
            except Exception as e:
                _logger.error("Failed to get path info from frontend %s for %s: %s", name, path, e)
        
        return unified_info