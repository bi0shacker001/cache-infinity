"""Cachelink index parsing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from urllib import parse

import yaml

from ..core.errors import ConfigError


class CachelinkMode(str, Enum):
    """Represents how a cachelink should be interpreted."""

    PLAIN = "plain"
    ZIP = "zip"


@dataclass(frozen=True)
class CachelinkDescriptor:
    """Single cachelink leaf."""

    canonical_id: str
    path_segments: tuple[str, ...]
    source_file: Path
    source_url: str
    identifier: str
    download_root: str
    subfolder: str
    mode: CachelinkMode

    @property
    def remote_listing_url(self) -> str:
        base = self.download_root.rstrip("/") + "/"
        sub = self.subfolder.lstrip("/")
        return base + sub

    @property
    def backend_relative_folder(self) -> PurePosixPath:
        """Folder (relative to backend root) that contains this cachelink."""

        if len(self.path_segments) <= 1:
            return PurePosixPath("/")
        return PurePosixPath("/" + "/".join(self.path_segments[:-1]))


@dataclass
class CachelinkIndex:
    """In-memory representation of cachelinks."""

    cachelinks: dict[str, CachelinkDescriptor]

    def by_prefix(self, prefix: str) -> dict[str, CachelinkDescriptor]:
        """Return all cachelinks that start with the provided prefix."""

        return {k: v for k, v in self.cachelinks.items() if k.startswith(prefix)}


def load_cachelinks(
    paths: Iterable[Path],
    inline_docs: Iterable[Mapping[str, object]] | None = None,
    inline_source: Path | None = None,
) -> CachelinkIndex:
    """Parse all cachelink files from the provided paths plus inline definitions."""

    cachelinks: dict[str, CachelinkDescriptor] = {}
    for file_path in paths:
        document = _read_yaml(file_path)
        tree = _extract_cachelinks_root(document, file_path)
        _walk_tree([], tree, file_path, cachelinks)
    if inline_docs:
        source = inline_source or Path("<settings>")
        for document in inline_docs:
            tree = _extract_cachelinks_root(document, source)
            _walk_tree([], tree, source, cachelinks)
    return CachelinkIndex(cachelinks=cachelinks)


def _walk_tree(
    segments: Sequence[str],
    node: Mapping[str, object],
    source_file: Path,
    output: dict[str, CachelinkDescriptor],
) -> None:
    for key, value in node.items():
        if isinstance(value, Mapping) and _is_leaf(value):
            descriptor = _parse_leaf(segments + [key], value, source_file)
            output[descriptor.canonical_id] = descriptor
        elif isinstance(value, Mapping):
            _walk_tree(segments + [key], value, source_file, output)
        else:
            raise ConfigError(
                f"Invalid cachelink structure at {'/'.join(segments)} in {source_file}"
            )


def _is_leaf(node: Mapping[str, object]) -> bool:
    return "url" in node and "subfolder" in node


def _parse_leaf(
    segments: Sequence[str],
    node: Mapping[str, object],
    source_file: Path,
) -> CachelinkDescriptor:
    canonical_id = "/".join(segments)
    url_raw = node.get("url")
    subfolder_raw = node.get("subfolder")
    if not isinstance(url_raw, str) or not isinstance(subfolder_raw, str):
        raise ConfigError(
            f"Cachelink '{canonical_id}' must define string url and subfolder values"
        )
    identifier, download_root = normalize_source_url(url_raw)
    mode = _detect_mode(subfolder_raw)
    return CachelinkDescriptor(
        canonical_id=canonical_id,
        path_segments=tuple(segments),
        source_file=source_file,
        source_url=download_root,
        identifier=identifier,
        download_root=download_root,
        subfolder=subfolder_raw,
        mode=mode,
    )


def normalize_source_url(url: str) -> tuple[str, str]:
    lowered = url.strip()
    if lowered.startswith("https://archive.org/"):
        parts = lowered.split("/download/")
        if len(parts) == 2:
            identifier = parts[1].strip("/")
        else:
            details_parts = lowered.split("/details/")
            if len(details_parts) != 2:
                raise ConfigError(f"Could not parse archive.org identifier from {url}")
            identifier = details_parts[1].strip("/")
        if not identifier:
            raise ConfigError(f"Archive.org identifier missing in {url}")
        download_root = f"https://archive.org/download/{identifier}/"
        return identifier, download_root

    parsed = parse.urlparse(lowered)
    if parsed.scheme not in {"http", "https", "ftp", "ftps"}:
        raise ConfigError("Cachelinks must use http(s) or ftp(s) URLs")
    base = lowered if lowered.endswith("/") else lowered + "/"
    identifier = parsed.netloc + parsed.path
    return identifier or base, base


def _detect_mode(subfolder: str) -> CachelinkMode:
    segments = [segment for segment in subfolder.strip("/").split("/") if segment]
    for segment in segments:
        if segment.endswith(".zip"):
            return CachelinkMode.ZIP
    return CachelinkMode.PLAIN


def _read_yaml(path: Path) -> Mapping[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        doc = yaml.safe_load(handle) or {}
    if not isinstance(doc, MutableMapping):
        raise ConfigError(f"Cachelink file {path} must contain a mapping root")
    return doc


def _extract_cachelinks_root(document: Mapping[str, object], source_file: Path) -> Mapping[str, object]:
    root = document.get("cachelinks")
    if root is None:
        raise ConfigError(f"{source_file} must define a top-level 'cachelinks' mapping")
    if not isinstance(root, Mapping):
        raise ConfigError(f"cachelinks section in {source_file} must be a mapping")
    return root


@dataclass(frozen=True)
class CachelinkRecord:
    folder_segments: tuple[str, ...]
    url: str
    subfolder: str


def records_for_file(index: CachelinkIndex, source_file: Path) -> list[CachelinkRecord]:
    records: list[CachelinkRecord] = []
    for descriptor in index.cachelinks.values():
        if descriptor.source_file != source_file:
            continue
        folder = tuple(descriptor.path_segments[:-1])
        records.append(CachelinkRecord(folder_segments=folder, url=descriptor.source_url, subfolder=descriptor.subfolder))
    return records


def render_cachelink_records(records: Sequence[CachelinkRecord]) -> dict[str, object]:
    tree: dict[str, object] = {}
    by_folder: dict[tuple[str, ...], list[CachelinkRecord]] = {}
    for record in records:
        by_folder.setdefault(record.folder_segments, []).append(record)
        _ensure_folder(tree, record.folder_segments)
    for folder, bucket in by_folder.items():
        node = _ensure_folder(tree, folder)
        for key in list(node.keys()):
            if key.startswith("map"):
                del node[key]
        bucket_sorted = sorted(bucket, key=lambda r: (r.url, r.subfolder))
        for index, record in enumerate(bucket_sorted, start=1):
            map_id = f"map{index:04d}"
            node[map_id] = {"url": record.url, "subfolder": record.subfolder}
    return {"cachelinks": tree}


def _ensure_folder(root: dict[str, object], segments: tuple[str, ...]) -> dict[str, object]:
    node: dict[str, object] = root
    for segment in segments:
        child = node.setdefault(segment, {})
        if not isinstance(child, dict):
            child = {}
            node[segment] = child
        node = child
    return node


__all__ = ["CachelinkDescriptor", "CachelinkIndex", "CachelinkMode", "CachelinkRecord", "load_cachelinks", "records_for_file", "render_cachelink_records"]
