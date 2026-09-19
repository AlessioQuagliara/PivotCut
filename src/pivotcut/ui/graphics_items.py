"""Qt rendering layer for the rig and for background/foreground layers.

Architecture note: Qt items here hold **zero authoritative state**. Every
bone/layer gets a flat (non-nested) ``QGraphicsItem`` whose transform/z/
visibility/opacity are written from the domain model on every sync — never
read back into it. Qt's own item-parenting (``setParentItem``) is
deliberately *not* used to compose transforms; instead pure Python
(``domain.rig.world_transforms``, ``domain.camera``) computes each bone's/
layer's absolute screen transform, and that is set directly as the item's
``QTransform``. This guarantees the scene can always be fully rebuilt from
the dataclasses with no data loss, and that the domain model is the only
source of truth — as required by the Milestone 2/3 architecture correction.

Camera handling: rig bones live on the character plane (z_depth = 0), so
their pure FK world transform (``domain.rig.world_transforms``, unchanged
since Milestone 2) is composed with ``domain.camera.character_plane_matrix``
on top. Layers use ``domain.camera.layer_screen_matrix`` directly, which
already folds in their own placement plus the camera parallax/zoom. Neither
renderer ever touches the QGraphicsView's own navigation zoom/pan — that
stays a purely editor-side concern (see ``canvas_view.py``).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
)

from pivotcut.domain.assets import Asset
from pivotcut.domain.camera import Camera, character_plane_matrix, layer_screen_matrix
from pivotcut.domain.layer import Layer
from pivotcut.domain.models import Frame
from pivotcut.domain.rig import Bone, Matrix2D, Rig, find_bone, world_transforms
from pivotcut.services.asset_manager import resolve_asset_path


def _load_pixmap(
    cache: dict[str, QPixmap], asset: Asset | None, project_dir: Path | None
) -> QPixmap | None:
    """Resolve and load (with caching) the PNG for ``asset``, or ``None`` if unresolvable."""
    if asset is None:
        return None
    resolved = resolve_asset_path(asset, project_dir)
    if resolved is None:
        return None
    key = str(resolved)
    cached = cache.get(key)
    if cached is not None:
        return cached
    pixmap = QPixmap(str(resolved))
    if pixmap.isNull():
        return None
    cache[key] = pixmap
    return pixmap


def _matrix_to_qtransform(matrix: Matrix2D) -> QTransform:
    return QTransform(matrix.a, matrix.b, matrix.c, matrix.d, matrix.tx, matrix.ty)


def _make_selection_outline(item: QGraphicsItem, color: str) -> QGraphicsPolygonItem:
    polygon = item.mapToScene(item.boundingRect())
    outline = QGraphicsPolygonItem(polygon)
    outline.setPen(QPen(QColor(color), 2))
    outline.setBrush(Qt.BrushStyle.NoBrush)
    outline.setZValue(10_000)
    return outline


#: Colors/sizes for the three minimal manipulation handles a selected bone
#: gets on the main canvas (Hotfix UX-1) — deliberately just three, distinct
#: by shape+color, never a "sophisticated" gizmo system: a rotate handle
#: (top, circle), a uniform-scale handle (corner, square), and a pivot
#: marker (crosshair, display-only — dragging the pivot stays a Rig Builder
#: action, see ``ui/rig_builder_dialog.py``, to avoid duplicating that
#: attach/pivot-reconciliation logic in two places).
_ROTATE_HANDLE_COLOR = QColor("#7fe0ff")
_SCALE_HANDLE_COLOR = QColor("#ffb26b")
_PIVOT_MARKER_COLOR = QColor("#ffd25c")
_ROTATE_HANDLE_RADIUS = 9.0
_SCALE_HANDLE_SIZE = 12.0
_PIVOT_MARKER_SIZE = 7.0
_HANDLE_Z = 10_001


class RotateHandleItem(QGraphicsEllipseItem):
    """Drag target that rotates the selected bone around its pivot."""

    def __init__(self, bone_id: str) -> None:
        super().__init__(-_ROTATE_HANDLE_RADIUS, -_ROTATE_HANDLE_RADIUS, 2 * _ROTATE_HANDLE_RADIUS, 2 * _ROTATE_HANDLE_RADIUS)
        self.bone_id = bone_id
        self.setBrush(QBrush(_ROTATE_HANDLE_COLOR))
        self.setPen(QPen(QColor("#0a0a0a"), 1.5))
        self.setZValue(_HANDLE_Z)
        self.setToolTip("Drag to rotate around the pivot")


class ScaleHandleItem(QGraphicsRectItem):
    """Drag target that scales the selected bone uniformly around its pivot."""

    def __init__(self, bone_id: str) -> None:
        half = _SCALE_HANDLE_SIZE / 2.0
        super().__init__(-half, -half, _SCALE_HANDLE_SIZE, _SCALE_HANDLE_SIZE)
        self.bone_id = bone_id
        self.setBrush(QBrush(_SCALE_HANDLE_COLOR))
        self.setPen(QPen(QColor("#0a0a0a"), 1.5))
        self.setZValue(_HANDLE_Z)
        self.setToolTip("Drag to scale uniformly around the pivot")


def _make_pivot_marker(scene_pos: QPointF) -> QGraphicsEllipseItem:
    """Display-only crosshair-ish dot at the selected bone's pivot. Not a
    drag target: pivot repositioning stays a Rig Builder-only action."""
    half = _PIVOT_MARKER_SIZE / 2.0
    marker = QGraphicsEllipseItem(scene_pos.x() - half, scene_pos.y() - half, _PIVOT_MARKER_SIZE, _PIVOT_MARKER_SIZE)
    marker.setBrush(QBrush(_PIVOT_MARKER_COLOR))
    marker.setPen(QPen(QColor("#0a0a0a"), 1.5))
    marker.setZValue(_HANDLE_Z)
    marker.setToolTip("Pivot — where this Part rotates/scales from")
    return marker


class BoneItem(QGraphicsPixmapItem):
    """Renders one Bone's PNG asset. Purely a view: state comes from sync()."""

    def __init__(self, bone_id: str, pixmap: QPixmap) -> None:
        super().__init__(pixmap)
        self.bone_id = bone_id
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        # Never let Qt's own drag/selection become authoritative over pose:
        # CanvasView reads clicks/drags and writes straight to the domain Bone.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)


