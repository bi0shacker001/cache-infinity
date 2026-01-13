"""Cachelink management for CacheInfinity."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
import re
from urllib.parse import urlparse
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

_logger = logging.getLogger(__name__)


@dataclass
class CachelinkDescriptor:
    """Descriptor for a cachelink."""
    
    canonical_id: str
    path_segments: tuple[str, ...]
    source_file: Path
    source_url: str
    identifier: str
    download_root: str
    subfolder: str
    mode: "CachelinkMode"
    url_handler: str
    rclone_remote: str | None = None
    rclone_path: str | None = None
    bandwidth_limit: str | None = None
    transfer_concurrency: int | None = None
    checkers: int | None = None
    timeout: int | None = None
    retries: int | None = None

    @property
    def backend_relative_folder(self) -> PurePosixPath:
        if len(self.path_segments) <= 1:
            return PurePosixPath("")
        return PurePosixPath("/".join(self.path_segments[:-1]))

    @property
    def remote_listing_url(self) -> str:
        subfolder = self.subfolder.lstrip("/")
        if not subfolder:
            return self.download_root
        if self.download_root.endswith("/"):
            return self.download_root + subfolder
        return f"{self.download_root}/{subfolder}"

    @property
    def id(self) -> str:
        return self.canonical_id


@dataclass
class CachelinkRecord:
    """Record for a cachelink."""
    
    folder_segments: tuple[str, ...]
    url: str
    subfolder: str


@dataclass
class CachelinkIndex:
    """Index of cachelinks."""
    
    cachelinks: Dict[str, CachelinkDescriptor]


class CachelinkManager:
    """Lookup helper for cachelink descriptors by path."""

    def __init__(self, index: CachelinkIndex):
        self._index = index

    def get_cachelinks_for_path(self, path: str) -> list[CachelinkDescriptor]:
        segments = _path_segments(path)
        matches = []
        for descriptor in self._index.cachelinks.values():
            if _segments_match(segments, descriptor.path_segments):
                matches.append(descriptor)
        return matches

    def get_cachelink_for_path(self, path: str) -> CachelinkDescriptor | None:
        segments = _path_segments(path)
        best: CachelinkDescriptor | None = None
        best_len = -1
        for descriptor in self._index.cachelinks.values():
            if _segments_match(segments, descriptor.path_segments):
                if len(descriptor.path_segments) > best_len:
                    best_len = len(descriptor.path_segments)
                    best = descriptor
        return best


def _path_segments(value: str) -> tuple[str, ...]:
    posix = PurePosixPath(value)
    parts = list(posix.parts)
    if posix.is_absolute() and parts:
        parts = parts[1:]
    return tuple(part for part in parts if part not in ("", "."))


def _segments_match(target: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    if len(prefix) > len(target):
        return False
    return target[: len(prefix)] == prefix


class CachelinkMode(str, Enum):
    PLAIN = "plain"
    ZIP = "zip"


def load_cachelinks(
    mount_tree_paths: List[Path],
    inline_docs: Optional[Dict] = None,
    inline_source: Optional[Path] = None
) -> CachelinkIndex:
    """Load cachelinks from files.
    
    Args:
        mount_tree_paths: List of paths to cachelink files
        inline_docs: Inline cachelink documents
        inline_source: Source path for inline documents
        
    Returns:
        CachelinkIndex with loaded cachelinks
    """
    cachelinks: Dict[str, CachelinkDescriptor] = {}

    def add_descriptor(
        *,
        canonical_id: str,
        path_segments: list[str],
        source_file: Path,
        url: str,
        subfolder: str,
        mode_value: str | None = None,
        handler_value: str | None = None,
        rclone_remote: str | None = None,
        rclone_path: str | None = None,
        bandwidth_limit: str | None = None,
        transfer_concurrency: int | None = None,
        checkers: int | None = None,
        timeout: int | None = None,
        retries: int | None = None,
    ) -> None:
        clean_url = (url or "").strip()
        if not clean_url:
            return
        identifier, download_root = normalize_source_url(clean_url)
        mode = _parse_mode(mode_value) or _detect_mode(subfolder or "/")
        url_handler = _normalize_url_handler(handler_value)
        descriptor = CachelinkDescriptor(
            canonical_id=canonical_id,
            path_segments=tuple(path_segments),
            source_file=source_file,
            source_url=clean_url,
            identifier=identifier,
            download_root=download_root,
            subfolder=subfolder or "/",
            mode=mode,
            url_handler=url_handler,
            rclone_remote=rclone_remote,
            rclone_path=rclone_path,
            bandwidth_limit=bandwidth_limit,
            transfer_concurrency=transfer_concurrency,
            checkers=checkers,
            timeout=timeout,
            retries=retries,
        )
        cachelinks[canonical_id] = descriptor

    def is_leaf_mapping(node: object) -> bool:
        return isinstance(node, dict) and "url" in node

    def walk_tree(node: dict, path_segments: list[str], source_file: Path) -> None:
        for key, value in node.items():
            if isinstance(value, dict) and is_leaf_mapping(value):
                canonical_id = "/".join(path_segments + [key])
                add_descriptor(
                    canonical_id=canonical_id,
                    path_segments=path_segments + [key],
                    source_file=source_file,
                    url=value.get("url", ""),
                    subfolder=value.get("subfolder", "/"),
                    mode_value=value.get("mode"),
                    handler_value=value.get("url_handler") or value.get("handler"),
                    rclone_remote=value.get("rclone_remote"),
                    rclone_path=value.get("rclone_path"),
                    bandwidth_limit=value.get("bandwidth_limit"),
                    transfer_concurrency=value.get("transfer_concurrency"),
                    checkers=value.get("checkers"),
                    timeout=value.get("timeout"),
                    retries=value.get("retries"),
                )
            elif isinstance(value, dict):
                walk_tree(value, path_segments + [key], source_file)

    def process_doc(doc: object, source_file: Path) -> None:
        if isinstance(doc, dict) and isinstance(doc.get("cachelinks"), dict):
            walk_tree(doc["cachelinks"], [], source_file)
            return
        if isinstance(doc, dict):
            walk_tree(doc, [], source_file)
            return
        if isinstance(doc, list):
            for item in doc:
                if not isinstance(item, dict):
                    continue
                canonical_id = item.get("canonical_id") or ""
                backend_path = (item.get("backend_path") or "").strip("/")
                segments = [seg for seg in backend_path.split("/") if seg]
                leaf = canonical_id.split("/")[-1] if canonical_id else ""
                if not leaf:
                    continue
                segments.append(leaf)
                add_descriptor(
                    canonical_id=canonical_id or "/".join(segments),
                    path_segments=segments,
                    source_file=Path(item.get("source_file") or source_file),
                    url=item.get("url", ""),
                    subfolder=item.get("subfolder", "/"),
                    mode_value=item.get("mode"),
                    handler_value=item.get("url_handler") or item.get("handler"),
                    rclone_remote=item.get("rclone_remote"),
                    rclone_path=item.get("rclone_path"),
                    bandwidth_limit=item.get("bandwidth_limit"),
                    transfer_concurrency=item.get("transfer_concurrency"),
                    checkers=item.get("checkers"),
                    timeout=item.get("timeout"),
                    retries=item.get("retries"),
                )

    for path in mount_tree_paths:
        _logger.warning("Cachelink file loading is disabled; ignoring %s", path)

    if inline_docs:
        source_path = inline_source or Path("<inline>")
        _logger.info("Loading inline cachelinks")
        process_doc(inline_docs, source_path)

    return CachelinkIndex(cachelinks=cachelinks)


def normalize_source_url(url: str) -> tuple[str, str]:
    """Normalize source URL.
    
    Args:
        url: Source URL
        
    Returns:
        Tuple of (identifier, normalized_url)
    """
    parsed = urlparse(url.strip())
    netloc = parsed.netloc.lower()
    if netloc.endswith("archive.org"):
        segments = [seg for seg in parsed.path.split("/") if seg]
        identifier = None
        for idx, segment in enumerate(segments[:-1]):
            if segment in ("download", "details"):
                identifier = segments[idx + 1]
                break
        if not identifier and segments:
            identifier = segments[-1]
        if identifier:
            return identifier, f"https://archive.org/download/{identifier}/"
    identifier = _derive_identifier(parsed)
    return identifier, url.strip().rstrip("/")


def _detect_mode(subfolder: str) -> CachelinkMode:
    """Detect cachelink mode from subfolder.
    
    Args:
        subfolder: Subfolder path
        
    Returns:
        Mode string
    """
    normalized = (subfolder or "/").strip()
    if normalized in ("", "/"):
        return CachelinkMode.PLAIN
    parts = [seg for seg in normalized.strip("/").split("/") if seg]
    for idx, segment in enumerate(parts):
        if segment.endswith(".zip") and idx < len(parts) - 1:
            return CachelinkMode.ZIP
    return CachelinkMode.PLAIN


def derive_cachelink_name(url: str) -> str:
    identifier, _ = normalize_source_url(url)
    if identifier:
        return f"cachelink_{_sanitize_identifier(identifier)}"
    parsed = urlparse(url.strip())
    return _sanitize_identifier(parsed.path.split("/")[-1] or parsed.netloc or "cachelink")


def _parse_mode(mode_value: str | None) -> CachelinkMode | None:
    if not mode_value:
        return None
    value = str(mode_value).lower()
    if value in ("zip", "zip-folder"):
        return CachelinkMode.ZIP
    if value in ("plain", "directory"):
        return CachelinkMode.PLAIN
    return None


def _normalize_url_handler(handler_value: str | None) -> str:
    if not handler_value:
        return "auto"
    value = str(handler_value).strip().lower()
    if value in ("auto", "default"):
        return "auto"
    if value in ("rclone", "rclone-python"):
        return "rclone"
    if value in ("http", "https", "ftp", "ftps"):
        return value
    return "auto"


def _sanitize_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value or "")
    return cleaned or "cachelink"


def _derive_identifier(parsed) -> str:
    candidate = parsed.path.split("/")[-1] if parsed.path else ""
    return candidate or parsed.netloc or "cachelink"


def records_for_file(index: CachelinkIndex, file_path: Path) -> List[CachelinkRecord]:
    """Get cachelink records for a file.
    
    Args:
        index: Cachelink index
        file_path: Path to file
        
    Returns:
        List of cachelink records
    """
    # This would implement record extraction
    return []


def render_cachelink_records(records: List[CachelinkRecord]) -> Dict:
    """Render cachelink records to YAML document.
    
    Args:
        records: List of cachelink records
        
    Returns:
        YAML document dictionary
    """
    # This would implement YAML rendering
    return {}
