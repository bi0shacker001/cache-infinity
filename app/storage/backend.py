"""Backend storage helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Optional

from ..core.errors import ConfigError


@dataclass
class BackendDefinition:
    """Definition of a backend cache root."""

    name: str
    backend_mounted: bool
    backend_cache_root: Path
    backend_mount_root: Optional[Path] = None

    def validate(self) -> None:
        if self.backend_mounted and not self.backend_mount_root:
            raise ConfigError(
                f"Backend '{self.name}' is marked mounted but missing backend_mount_root"
            )


@dataclass
class BackendStorage:
    """Represents a mounted backend cache root."""

    definition: BackendDefinition

    def ensure_ready(self) -> None:
        if not self.definition.backend_cache_root.exists():
            raise FileNotFoundError(
                f"Backend cache root missing: {self.definition.backend_cache_root}"
            )
        if self.definition.backend_mounted:
            mount_root = self.definition.backend_mount_root
            if not mount_root or not mount_root.exists():
                raise FileNotFoundError(
                    f"Backend mount root missing: {self.definition.backend_mount_root}"
                )

    def resolve(self, relative_path: PurePosixPath | str) -> Path:
        """Resolve a resource relative to the backend cache root."""

        segments = _normalize_relative(relative_path)
        if not segments:
            return self.definition.backend_cache_root
        return self.definition.backend_cache_root.joinpath(*segments)

    def exists(self, relative_path: PurePosixPath | str) -> bool:
        return self.resolve(relative_path).exists()

    def open_read(self, relative_path: PurePosixPath | str, *, binary: bool = True) -> BinaryIO:
        mode = "rb" if binary else "r"
        return self.resolve(relative_path).open(mode)

    def open_write(self, relative_path: PurePosixPath | str, *, binary: bool = True) -> BinaryIO:
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "wb" if binary else "w"
        return path.open(mode)


@dataclass
class BackendRegistry:
    """Registry for all configured backends."""

    storages: dict[str, BackendStorage]
    primary_name: str

    @classmethod
    def from_settings(cls, backends: dict[str, BackendDefinition], primary: str) -> "BackendRegistry":
        storages = {name: BackendStorage(defn) for name, defn in backends.items()}
        return cls(storages=storages, primary_name=primary)

    @property
    def primary(self) -> BackendStorage:
        return self.storages[self.primary_name]


def _normalize_relative(value: PurePosixPath | str) -> tuple[str, ...]:
    posix = value if isinstance(value, PurePosixPath) else PurePosixPath(str(value))
    parts = list(posix.parts)
    if posix.is_absolute() and parts:
        parts = parts[1:]
    filtered = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("Relative paths may not traverse upward")
        filtered.append(part)
    return tuple(filtered)