class MissingAssetItem(QGraphicsRectItem):
    """Placeholder shown when a bone's PNG asset cannot be resolved on disk."""

    def __init__(self, bone_id: str, width: float, height: float, label: str) -> None:
        super().__init__(0, 0, max(width, 8.0), max(height, 8.0))
        self.bone_id = bone_id
        self.setBrush(QBrush(QColor(80, 40, 40, 160)))
        self.setPen(QPen(QColor("#e06666"), 2, Qt.PenStyle.DashLine))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setToolTip(f"Missing PNG asset: {label}")

        text = QGraphicsSimpleTextItem(f"Missing:\n{label}", self)
        text.setBrush(QBrush(QColor("#ffffff")))
        text.setPos(4, 4)


class LayerItem(QGraphicsPixmapItem):
    """Renders one Layer's PNG asset. Purely a view: state comes from sync()."""

    def __init__(self, layer_id: str, pixmap: QPixmap) -> None:
        super().__init__(pixmap)
        self.layer_id = layer_id
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)


class MissingLayerItem(QGraphicsRectItem):
    """Placeholder shown when a layer's PNG asset cannot be resolved on disk."""

    def __init__(self, layer_id: str, width: float, height: float, label: str) -> None:
        super().__init__(0, 0, max(width, 8.0), max(height, 8.0))
        self.layer_id = layer_id
        self.setBrush(QBrush(QColor(40, 40, 80, 160)))
        self.setPen(QPen(QColor("#6699e0"), 2, Qt.PenStyle.DashLine))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setToolTip(f"Missing PNG asset: {label}")

        text = QGraphicsSimpleTextItem(f"Missing:\n{label}", self)
        text.setBrush(QBrush(QColor("#ffffff")))
        text.setPos(4, 4)


