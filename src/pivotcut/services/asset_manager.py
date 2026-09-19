"""PNG asset import, deduplication and path resolution.

The ``Asset`` dataclass itself lives in ``domain.assets`` (kept Qt-free so
``Project`` — a pure domain object — can hold a typed asset registry). This
module is the *service* layer around it: it touches the filesystem
(Pillow, for real PNG validation) and Qt (a single ``QFileDialog`` picker
function) to import files and turn them into registry entries.

Assets are never physically copied into the project folder in this
milestone: ``source_path`` always stays the absolute, canonical path on
disk, and ``relative_path`` is a derived convenience recomputed against the
project file's directory on every save.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from PySide6.QtWidgets import QFileDialog, QWidget

from pivotcut.domain.assets import Asset
from pivotcut.domain.models import Project


class AssetImportError(Exception):
    """Raised when a file cannot be imported as a PNG asset."""


def validate_png_file(path: Path) -> tuple[int, int]:
    """Return ``(width, height)`` if ``path`` is a real PNG Pillow can read.

    Raises ``AssetImportError`` with a human-readable message otherwise.
    """
    if not path.is_file():
        raise AssetImportError(f"File not found: {path}")
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise AssetImportError(f"Not a PNG file: {path} (detected format: {image.format})")
            width, height = image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise AssetImportError(f"Cannot read image file: {path}") from exc
    return width, height


def derive_relative_path(absolute_path: Path, project_dir: Path | None) -> str | None:
    """Express ``absolute_path`` relative to ``project_dir``, if possible."""
    if project_dir is None:
        return None
    try:
        return str(absolute_path.resolve().relative_to(project_dir.resolve()))
    except ValueError:
        return None


def resolve_asset_path(asset: Asset, project_dir: Path | None) -> Path | None:
    """Resolve an asset's current on-disk path.

    Prefers ``relative_path`` (resolved against ``project_dir``) since it
    survives the project folder being moved/shared; falls back to the
    absolute ``source_path``. Returns ``None`` (never raises) if neither
    exists, so a missing asset never blocks opening the rest of the project.
    """
    if asset.relative_path and project_dir is not None:
        candidate = project_dir / asset.relative_path
        if candidate.is_file():
            return candidate
    if asset.source_path:
        candidate = Path(asset.source_path)
        if candidate.is_file():
            return candidate
    return None


def find_existing_asset(project: Project, file_path: Path, project_dir: Path | None) -> Asset | None:
    """Return the already-registered asset for ``file_path``, if any."""
    target = file_path.resolve()
    for asset in project.assets:
        resolved = resolve_asset_path(asset, project_dir)
        if resolved is not None and resolved.resolve() == target:
            return asset
    return None


def import_png_asset(project: Project, file_path: Path, project_dir: Path | None) -> Asset:
    """Import ``file_path`` into ``project.assets``, reusing an existing entry if already registered.

    Raises ``AssetImportError`` if ``file_path`` is not a readable PNG.
    """
    existing = find_existing_asset(project, file_path, project_dir)
    if existing is not None:
        return existing

    width, height = validate_png_file(file_path)
    asset = Asset(
        id=str(uuid.uuid4()),
        name=file_path.stem,
        source_path=str(file_path.resolve()),
        relative_path=derive_relative_path(file_path, project_dir),
        width=width,
        height=height,
    )
    project.assets.append(asset)
    return asset


def refresh_relative_paths(project: Project, project_dir: Path) -> None:
    """Recompute ``relative_path`` for every asset against ``project_dir``.

    Called after Save/Save As so assets stay resolvable relative to
    wherever the project file currently lives.
    """
    for asset in project.assets:
        asset.relative_path = derive_relative_path(Path(asset.source_path), project_dir)


def pick_png_file_via_dialog(parent: QWidget | None) -> Path | None:
    """Open a native "Import PNG" file picker. Returns ``None`` if cancelled."""
    path_str, _ = QFileDialog.getOpenFileName(
        parent, "Import PNG", str(Path.home()), "PNG Images (*.png)"
    )
    if not path_str:
        return None
    return Path(path_str)
