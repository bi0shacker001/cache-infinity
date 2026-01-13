#!/usr/bin/env python3
"""WSGI DispatcherMiddleware implementation for CacheInfinity hosting port."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

try:
    from werkzeug.wsgi import DispatcherMiddleware
except ImportError:
    from werkzeug.middleware.dispatcher import DispatcherMiddleware

_logger = logging.getLogger(__name__)


class HostingDispatcher:
    """WSGI DispatcherMiddleware for hosting port path routing."""

    def __init__(self, service: object | None = None) -> None:
        """Initialize hosting dispatcher."""
        self._service = service
        self._webdav_app = None
        self._api_app = None
        self._dispatcher_app = None
        _logger.debug("Hosting dispatcher initialized")
    
    def set_webdav_app(self, webdav_app: Callable[[Dict[str, Any], Callable], Any]) -> None:
        """Set the WebDAV application.
        
        Args:
            webdav_app: WSGI callable for WebDAV application
        """
        self._webdav_app = webdav_app
        self._update_dispatcher()
    
    def set_api_app(self, api_app: Callable[[Dict[str, Any], Callable], Any]) -> None:
        """Set the read-only admin API application.
        
        Args:
            api_app: WSGI callable for read-only admin API
        """
        self._api_app = api_app
        self._update_dispatcher()
    
    def _update_dispatcher(self) -> None:
        """Update the DispatcherMiddleware with current apps."""
        if self._webdav_app and self._api_app:
            # Create DispatcherMiddleware that routes:
            # - /dav -> WebDAV app
            # - /api -> Read-only admin API
            self._dispatcher_app = DispatcherMiddleware(self._api_app, {
                '/dav': self._webdav_app,
                '/api': self._api_app,
            })
            _logger.info("Hosting dispatcher configured: /dav -> WebDAV, /api -> Read-only Admin API")
        else:
            self._dispatcher_app = None
            _logger.debug("Hosting dispatcher not ready: waiting for both WebDAV and API apps")
    
    def get_wsgi_app(self) -> Callable[[Dict[str, Any], Callable], Any]:
        """Get the WSGI application with DispatcherMiddleware.
        
        Returns:
            WSGI callable that routes requests based on path
        
        Raises:
            RuntimeError: If dispatcher is not ready
        """
        if self._dispatcher_app is None:
            raise RuntimeError("DispatcherMiddleware not ready - both WebDAV and API apps must be set")
        return self._dispatcher_app
    