class RigRenderer:
    """Owns the Qt items for the current frame's rigs and keeps them in sync.

    Bones are rendered on the "character plane": their pure FK world
    transform (``domain.rig.world_transforms``, camera-unaware) is composed
    with ``domain.camera.character_plane_matrix`` (z_depth=0) so the rig
    pans/zooms exactly in step with the project camera.
    """

    def __init__(self, scene: QGraphicsScene) -> None:
        self._scene = scene
        self._bone_items: dict[str, BoneItem] = {}
        self._missing_items: dict[str, MissingAssetItem] = {}
        self._selection_item: QGraphicsPolygonItem | None = None
        self._rotate_handle_item: RotateHandleItem | None = None
        self._scale_handle_item: ScaleHandleItem | None = None
        self._pivot_marker_item: QGraphicsEllipseItem | None = None
        self._pixmap_cache: dict[str, QPixmap] = {}

        self._frame: Frame | None = None
        self._assets_by_id: dict[str, Asset] = {}
        self._project_dir: Path | None = None
        self._scene_width = 1920.0
        self._scene_height = 1080.0
        self._selected_bone_id: str | None = None

    # -- Sync ---------------------------------------------------------------

    def sync(
        self,
        frame: Frame,
        assets: list[Asset],
        project_dir: Path | None,
        scene_width: float,
        scene_height: float,
    ) -> None:
        """Full rebuild from a frame snapshot.

        Call whenever the bone *set*, the camera, or the scene size may
        have changed: frame switch, rig creation, project load/new, or a
        camera/scene edit.
        """
        self._frame = frame
        self._assets_by_id = {asset.id: asset for asset in assets}
        self._project_dir = project_dir
        self._scene_width = scene_width
        self._scene_height = scene_height

        for item in list(self._bone_items.values()):
            self._scene.removeItem(item)
        for item in list(self._missing_items.values()):
            self._scene.removeItem(item)
        self._bone_items.clear()
        self._missing_items.clear()

        for rig in frame.rigs:
            self._sync_rig(rig)

        if self._selected_bone_id is not None and self._item_for(self._selected_bone_id) is None:
            self._selected_bone_id = None
        self._update_selection_overlay()

    def refresh_transforms(self) -> None:
        """Lightweight pose-only update: same bone set, new x/y/rotation/scale."""
        if self._frame is None:
            return
        for rig in self._frame.rigs:
            transforms = world_transforms(rig)
            for bone in rig.bones:
                self._apply_bone_state(bone, transforms[bone.id])
        self.refresh_handle_positions()

    def _sync_rig(self, rig: Rig) -> None:
        transforms = world_transforms(rig)
        for bone in rig.bones:
            asset = self._assets_by_id.get(bone.asset_id)
            pixmap = _load_pixmap(self._pixmap_cache, asset, self._project_dir)
            if pixmap is None:
                item: QGraphicsItem = MissingAssetItem(
                    bone.id,
                    float(asset.width) if asset is not None else 64.0,
                    float(asset.height) if asset is not None else 64.0,
                    asset.name if asset is not None else bone.asset_id,
                )
                self._missing_items[bone.id] = item  # type: ignore[assignment]
            else:
                item = BoneItem(bone.id, pixmap)
                self._bone_items[bone.id] = item  # type: ignore[assignment]
            self._scene.addItem(item)
            self._apply_bone_state(bone, transforms[bone.id], item)

    def _apply_bone_state(self, bone: Bone, world_matrix: Matrix2D, item: QGraphicsItem | None = None) -> None:
        if item is None:
            item = self._item_for(bone.id)
        if item is None:
            return
        screen_matrix = world_matrix.then(self.camera_screen_matrix())
        item.setTransform(_matrix_to_qtransform(screen_matrix))
        item.setZValue(bone.z_index)
        item.setVisible(bone.visible)
        item.setOpacity(max(0.0, min(1.0, bone.opacity)))

    def _item_for(self, bone_id: str) -> QGraphicsItem | None:
        return self._bone_items.get(bone_id) or self._missing_items.get(bone_id)

    # -- Camera -----------------------------------------------------------

    def camera_screen_matrix(self) -> Matrix2D:
        """The extra transform bones receive on top of their FK world transform.

        Exposed so ``CanvasView`` can correctly convert scene-space mouse
        deltas into bone-local deltas while dragging, accounting for
        ``camera.zoom`` (see ``CanvasView._update_bone_drag``).
        """
        camera = self._frame.camera if self._frame is not None else Camera()
        return character_plane_matrix(camera, self._scene_width, self._scene_height)

    # -- Selection ------------------------------------------------------------

    def bone_id_for_item(self, item: QGraphicsItem | None) -> str | None:
        if isinstance(item, (BoneItem, MissingAssetItem)):
            return item.bone_id
        return None

    def handle_kind_for_item(self, item: QGraphicsItem | None) -> str | None:
        """"rotate"/"scale" if ``item`` is one of the selected bone's manipulation
        handles (see ``graphics_items``'s ``RotateHandleItem``/``ScaleHandleItem``),
        else ``None``. Used by ``CanvasView`` to prioritize handle hit-testing
        over plain bone selection."""
        if isinstance(item, RotateHandleItem):
            return "rotate"
        if isinstance(item, ScaleHandleItem):
            return "scale"
        return None

    def set_selected_bone(self, bone_id: str | None) -> None:
        self._selected_bone_id = bone_id
        self._update_selection_overlay()

    def selected_bone_id(self) -> str | None:
        return self._selected_bone_id

    def _clear_manipulation_items(self) -> None:
        for item in (self._selection_item, self._rotate_handle_item, self._scale_handle_item, self._pivot_marker_item):
            if item is not None:
                self._scene.removeItem(item)
        self._selection_item = None
        self._rotate_handle_item = None
        self._scale_handle_item = None
        self._pivot_marker_item = None

    def _update_selection_overlay(self) -> None:
        self._clear_manipulation_items()
        if self._selected_bone_id is None:
            return
        item = self._item_for(self._selected_bone_id)
        bone, _rig = self.find_bone_and_rig(self._selected_bone_id)
        if item is None or bone is None:
            return

        self._selection_item = _make_selection_outline(item, "#ffd25c")
        self._scene.addItem(self._selection_item)

        local_rect: QRectF = item.boundingRect()

        self._rotate_handle_item = RotateHandleItem(bone.id)
        rotate_local = QPointF(local_rect.width() / 2.0, -max(24.0, local_rect.height() * 0.18))
        self._rotate_handle_item.setPos(item.mapToScene(rotate_local))
        self._scene.addItem(self._rotate_handle_item)

        self._scale_handle_item = ScaleHandleItem(bone.id)
        self._scale_handle_item.setPos(item.mapToScene(QPointF(local_rect.width(), local_rect.height())))
        self._scene.addItem(self._scale_handle_item)

        self._pivot_marker_item = _make_pivot_marker(item.mapToScene(QPointF(bone.pivot_x, bone.pivot_y)))
        self._scene.addItem(self._pivot_marker_item)

    def refresh_handle_positions(self) -> None:
        """Re-position (not rebuild) the selected bone's handles/pivot marker
        after a live pose change (drag in progress, inspector edit) — called
        from ``refresh_transforms()`` so handles track the bone every frame."""
        if self._selected_bone_id is None:
            return
        item = self._item_for(self._selected_bone_id)
        bone, _rig = self.find_bone_and_rig(self._selected_bone_id)
        if item is None or bone is None:
            return
        if self._selection_item is not None:
            self._selection_item.setPolygon(item.mapToScene(item.boundingRect()))
        local_rect: QRectF = item.boundingRect()
        if self._rotate_handle_item is not None:
            rotate_local = QPointF(local_rect.width() / 2.0, -max(24.0, local_rect.height() * 0.18))
            self._rotate_handle_item.setPos(item.mapToScene(rotate_local))
        if self._scale_handle_item is not None:
            self._scale_handle_item.setPos(item.mapToScene(QPointF(local_rect.width(), local_rect.height())))
        if self._pivot_marker_item is not None:
            self._scene.removeItem(self._pivot_marker_item)
            self._pivot_marker_item = _make_pivot_marker(item.mapToScene(QPointF(bone.pivot_x, bone.pivot_y)))
            self._scene.addItem(self._pivot_marker_item)

    # -- Domain lookups used by CanvasView for drag math ------------------------

    def find_bone_and_rig(self, bone_id: str) -> tuple[Bone | None, Rig | None]:
        if self._frame is None:
            return None, None
        for rig in self._frame.rigs:
            bone = find_bone(rig, bone_id)
            if bone is not None:
                return bone, rig
        return None, None

    def parent_world_matrix(self, rig: Rig, bone: Bone) -> Matrix2D:
        if bone.parent_id is None:
            return Matrix2D.identity()
        return world_transforms(rig)[bone.parent_id]


