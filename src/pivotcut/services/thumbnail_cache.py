"""Revision-keyed cache of timeline thumbnails.

``Frame``/``Project`` stay Qt-free and carry no notion of a "revision" —
that is purely derived UI-cache-invalidation bookkeeping, not domain truth,
so it lives here instead of on the dataclasses in ``domain/models.py``.
Call :meth:`ThumbnailCache.bump` whenever a command actually mutates a
frame's rig/layer/camera content (see ``app/main_window.py``'s command
execution path), and :meth:`ThumbnailCache.bump_all` when something scene-
wide changes (asset registry, ``SceneSettings``). A cache miss re-renders
through :func:`services.export_renderer.render_thumbnail`, never
duplicating that pipeline.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage

from pivotcut.domain.models import Project
from pivotcut.services.export_renderer import render_thumbnail

_CacheKey = tuple[str, int, int, int, str]  # (frame_id, revision, max_width, max_height, background_mode)


class ThumbnailCache:
    def __init__(self) -> None:
        self._revisions: dict[str, int] = {}
        self._images: dict[_CacheKey, QImage] = {}

    def revision(self, frame_id: str) -> int:
        return self._revisions.get(frame_id, 0)

    def bump(self, frame_id: str) -> None:
        """Invalidate cached thumbnails for one frame (its rig/layer/camera changed)."""
        self._revisions[frame_id] = self._revisions.get(frame_id, 0) + 1

    def bump_all(self) -> None:
        """Invalidate every cached thumbnail (asset registry or SceneSettings changed)."""
        self._images.clear()

    def get(
        self,
        project: Project,
        frame_index: int,
        max_width: int,
        max_height: int,
        project_dir: Path | None = None,
        background_mode: str = "scene",
    ) -> QImage:
        """Return a cached thumbnail, rendering (and caching) on a miss."""
        frame = project.frames[frame_index]
        key: _CacheKey = (frame.id, self.revision(frame.id), max_width, max_height, background_mode)
        cached = self._images.get(key)
        if cached is not None:
            return cached
        image = render_thumbnail(project, frame_index, max_width, max_height, project_dir=project_dir)
        self._images[key] = image
        return image
