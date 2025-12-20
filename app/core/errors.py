"""Error definitions for CacheInfinity."""

from __future__ import annotations


class ConfigError(Exception):
    """Raised when configuration is invalid."""

    message: str

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"Configuration error: {self.message}"


class ServiceError(Exception):
    """Base class for service lifecycle errors."""

    def __init__(self, service_name: str, message: str):
        self.service_name = service_name
        self.message = message
        super().__init__(f"{service_name}: {message}")


class ServiceInitializationError(ServiceError):
    """Raised when a service fails during initialization."""


class ServiceStartError(ServiceError):
    """Raised when a service fails to start."""


class ServiceStopError(ServiceError):
    """Raised when a service fails to stop cleanly."""


class ServiceDependencyError(ServiceError):
    """Raised when service dependencies are invalid or missing."""
