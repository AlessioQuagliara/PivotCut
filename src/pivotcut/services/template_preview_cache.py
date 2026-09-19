"""Cache of Character Library preview thumbnails (Milestone 6C).

Mirrors ``services.thumbnail_cache``'s design — the cache lives entirely
outside the domain, never touching ``RigTemplate``/JSON — but keys on a
content fingerprint instead of a "revision" counter: ``RigTemplate`` has no
revision field of its own and none is added here (see
``domain.rig``'s module docstring on keeping the domain free of UI-only
bookkeeping). Call :meth:`TemplatePreviewCache.clear` whenever
``Project.rig_templates`` changes wholesale (import/remove), though even
without that a stale fingerprint simply re-renders on next access.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage

from pivotcut.domain.assets import Asset
from pivotcut.domain.rig import RigTemplate
from pivotcut.services.export_renderer import render_rig_template_preview

_CacheKey = tuple[str, int, int]  # (template_id, content_fingerprint, size)


def _fingerprint(template: RigTemplate) -> int:
    """A cheap content hash: any bone-field change invalidates the cache
    entry, without needing a revision counter on the domain dataclass."""
    return hash(
        (
            template.root_bone_id,
            tuple(
                (
                    bone.id,
                    bone.parent_id,
                    bone.asset_id,
                    bone.x,
                    bone.y,
                    bone.rotation,
                    bone.scale_x,
                    bone.scale_y,
                    bone.pivot_x,
                    bone.pivot_y,
                    bone.z_index,
                    bone.visible,
                    bone.opacity,
                )
                for bone in template.bones
            ),
        )
    )


class TemplatePreviewCache:
    def __init__(self) -> None:
        self._images: dict[_CacheKey, QImage] = {}

    def get(
        self,
        template: RigTemplate,
        assets_by_id: dict[str, Asset],
        project_dir: Path | None,
        size: int = 160,
    ) -> QImage:
        """Return a cached preview, rendering (and caching) on a miss."""
        key: _CacheKey = (template.id, _fingerprint(template), size)
        cached = self._images.get(key)
        if cached is not None:
            return cached
        image = render_rig_template_preview(template, assets_by_id, project_dir, size)
        self._images[key] = image
        return image

    def clear(self) -> None:
        self._images.clear()
