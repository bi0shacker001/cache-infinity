"""Enhanced File Browser for WebUI with advanced features."""

from __future__ import annotations

import logging
import os
import mimetypes
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from .service import CacheInfinityService

_LOGGER = logging.getLogger(__name__)


class FileBrowser:
    """Enhanced file browser with advanced features."""
    
    def __init__(self, service: CacheInfinityService):
        self.service = service
        self._sort_by = "name"  # name, size, modified, type
        self._sort_order = "asc"  # asc, desc
        self._view_mode = "list"  # list, grid, details
        self._show_hidden = False
        self._search_query = ""
        self._selected_items: List[str] = []
        
    def browse(self, location: str, relative_path: str = "/", 
               sort_by: Optional[str] = None, sort_order: Optional[str] = None,
               view_mode: Optional[str] = None, show_hidden: Optional[bool] = None,
               search_query: Optional[str] = None) -> Dict[str, Any]:
        """Browse files with enhanced features."""
        if sort_by:
            self._sort_by = sort_by
        if sort_order:
            self._sort_order = sort_order
        if view_mode:
            self._view_mode = view_mode
        if show_hidden is not None:
            self._show_hidden = show_hidden
        if search_query is not None:
            self._search_query = search_query
            
        try:
            # Get base directory info
            normalized_location, segments, target = self._resolve_storage_directory(
                location, relative_path, ensure_exists=False
            )
            
            # Get all entries
            entries = self._get_directory_entries(target)
            
            # Apply filters
            if not self._show_hidden:
                entries = [e for e in entries if not e["name"].startswith(".")]
                
            if self._search_query:
                query = self._search_query.lower()
                entries = [
                    e for e in entries 
                    if query in e["name"].lower() or 
                       (e["type"] == "file" and query in (e.get("content_preview", "").lower()[:500]))
                ]
            
            # Apply sorting
            entries = self._sort_entries(entries)
            
            # Build breadcrumbs
            breadcrumbs = self._build_breadcrumbs(normalized_location, segments)
            
            # Get parent path
            parent_path = self._get_parent_path(location, segments)
            
            # Calculate directory stats
            stats = self._calculate_directory_stats(entries)
            
            return {
                "location": normalized_location,
                "path": "/" + "/".join(segments) if segments else "/",
                "parent_path": parent_path,
                "entries": entries,
                "breadcrumbs": breadcrumbs,
                "stats": stats,
                "view_options": {
                    "sort_by": self._sort_by,
                    "sort_order": self._sort_order,
                    "view_mode": self._view_mode,
                    "show_hidden": self._show_hidden,
                    "search_query": self._search_query,
                },
                "selected_items": self._selected_items,
            }
            
        except Exception as exc:
            _LOGGER.error("File browser error: %s", exc)
            return {
                "location": location,
                "path": relative_path,
                "entries": [],
                "breadcrumbs": [],
                "error": str(exc),
                "stats": {"files": 0, "directories": 0, "total_size": 0},
                "view_options": {},
                "selected_items": [],
            }
    
    def _resolve_storage_directory(self, location: str, relative: str | None, 
                                 ensure_exists: bool = True) -> Tuple[str, tuple[str, ...], Path]:
        """Resolve storage directory (copied from service.py)."""
        base = self._storage_base(location)
        segments = self._normalize_relative_path(relative)
        target = base.joinpath(*segments) if segments else base
        resolved_base = base.resolve()
        resolved_target = target if not ensure_exists else target.resolve() if target.exists() else target
        if ensure_exists:
            if not target.exists() or not target.is_dir():
                raise ConfigError("Requested path is unavailable")
            try:
                resolved_target.relative_to(resolved_base)
            except ValueError as exc:
                raise ConfigError("Path traversal outside base is not allowed") from exc
        return location.lower(), segments, target
    
    def _storage_base(self, location: str) -> Path:
        """Get storage base path (copied from service.py)."""
        loc = (location or "backend").strip().lower()
        if loc == "backend":
            return self.service.backend_registry.primary.definition.backend_cache_root
        if loc == "staging":
            return self.service.staging.base_path
        raise ConfigError("Unknown storage location")
    
    def _normalize_relative_path(self, relative: str | None) -> tuple[str, ...]:
        """Normalize relative path (copied from service.py)."""
        if not relative or relative == "/":
            return tuple()
        clean = PurePosixPath("/" + relative.lstrip("/"))
        segments: list[str] = []
        for segment in clean.parts:
            if segment in ("", "/"):
                continue
            if segment == "..":
                raise ConfigError("Path traversal is not allowed")
            segments.append(segment)
        return tuple(segments)
    
    def _get_directory_entries(self, target: Path) -> List[Dict[str, Any]]:
        """Get all directory entries with metadata."""
        entries = []
        
        try:
            for child in target.iterdir():
                try:
                    metadata = child.stat()
                    entry = self._build_entry_info(child, metadata)
                    entries.append(entry)
                except (OSError, PermissionError):
                    # Skip files we can't access
                    continue
        except (OSError, PermissionError):
            # Directory not accessible
            pass
            
        return entries
    
    def _build_entry_info(self, path: Path, metadata) -> Dict[str, Any]:
        """Build comprehensive entry information."""
        is_dir = path.is_dir()
        name = path.name
        size = metadata.st_size if not is_dir else 0
        modified = metadata.st_mtime
        created = metadata.st_ctime
        
        # Determine file type and icon
        file_type, icon = self._classify_file(path, is_dir)
        
        # Get additional metadata for files
        preview = None
        if not is_dir:
            preview = self._get_file_preview(path)
        
        # Calculate directory size if it's a directory
        directory_size = 0
        if is_dir:
            directory_size = self._calculate_directory_size(path)
        
        return {
            "name": name,
            "path": str(path),
            "relative_path": "/" + str(path.relative_to(self._storage_base("backend"))),
            "is_dir": is_dir,
            "size": size,
            "directory_size": directory_size,
            "modified": modified,
            "created": created,
            "type": "directory" if is_dir else "file",
            "file_type": file_type,
            "icon": icon,
            "extension": path.suffix.lower() if not is_dir else "",
            "preview": preview,
            "readable": self._is_file_readable(path) if not is_dir else True,
            "executable": os.access(path, os.X_OK) if not is_dir else False,
            "permissions": self._get_permissions_string(metadata),
        }
    
    def _classify_file(self, path: Path, is_dir: bool) -> Tuple[str, str]:
        """Classify file type and determine appropriate icon."""
        if is_dir:
            return "directory", "📁"
        
        suffix = path.suffix.lower()
        
        # Document types
        if suffix in {'.txt', '.md', '.rst', '.doc', '.docx', '.pdf', '.rtf'}:
            return "document", "📄"
        
        # Image types
        if suffix in {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico'}:
            return "image", "🖼️"
        
        # Video types
        if suffix in {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm'}:
            return "video", "🎬"
        
        # Audio types
        if suffix in {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a'}:
            return "audio", "🎵"
        
        # Archive types
        if suffix in {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.iso'}:
            return "archive", "📦"
        
        # Code files
        if suffix in {'.py', '.js', '.html', '.css', '.java', '.cpp', '.c', '.php', '.rb', '.go', '.rust'}:
            return "code", "💻"
        
        # Configuration files
        if suffix in {'.json', '.yaml', '.yml', '.xml', '.ini', '.conf', '.cfg'}:
            return "config", "⚙️"
        
        # Executable files
        if suffix in {'.exe', '.bat', '.sh', '.ps1'}:
            return "executable", "⚙️"
        
        # Default file type
        return "file", "📎"
    
    def _get_file_preview(self, path: Path, max_size: int = 1024 * 1024) -> Optional[str]:
        """Get a preview of the file content."""
        try:
            if path.stat().st_size > max_size:
                return None
            
            # Try to read as text first
            try:
                with path.open('r', encoding='utf-8') as f:
                    content = f.read(500)
                    return content if content.strip() else None
            except UnicodeDecodeError:
                # Try common encodings
                for encoding in ['latin-1', 'cp1252']:
                    try:
                        with path.open('r', encoding=encoding) as f:
                            content = f.read(500)
                            return content if content.strip() else None
                    except UnicodeDecodeError:
                        continue
                
                # For binary files, try to extract text-like content
                with path.open('rb') as f:
                    content = f.read(1000)
                    # Look for printable ASCII sequences
                    text_content = ''.join(chr(b) if 32 <= b <= 126 else ' ' for b in content)
                    text_content = ' '.join(text_content.split())  # Clean up whitespace
                    return text_content[:200] if text_content.strip() else None
                    
        except (OSError, PermissionError):
            return None
    
    def _is_file_readable(self, path: Path) -> bool:
        """Check if file is readable."""
        try:
            return os.access(path, os.R_OK)
        except (OSError, PermissionError):
            return False
    
    def _get_permissions_string(self, metadata) -> str:
        """Get Unix-style permissions string."""
        mode = metadata.st_mode
        perms = ''
        
        # Owner permissions
        perms += 'r' if mode & 0o400 else '-'
        perms += 'w' if mode & 0o200 else '-'
        perms += 'x' if mode & 0o100 else '-'
        
        # Group permissions
        perms += 'r' if mode & 0o040 else '-'
        perms += 'w' if mode & 0o020 else '-'
        perms += 'x' if mode & 0o010 else '-'
        
        # Other permissions
        perms += 'r' if mode & 0o004 else '-'
        perms += 'w' if mode & 0o002 else '-'
        perms += 'x' if mode & 0o001 else '-'
        
        return perms
    
    def _calculate_directory_size(self, path: Path, max_depth: int = 3) -> int:
        """Calculate directory size with depth limit for performance."""
        total_size = 0
        try:
            for child in path.rglob('*'):
                try:
                    if child.is_file():
                        total_size += child.stat().st_size
                except (OSError, PermissionError):
                    continue
        except (OSError, PermissionError):
            pass
        return total_size
    
    def _sort_entries(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sort entries based on current sort criteria."""
        reverse = self._sort_order == "desc"
        
        if self._sort_by == "name":
            return sorted(entries, key=lambda x: x["name"].lower(), reverse=reverse)
        elif self._sort_by == "size":
            return sorted(entries, key=lambda x: x["size"] if not x["is_dir"] else x["directory_size"], reverse=reverse)
        elif self._sort_by == "modified":
            return sorted(entries, key=lambda x: x["modified"], reverse=reverse)
        elif self._sort_by == "type":
            return sorted(entries, key=lambda x: (x["file_type"], x["name"].lower()), reverse=reverse)
        else:
            return entries
    
    def _build_breadcrumbs(self, location: str, segments: tuple[str, ...]) -> List[Dict[str, Any]]:
        """Build breadcrumb navigation."""
        breadcrumbs = [
            {"label": location.upper(), "path": "/", "active": len(segments) == 0}
        ]
        
        accum: list[str] = []
        for i, segment in enumerate(segments):
            accum.append(segment)
            path = "/" + "/".join(accum)
            breadcrumbs.append({
                "label": segment,
                "path": path,
                "active": i == len(segments) - 1
            })
        
        return breadcrumbs
    
    def _get_parent_path(self, location: str, segments: tuple[str, ...]) -> Optional[str]:
        """Get parent directory path."""
        if not segments:
            return None
        if len(segments) == 1:
            return "/"
        return "/" + "/".join(segments[:-1])
    
    def _calculate_directory_stats(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate directory statistics."""
        files = sum(1 for e in entries if not e["is_dir"])
        directories = sum(1 for e in entries if e["is_dir"])
        total_size = sum(e["size"] if not e["is_dir"] else e["directory_size"] for e in entries)
        
        # File type distribution
        file_types = {}
        for e in entries:
            if not e["is_dir"]:
                file_type = e["file_type"]
                file_types[file_type] = file_types.get(file_type, 0) + 1
        
        return {
            "files": files,
            "directories": directories,
            "total_size": total_size,
            "file_types": file_types,
        }
    
    def search_files(self, location: str, query: str, path: str = "/") -> List[Dict[str, Any]]:
        """Search for files by name or content."""
        self._search_query = query
        result = self.browse(location, path)
        return result.get("entries", [])
    
    def get_file_details(self, location: str, file_path: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific file."""
        try:
            path = self._resolve_storage_path(location, file_path)
            if not path.exists():
                return None
            
            metadata = path.stat()
            return self._build_entry_info(path, metadata)
        except Exception:
            return None
    
    def _resolve_storage_path(self, location: str, relative: str | None) -> Path:
        """Resolve storage path (copied from service.py)."""
        base = self._storage_base(location)
        segments = self._normalize_relative_path(relative)
        target = base.joinpath(*segments) if segments else base
        resolved_base = base.resolve()
        resolved_target = target.resolve()
        try:
            resolved_target.relative_to(resolved_base)
        except ValueError as exc:
            raise ConfigError("Path traversal outside base is not allowed") from exc
        return resolved_target


# Import error handling
try:
    from .config import ConfigError
except ImportError:
    class ConfigError(Exception):
        pass


__all__ = ["FileBrowser"]