class LayerRenderer:
    """Owns the Qt items for the current frame's layers and keeps them in sync.

    Each layer's screen transform (placement + camera parallax/zoom) is
    computed entirely by ``domain.camera.layer_screen_matrix``; layers are
    selectable (for the inspector) but not draggable in the canvas in this
    milestone — editing happens through the inspector panel.
    """

    def __init__(self, scene: QGraphicsScene) -> None:
        self._scene = scene
        self._items: dict[str, QGraphicsItem] = {}
        self._selection_item: QGraphicsPolygonItem | None = None
        self._pixmap_cache: dict[str, QPixmap] = {}

        self._frame: Frame | None = None
        self._assets_by_id: dict[str, Asset] = {}
        self._project_dir: Path | None = None
        self._scene_width = 1920.0
        self._scene_height = 1080.0
        self._selected_layer_id: str | None = None

    # -- Sync ---------------------------------------------------------------

    def sync(
        self,
        frame: Frame,
        assets: list[Asset],
        project_dir: Path | None,
        scene_width: float,
        scene_height: float,
    ) -> None:
        self._frame = frame
        self._assets_by_id = {asset.id: asset for asset in assets}
        self._project_dir = project_dir
        self._scene_width = scene_width
        self._scene_height = scene_height

        for item in list(self._items.values()):
            self._scene.removeItem(item)
        self._items.clear()

        for layer in frame.layers:
            self._sync_layer(layer)

        if self._selected_layer_id is not None and self._selected_layer_id not in self._items:
            self._selected_layer_id = None
        self._update_selection_overlay()

    def refresh_transforms(self) -> None:
        """Lightweight update: same layer set, new placement/camera state."""
        if self._frame is None:
            return
        for layer in self._frame.layers:
            self._apply_layer_state(layer)
        self._update_selection_overlay()

    def _sync_layer(self, layer: Layer) -> None:
        asset = self._assets_by_id.get(layer.asset_id)
        pixmap = _load_pixmap(self._pixmap_cache, asset, self._project_dir)
        if pixmap is None:
            item: QGraphicsItem = MissingLayerItem(
                layer.id,
                float(asset.width) if asset is not None else 64.0,
                float(asset.height) if asset is not None else 64.0,
                asset.name if asset is not None else layer.asset_id,
            )
        else:
            item = LayerItem(layer.id, pixmap)
        self._items[layer.id] = item
        self._scene.addItem(item)
        self._apply_layer_state(layer, item)

    def _apply_layer_state(self, layer: Layer, item: QGraphicsItem | None = None) -> None:
        if item is None:
            item = self._items.get(layer.id)
        if item is None:
            return
        camera = self._frame.camera if self._frame is not None else Camera()
        screen_matrix = layer_screen_matrix(layer, camera, self._scene_width, self._scene_height)
        item.setTransform(_matrix_to_qtransform(screen_matrix))
        item.setZValue(layer.z_index)
        item.setVisible(layer.visible)
        item.setOpacity(max(0.0, min(1.0, layer.opacity)))

    # -- Selection ------------------------------------------------------------

    def layer_id_for_item(self, item: QGraphicsItem | None) -> str | None:
        if isinstance(item, (LayerItem, MissingLayerItem)):
            return item.layer_id
        return None

    def set_selected_layer(self, layer_id: str | None) -> None:
        self._selected_layer_id = layer_id
        self._update_selection_overlay()

    def selected_layer_id(self) -> str | None:
        return self._selected_layer_id

    def _update_selection_overlay(self) -> None:
        if self._selection_item is not None:
            self._scene.removeItem(self._selection_item)
            self._selection_item = None
        if self._selected_layer_id is None:
            return
        item = self._items.get(self._selected_layer_id)
        if item is None:
            return
        self._selection_item = _make_selection_outline(item, "#7fd8ff")
        self._scene.addItem(self._selection_item)
