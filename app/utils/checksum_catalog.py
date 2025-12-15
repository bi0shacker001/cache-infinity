"""Checksum catalog ingestion and lookup."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..db.index import CatalogChecksum, IndexDatabase

_ALGORITHMS = ("sha256", "sha1", "md5", "crc32")


@dataclass
class CatalogRecord:
    """In-memory representation of checksum data for a filename."""

    source: str
    name: str
    size: int | None
    checksums: dict[str, str] = field(default_factory=dict)

    def preferred_pair(self) -> tuple[str, str] | None:
        for algo in _ALGORITHMS:
            digest = self.checksums.get(algo)
            if digest:
                return algo, digest
        return None


class ChecksumCatalog:
    """Loads checksum reference datasets and exposes lookup helpers."""

    def __init__(self, config_dir: Path, database: IndexDatabase):
        self.config_dir = Path(config_dir)
        self._database = database
        self._records: dict[str, CatalogRecord] = {}
        self.reload()

    # Public API -------------------------------------------------------------
    def reload(self) -> None:
        """Reload checksum datasets from disk and refresh the DB snapshot."""

        entries = list(self._scan_catalog_files())
        grouped: dict[str, CatalogRecord] = {}
        for entry in entries:
            key = entry.name.lower()
            record = grouped.get(key)
            if record is None:
                record = CatalogRecord(source=entry.source, name=entry.name, size=entry.size)
                grouped[key] = record
            if entry.size is not None and record.size is None:
                record.size = entry.size
            record.checksums[entry.algorithm] = entry.digest
        self._records = grouped
        self._database.refresh_catalog(entries)

    def lookup(self, entry_name: str, *, size: int | None = None) -> tuple[str, str] | None:
        """Return (algorithm, digest) for the provided filename if known."""

        record = self._records.get(entry_name.lower())
        if not record:
            return None
        if size is not None and record.size is not None and record.size != size:
            return None
        return record.preferred_pair()

    # Disk ingestion ---------------------------------------------------------
    def _scan_catalog_files(self) -> Iterable[CatalogChecksum]:
        catalog_dir = self.config_dir / "checksums"
        if not catalog_dir.exists():
            return []
        entries: list[CatalogChecksum] = []
        for path in sorted(catalog_dir.glob("*")):
            if path.suffix.lower() == ".csv":
                entries.extend(self._parse_csv(path))
            elif path.suffix.lower() in {".json", ".jsn"}:
                entries.extend(self._parse_json(path))
        return entries

    def _parse_csv(self, path: Path) -> list[CatalogChecksum]:
        rows: list[CatalogChecksum] = []
        with path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                record_rows = self._row_to_checksums(row, path.stem)
                rows.extend(record_rows)
        return rows

    def _parse_json(self, path: Path) -> list[CatalogChecksum]:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            entries = data.get("entries") or data.get("files") or []
        elif isinstance(data, list):
            entries = data
        else:
            entries = []
        rows: list[CatalogChecksum] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            row = self._row_to_checksums(item, path.stem)
            rows.extend(row)
        return rows

    def _row_to_checksums(self, row: dict[str, object], source: str) -> list[CatalogChecksum]:
        name = self._extract_name(row)
        if not name:
            return []
        size = self._extract_size(row)
        checksums: list[CatalogChecksum] = []
        for field in _ALGORITHMS:
            value = row.get(field) or row.get(field.upper())
            if isinstance(value, str) and value.strip():
                checksums.append(CatalogChecksum(source=source, name=name, algorithm=field, digest=value.strip(), size=size))
        return checksums

    @staticmethod
    def _extract_name(row: dict[str, object]) -> str | None:
        for key in ("name", "filename", "path", "file"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _extract_size(row: dict[str, object]) -> int | None:
        value = row.get("size")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return int(text)
            except ValueError:
                try:
                    return int(float(text))
                except ValueError:
                    return None
        return None


__all__ = ["ChecksumCatalog"]
