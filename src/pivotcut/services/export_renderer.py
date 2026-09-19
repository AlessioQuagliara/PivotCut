"""Headless rendering of a Project's frames to QImage / PNG sequence.

This is the **only** code path allowed to produce exported pixels. It reads
exclusively from the domain (``Project``/``Frame``/``Asset``) and produces
images — it never depends on ``MainWindow``, selection state, focus, editor
zoom or any other ``CanvasView``/UI concept, and it is safe to call from a
worker thread with no visible window at all (no widget screenshots).

It reuses the same placement math as the interactive editor
(``domain.rig.world_transforms`` + ``domain.camera.character_plane_matrix``
for rig bones, ``domain.camera.layer_screen_matrix`` for layers) and the
same Qt item classes as ``ui.graphics_items`` (``BoneItem``/
``MissingAssetItem``/``LayerItem``/``MissingLayerItem``, which already hold
zero authoritative state and no ``CanvasView`` dependency), but it is its
own dedicated pipeline: ``_build_offscreen_scene`` below never adds a
selection outline, an output-frame border or a grid — those are purely
``CanvasView`` editor concerns and must never leak into exported output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsScene

from pivotcut.domain.assets import Asset
from pivotcut.domain.camera import character_plane_matrix, layer_screen_matrix
from pivotcut.domain.models import Frame, Project
from pivotcut.domain.playback import expanded_timeline_progress, validate_playback_settings
from pivotcut.domain.rig import Rig, RigTemplate, world_transforms
from pivotcut.services.playback_engine import PlaybackEngine
from pivotcut.ui.graphics_items import (
    BoneItem,
    LayerItem,
    MissingAssetItem,
    MissingLayerItem,
    _load_pixmap,
    _matrix_to_qtransform,
)


def _parse_background_color(hex_color: str) -> QColor:
    color = QColor(hex_color)
    return color if color.isValid() else QColor(0, 0, 0, 255)


def _build_offscreen_scene(
    scene: QGraphicsScene,
    frame: Frame,
    assets_by_id: dict[str, Asset],
    project_dir: Path | None,
    scene_width: float,
    scene_height: float,
    pixmap_cache: dict[str, QPixmap],
) -> None:
    """Populate ``scene`` with exactly the final scene content for ``frame``.

    No selection overlay, no output-frame border, no grid — this function
    is the entire contents of the exported image.
    """
    for rig in frame.rigs:
        transforms = world_transforms(rig)
        camera_matrix = character_plane_matrix(frame.camera, scene_width, scene_height)
        for bone in rig.bones:
            asset = assets_by_id.get(bone.asset_id)
            pixmap = _load_pixmap(pixmap_cache, asset, project_dir)
            if pixmap is None:
                item = MissingAssetItem(
                    bone.id,
                    float(asset.width) if asset is not None else 64.0,
                    float(asset.height) if asset is not None else 64.0,
                    asset.name if asset is not None else bone.asset_id,
                )
            else:
                item = BoneItem(bone.id, pixmap)
            screen_matrix = transforms[bone.id].then(camera_matrix)
            item.setTransform(_matrix_to_qtransform(screen_matrix))
            item.setZValue(bone.z_index)
            item.setVisible(bone.visible)
            item.setOpacity(max(0.0, min(1.0, bone.opacity)))
            scene.addItem(item)

    for layer in frame.layers:
        asset = assets_by_id.get(layer.asset_id)
        pixmap = _load_pixmap(pixmap_cache, asset, project_dir)
        if pixmap is None:
            item = MissingLayerItem(
                layer.id,
                float(asset.width) if asset is not None else 64.0,
                float(asset.height) if asset is not None else 64.0,
                asset.name if asset is not None else layer.asset_id,
            )
        else:
            item = LayerItem(layer.id, pixmap)
        screen_matrix = layer_screen_matrix(layer, frame.camera, scene_width, scene_height)
        item.setTransform(_matrix_to_qtransform(screen_matrix))
        item.setZValue(layer.z_index)
        item.setVisible(layer.visible)
        item.setOpacity(max(0.0, min(1.0, layer.opacity)))
        scene.addItem(item)


def _render_frame_object_to_qimage(
    project: Project,
    frame: Frame,
    force_opaque_background: bool,
    project_dir: Path | None,
) -> QImage:
    """Render an already-resolved ``Frame`` object to a ``QImage``.

    The shared core of :func:`render_frame_to_qimage` (a real timeline
    pose) and :func:`render_video_frame_to_qimage` (possibly a virtual,
    interpolated frame — see ``services.playback_engine``) — ``frame``
    itself is all either caller needs to have already resolved; this
    function has no notion of a timeline index.
    """
    scene_width = float(project.scene_settings.width)
    scene_height = float(project.scene_settings.height)

    background_color = _parse_background_color(project.scene_settings.background_color)
    if force_opaque_background and background_color.alpha() < 255:
        background_color.setAlpha(255)

    scene = QGraphicsScene(0.0, 0.0, scene_width, scene_height)
    assets_by_id = {asset.id: asset for asset in project.assets}
    pixmap_cache: dict[str, QPixmap] = {}
    _build_offscreen_scene(scene, frame, assets_by_id, project_dir, scene_width, scene_height, pixmap_cache)

    image = QImage(int(scene_width), int(scene_height), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(background_color)

    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        target = QRectF(0.0, 0.0, scene_width, scene_height)
        scene.render(painter, target, target)
    finally:
        painter.end()

    return image


def render_frame_to_qimage(
    project: Project,
    frame_index: int,
    force_opaque_background: bool = False,
    project_dir: Path | None = None,
) -> QImage:
    """Render one timeline pose (``project.frames[frame_index]``) to a QImage.

    Renders at exact ``project.scene_settings`` pixel dimensions regardless
    of display DPI, in ``Format_ARGB32_Premultiplied``. A bone/layer whose
    PNG asset cannot be resolved on disk renders as the same dashed
    placeholder the editor shows instead of raising.

    ``force_opaque_background`` flattens the configured
    ``scene_settings.background_color`` to full alpha before compositing —
    used for MP4 frames, since yuv420p/H.264 has no alpha channel. PNG
    sequence export leaves the configured alpha intact.
    """
    validate_playback_settings(
        project.fps, project.exposure, project.scene_settings.width, project.scene_settings.height
    )
    if not (0 <= frame_index < len(project.frames)):
        raise IndexError(f"frame_index {frame_index} out of range for {len(project.frames)} frame(s).")

    return _render_frame_object_to_qimage(
        project, project.frames[frame_index], force_opaque_background, project_dir
    )


def render_video_frame_to_qimage(
    project: Project,
    pose_index: int,
    frame_progress: float,
    force_opaque_background: bool = False,
    project_dir: Path | None = None,
) -> QImage:
    """Render one actual **video** frame — possibly mid-way between two poses.

    When ``project.smooth_animation_enabled`` is ``False`` (the default) or
    ``frame_progress <= 0.0``, this is byte-for-byte identical to
    :func:`render_frame_to_qimage(project, pose_index, ...) <render_frame_to_qimage>` —
    every project created before Smooth Animation existed renders exactly
    as it always has. Otherwise, eases from ``project.frames[pose_index]``
    toward the next pose at ``frame_progress`` (``0.0``-``1.0``) via
    :class:`~pivotcut.services.playback_engine.PlaybackEngine`; the last
    pose has no "next" one, so it simply holds (see
    ``PlaybackEngine.get_interpolated_frame``'s docstring).

    See ``domain.playback.expanded_timeline_progress`` for how
    :func:`save_png_sequence` derives ``(pose_index, frame_progress)`` for
    every output video frame.
    """
    if not project.smooth_animation_enabled or frame_progress <= 0.0:
        return render_frame_to_qimage(project, pose_index, force_opaque_background, project_dir)

    validate_playback_settings(
        project.fps, project.exposure, project.scene_settings.width, project.scene_settings.height
    )
    if not (0 <= pose_index < len(project.frames)):
        raise IndexError(f"pose_index {pose_index} out of range for {len(project.frames)} frame(s).")

    next_index = pose_index + 1 if pose_index + 1 < len(project.frames) else pose_index
    virtual_frame = PlaybackEngine().get_interpolated_frame(
        project.frames[pose_index], project.frames[next_index], frame_progress
    )
    return _render_frame_object_to_qimage(project, virtual_frame, force_opaque_background, project_dir)


def save_png_sequence(
    project: Project,
    output_directory: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    project_dir: Path | None = None,
    force_opaque_background: bool = False,
) -> list[Path]:
    """Render the full exposure-expanded video-frame sequence as PNGs.

    Numbers files ``frame_000001.png``, ``frame_000002.png``, ... (1-indexed,
    six digits, matching FFmpeg's ``frame_%06d.png`` expectation). Calls
    ``progress_callback(done, total)`` after every frame written. If
    ``cancel_requested`` starts returning ``True`` partway through, stops
    cleanly and returns the paths written so far without deleting them.

    When ``project.smooth_animation_enabled`` is ``True``, each pose's
    exposure window eases toward the next pose instead of holding a hard
    cut — see :func:`render_video_frame_to_qimage`. ``False`` (the
    default) renders exactly as every project always has.
    """
    validate_playback_settings(
        project.fps, project.exposure, project.scene_settings.width, project.scene_settings.height
    )
    pose_progress = expanded_timeline_progress(len(project.frames), project.exposure)
    total = len(pose_progress)

    output_directory.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for video_frame_number, (pose_index, frame_progress) in enumerate(pose_progress, start=1):
        if cancel_requested is not None and cancel_requested():
            break
        image = render_video_frame_to_qimage(
            project,
            pose_index,
            frame_progress,
            force_opaque_background=force_opaque_background,
            project_dir=project_dir,
        )
        path = output_directory / f"frame_{video_frame_number:06d}.png"
        if not image.save(str(path), "PNG"):
            raise OSError(f"Failed to write PNG frame: {path}")
        written.append(path)
        if progress_callback is not None:
            progress_callback(video_frame_number, total)

    return written


def render_thumbnail(
    project: Project,
    frame_index: int,
    max_width: int,
    max_height: int,
    project_dir: Path | None = None,
) -> QImage:
    """Render one timeline pose scaled to fit within ``max_width``x``max_height``.

    Goes through :func:`render_frame_to_qimage` (never duplicates the
    rendering pipeline) then scales preserving the scene's aspect ratio,
    letterboxed/pillarboxed onto a transparent ``max_width``x``max_height``
    canvas so every thumbnail this returns is exactly that uniform size
    regardless of the project's own aspect ratio. No selection overlay,
    output-frame border or grid — same guarantee as the full-size render.
    """
    full = render_frame_to_qimage(project, frame_index, force_opaque_background=False, project_dir=project_dir)
    scaled = full.scaled(
        max_width, max_height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )

    if scaled.width() == max_width and scaled.height() == max_height:
        return scaled

    canvas = QImage(max_width, max_height, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    try:
        x = (max_width - scaled.width()) // 2
        y = (max_height - scaled.height()) // 2
        painter.drawImage(x, y, scaled)
    finally:
        painter.end()
    return canvas


def render_rig_template_preview(
    template: RigTemplate,
    assets_by_id: dict[str, Asset],
    project_dir: Path | None,
    size: int = 160,
) -> QImage:
    """A clean, editor-free preview of a ``RigTemplate`` for the Character
    Library (Milestone 6C) — the rig's own bounding box, centered and scaled
    (preserving aspect ratio) onto a ``size``x``size`` transparent canvas.

    Deliberately its own dedicated function rather than a detour through
    :func:`render_frame_to_qimage`: a template has no ``Frame``/``Camera``/
    layers to render, is never posed by a project camera, and must never
    show the Rig Builder's pivot/attach handles or skeleton guides — this
    reuses only the same stateless Qt item classes
    (``BoneItem``/``MissingAssetItem``) and ``domain.rig.world_transforms``,
    exactly like the rest of this module, never ``CanvasView``.
    """
    canvas = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    if not template.bones:
        return canvas

    probe_rig = Rig(id="__template_preview__", name="", root_bone_id=template.root_bone_id, bones=template.bones)
    transforms = world_transforms(probe_rig)

    scene = QGraphicsScene()
    pixmap_cache: dict[str, QPixmap] = {}
    bounds = QRectF()
    for bone in template.bones:
        asset = assets_by_id.get(bone.asset_id)
        pixmap = _load_pixmap(pixmap_cache, asset, project_dir)
        if pixmap is None:
            item = MissingAssetItem(
                bone.id,
                float(asset.width) if asset is not None else 64.0,
                float(asset.height) if asset is not None else 64.0,
                asset.name if asset is not None else bone.asset_id,
            )
        else:
            item = BoneItem(bone.id, pixmap)
        item.setTransform(_matrix_to_qtransform(transforms[bone.id]))  # world space directly — no camera
        item.setZValue(bone.z_index)
        item.setVisible(bone.visible)
        item.setOpacity(max(0.0, min(1.0, bone.opacity)))
        scene.addItem(item)
        bounds = bounds.united(item.mapToScene(item.boundingRect()).boundingRect())

    if bounds.isEmpty():
        bounds = QRectF(-1.0, -1.0, 2.0, 2.0)
    padding = max(bounds.width(), bounds.height(), 1.0) * 0.08
    bounds = bounds.adjusted(-padding, -padding, padding, padding)

    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        target = QRectF(0.0, 0.0, float(size), float(size))
        scene.render(painter, target, bounds, Qt.AspectRatioMode.KeepAspectRatio)
    finally:
        painter.end()
    return canvas
