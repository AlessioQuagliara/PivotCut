"""Pure ``Asset`` data model: a reusable, registry-tracked PNG reference.

Kept in its own leaf module (rather than in ``models.py`` or
``services/asset_manager.py``) so both ``domain.models`` (which embeds the
asset registry in ``Project``) and ``domain.rig`` (which validates bones
against it) can import it without creating an import cycle between the two.
The actual import/validation/path-resolution logic (which touches the
filesystem, Pillow and Qt) lives in ``services.asset_manager`` — this module
only holds the serializable shape.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Asset:
    """A registered, reusable PNG asset.

    ``source_path`` is always the absolute, canonical path on disk.
    ``relative_path`` is a convenience path relative to the project file's
    directory, derived and refreshed on save; it is ``None`` until the
    project has been saved at least once.
    """

    id: str
    name: str
    source_path: str
    relative_path: str | None
    width: int
    height: int
