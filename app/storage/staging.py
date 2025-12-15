"""Staging area management."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dataclasses import dataclass
from pathlib import Path


@dataclass
class StagingDefinition:
    """Definition of a staging area."""

    staging_mounted: bool = False
    staging_mount_root: Optional[Path] = None
    size_gb: int = 50


@dataclass
class StagingArea:
    """Represents the staging filesystem used for downloads."""

    definition: StagingDefinition

    def ensure_ready(self) -> None:
        base = self.base_path
        base.mkdir(parents=True, exist_ok=True)

    @property
    def base_path(self) -> Path:
        if self.definition.staging_mount_root:
            return self.definition.staging_mount_root
        return Path(tempfile.gettempdir()) / "cacheinfinity-staging"

    def reserve_tempfile(self, prefix: str) -> Path:
        """Create a unique staging file path without touching disk."""

        fd, path = tempfile.mkstemp(prefix=f"ci-{prefix}-", dir=self.base_path)
        os.close(fd)
        staged = Path(path)
        staged.chmod(0o600)
        return staged


__all__ = ["StagingArea"]
