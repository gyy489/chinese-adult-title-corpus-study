"""Shared project paths for the data-collection utilities."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SITE_CONFIG = PROJECT_ROOT / "config" / "sites.json"
DEFAULT_MYSQL_CONFIG = PROJECT_ROOT / "config" / "local" / "mysql.json"
DEFAULT_EXPORT_DIR = PROJECT_ROOT / "data" / "exports" / "latest"


def resolve_project_path(value: str | Path) -> Path:
    """Resolve relative CLI paths from the project root."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()
