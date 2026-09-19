"""Central animation canvas: QGraphicsScene/QGraphicsView with zoom, pan,
rig manipulation (select / move / rotate a single bone), and layer
selection for the inspector.

Two separate "zooms" coexist by design and must never be conflated:

- **Editor navigation zoom** (this class's ``wheelEvent``/``_zoom`` and
  Space+drag/middle-mouse pan): purely a view-side convenience for working
  on the canvas. It only ever calls ``QGraphicsView.scale()``/scrollbar
  changes — it never touches the domain model.
- **Project camera zoom** (``Frame.camera.zoom``, edited via the inspector
  panel): a domain value, snapshot per frame, that scales the *rendered
  scene itself* (rig + layers) around the output frame's center — see
  ``domain.camera``. It is applied by ``RigRenderer``/``LayerRenderer`` as
  part of each item's ``QTransform``, entirely independent of this view's
  own transform.

The canvas never treats Qt item state as authoritative: bone/layer
selection and pose edits always read/write the domain dataclasses directly
(via ``RigRenderer``/``LayerRenderer``, see ``graphics_items.py``), and
rendering is a one-way "read model, draw" sync.
"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene, QGraphicsView, QWidget

from pivotcut.domain import commands
from pivotcut.domain.assets import Asset
from pivotcut.domain.models import Frame, SceneSettings
from pivotcut.domain.rig import Bone, Matrix2D
from pivotcut.ui.graphics_items import LayerRenderer, RigRenderer

_MIN_ZOOM = 0.05
_MAX_ZOOM = 20.0
_WHEEL_ZOOM_STEP = 1.0015
_ROTATE_DEGREES_PER_PIXEL = 0.5
_OUTPUT_FRAME_Z = 9000


class CanvasView(QGraphicsView):
    """Scene canvas with trackpad/wheel zoom, Space+drag/middle-mouse pan,
    click-select + drag-move / shift-drag-rotate on rig bones, and
    click-select on layers (for the inspector)."""

    #: Emitted when Space is pressed and released *without* an intervening
    #: drag while the canvas has focus — MainWindow treats this as a
    #: Play/Stop toggle. A Space+drag instead pans the editor and never
    #: emits this signal (see keyPressEvent/keyReleaseEvent/mouseMoveEvent).
    playback_requested = Signal()

    #: Emitted whenever the selected bone or layer changes (including
    #: deselection), so MainWindow can refresh the inspector panel.
    selection_changed = Signal()

    #: Emitted once when a bone move/rotate drag *ends* (never per mouse
    #: move) with a real change — (mode, rig_id, bone_id, before, after) —
    #: so MainWindow can build exactly one undoable TransformBoneCommand per
    #: drag gesture instead of one per pixel of movement.
    bone_transform_committed = Signal(str, str, str, object, object)

    def __init__(self, scene_settings: SceneSettings, parent: QWidget | None = None) -> None:
        scene = QGraphicsScene(0, 0, scene_settings.width, scene_settings.height)
        super().__init__(scene, parent)

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setBackgroundBrush(QBrush(QColor("#3a3a3a")))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWhatsThis(
            "The animation canvas for the current Pose. Click a Part or layer to select it (its "
            "fields then appear in the Inspector, on the right).\n\n"
            "- Drag a selected Part to move it.\n"
            "- Shift+drag a selected Part to rotate it around its pivot.\n"
            "- Once selected, drag its round handle to rotate or its square handle to scale.\n"
            "- Scroll/pinch to zoom the editor view — a viewing convenience only, it never "
            "changes the project (see the Inspector's camera Zoom for the real, exported one).\n"
            "- Space+drag or middle-mouse-drag to pan the view; a plain Space press toggles "
            "Play/Stop.\n"
            "- The dashed rectangle marks the exact area Export renders."
        )

        self._zoom = 1.0
        self._space_held = False
        self._space_drag_occurred = False
        self._panning = False
        self._pan_last_pos: QPointF | None = None
        self._playback_active = False

        self._background_item = self._build_background(scene_settings)
        self.scene().addItem(self._background_item)
        self._output_frame_item = self._build_output_frame_indicator(scene_settings)
        self.scene().addItem(self._output_frame_item)

        # Layers render first (they include far-background planes that must
        # sit visually behind the rig by default); the rig renderer is
        # created second only for readability, actual stacking is z_index.
        self._layer_renderer = LayerRenderer(self.scene())
        self._rig_renderer = RigRenderer(self.scene())

        self._drag_bone_id: str | None = None
        self._drag_rig_id: str | None = None
        self._drag_mode: str | None = None  # "move" | "rotate" | "rotate-handle" | "scale-handle"
        self._drag_start_scene_pos = QPointF()
        self._drag_start_bone_x = 0.0
        self._drag_start_bone_y = 0.0
        self._drag_start_bone_rotation = 0.0
        self._drag_start_bone_scale_x = 1.0
        self._drag_start_bone_scale_y = 1.0
        self._drag_before_snapshot: Bone | None = None
        # Cached once at the start of a rotate-handle/scale-handle drag: the
        # inverse of (parent world transform -> camera screen), so every
        # mouse-move only needs one matrix-vector multiply — exactly the
        # same "capture at drag start, invert once" technique already used
        # for plain move-drag below (see _update_bone_drag).
        self._drag_parent_inverse: Matrix2D | None = None
        self._drag_anchor_local = (0.0, 0.0)
        self._drag_start_angle = 0.0
        self._drag_start_distance = 1.0

    def _build_background(self, scene_settings: SceneSettings) -> QGraphicsRectItem:
        rect = QGraphicsRectItem(0, 0, scene_settings.width, scene_settings.height)
        rect.setBrush(QBrush(QColor(scene_settings.background_color)))
        rect.setPen(Qt.PenStyle.NoPen)
        rect.setZValue(-1000)
        return rect

    def _build_output_frame_indicator(self, scene_settings: SceneSettings) -> QGraphicsRectItem:
        """A fixed guide outline marking the 1920x1080 (etc.) render output area.

        Deliberately NOT affected by the project camera: bones/layers shift
        underneath it as the camera pans/zooms (see module docstring), so
        whatever ends up inside this rectangle is what a future export
        would actually capture.
        """
        rect = QGraphicsRectItem(0, 0, scene_settings.width, scene_settings.height)
        rect.setBrush(Qt.BrushStyle.NoBrush)
        rect.setPen(QPen(QColor("#ffffff"), 2, Qt.PenStyle.DashLine))
        rect.setZValue(_OUTPUT_FRAME_Z)
        return rect

    def set_playback_active(self, active: bool) -> None:
        """During playback, bone/layer selection and drag-editing are locked.

        The canvas keeps rendering (frame changes still call ``sync_scene``)
        so the animation stays visible, but clicks/drags in the scene must
        never mutate the rig/layer while a pose is only being *played*, not
        edited.
        """
        self._playback_active = active
        if active:
            self._clear_selection()

    def apply_scene_settings(self, scene_settings: SceneSettings) -> None:
        """Resize the scene rect, background and output-frame guide."""
        self.scene().setSceneRect(0, 0, scene_settings.width, scene_settings.height)
        self._background_item.setRect(0, 0, scene_settings.width, scene_settings.height)
        self._background_item.setBrush(QBrush(QColor(scene_settings.background_color)))
        self._output_frame_item.setRect(0, 0, scene_settings.width, scene_settings.height)

    # -- Rig/layer rendering sync -------------------------------------------

    def sync_scene(
        self, frame: Frame, assets: list[Asset], project_dir: Path | None, scene_width: float, scene_height: float
    ) -> None:
        """Rebuild the rig + layer rendering from a frame snapshot.

        Call on frame switch, rig/layer creation, project load/new, and
        after any camera or scene-size edit.
        """
        self._layer_renderer.sync(frame, assets, project_dir, scene_width, scene_height)
        self._rig_renderer.sync(frame, assets, project_dir, scene_width, scene_height)

    # -- Selection (for the inspector) ---------------------------------------

    def selected_layer_id(self) -> str | None:
        return self._layer_renderer.selected_layer_id()

    def selected_bone_id(self) -> str | None:
        return self._rig_renderer.selected_bone_id()

    def select_bone(self, bone_id: str | None) -> None:
        """Programmatically select a bone (e.g. right after Quick Add/Build
        Character creates it), so its selection box/handles appear
        immediately without the user having to click it first."""
        self._select_bone(bone_id)

    def clear_selection(self) -> None:
        self._clear_selection()

    def selected_rig_id(self) -> str | None:
        """The id of the Rig instance the selected bone (if any) belongs to.

        Used by Characters menu actions ("Save Selected Rig as Template…",
        "Edit Selected Character Rig…") to know which of a frame's
        (possibly several, since Milestone 6A) rigs the user means.
        """
        bone_id = self._rig_renderer.selected_bone_id()
        if bone_id is None:
            return None
        _bone, rig = self._rig_renderer.find_bone_and_rig(bone_id)
        return rig.id if rig is not None else None

    def _select_bone(self, bone_id: str | None) -> None:
        self._rig_renderer.set_selected_bone(bone_id)
        self._layer_renderer.set_selected_layer(None)
        self.selection_changed.emit()

    def _select_layer(self, layer_id: str | None) -> None:
        self._layer_renderer.set_selected_layer(layer_id)
        self._rig_renderer.set_selected_bone(None)
        self.selection_changed.emit()

    def _clear_selection(self) -> None:
        self._rig_renderer.set_selected_bone(None)
        self._layer_renderer.set_selected_layer(None)
        self.selection_changed.emit()

    # -- Zoom (editor navigation only — see module docstring) -----------------

    def wheelEvent(self, event: QWheelEvent) -> None:
        angle = event.angleDelta().y()
        if angle == 0:
            super().wheelEvent(event)
            return

        factor = _WHEEL_ZOOM_STEP**angle
        new_zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, self._zoom * factor))
        applied_factor = new_zoom / self._zoom
        self._zoom = new_zoom
        self.scale(applied_factor, applied_factor)
        event.accept()

    # -- Pan: Space + drag ------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: ANN001 - Qt event type
        if event.key() == Qt.Key.Key_Escape and not event.isAutoRepeat() and self._drag_bone_id is not None:
            # Cancel the in-progress move/rotate/scale gesture and restore
            # the bone exactly as it was before this drag started — no
            # command is ever built for a cancelled gesture.
            self._cancel_bone_drag()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            self._space_drag_occurred = False
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: ANN001 - Qt event type
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            if not self._space_drag_occurred:
                self.playback_requested.emit()
            self._space_drag_occurred = False
        super().keyReleaseEvent(event)

    # -- Mouse: middle-button pan, left-button bone/layer select/drag ---------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_last_pos = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and not self._space_held and not self._playback_active:
            view_pos = event.position().toPoint()

            # Rotate/scale handles (only ever present for the currently
            # selected bone) take priority over everything else: they sit on
            # top in z-order but bone_id_for_item()/layer_id_for_item() both
            # return None for them, so without this dedicated check the loop
            # below would just skip past them to whatever bone/layer is
            # underneath.
            for candidate in self.items(view_pos):
                handle_kind = self._rig_renderer.handle_kind_for_item(candidate)
                if handle_kind is not None:
                    self._begin_handle_drag(handle_kind, self.mapToScene(view_pos))
                    event.accept()
                    return

            # Use items() (topmost-first list), not itemAt(), and skip past
            # non-bone/layer items such as selection outlines (drawn on top
            # of everything), the output-frame guide, or the background.
            bone_id: str | None = None
            layer_id: str | None = None
            for candidate in self.items(view_pos):
                bone_id = self._rig_renderer.bone_id_for_item(candidate)
                if bone_id is not None:
                    break
                layer_id = self._layer_renderer.layer_id_for_item(candidate)
                if layer_id is not None:
                    break

            if bone_id is not None:
                shift_held = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self._begin_bone_drag(bone_id, self.mapToScene(view_pos), shift_held)
                event.accept()
                return
            if layer_id is not None:
                self._select_layer(layer_id)
                event.accept()
                return
            self._clear_selection()

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._space_held:
            # Any mouse movement while Space is held is a pan drag (Qt's
            # built-in ScrollHandDrag handles the actual scrolling via
            # super().mouseMoveEvent below) — mark it so keyReleaseEvent
            # does NOT also toggle playback for this Space press.
            self._space_drag_occurred = True

        if self._panning and self._pan_last_pos is not None:
            pos = event.position().toPoint()
            delta = pos - self._pan_last_pos
            self._pan_last_pos = pos
            h_bar = self.horizontalScrollBar()
            v_bar = self.verticalScrollBar()
            h_bar.setValue(h_bar.value() - delta.x())
            v_bar.setValue(v_bar.value() - delta.y())
            event.accept()
            return

        if self._drag_bone_id is not None:
            self._update_bone_drag(self.mapToScene(event.position().toPoint()))
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self._pan_last_pos = None
            self.unsetCursor()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._drag_bone_id is not None:
            self._commit_bone_drag()
            self._drag_bone_id = None
            self._drag_rig_id = None
            self._drag_mode = None
            self._drag_before_snapshot = None
            event.accept()
            return

        super().mouseReleaseEvent(event)

    # -- Bone selection / drag -----------------------------------------------

    def _begin_bone_drag(self, bone_id: str, scene_pos: QPointF, shift_held: bool) -> None:
        self._select_bone(bone_id)
        bone, rig = self._rig_renderer.find_bone_and_rig(bone_id)
        if bone is None or rig is None:
            return
        self._drag_bone_id = bone_id
        self._drag_rig_id = rig.id
        self._drag_mode = "rotate" if shift_held else "move"
        self._drag_start_scene_pos = scene_pos
        self._drag_start_bone_x = bone.x
        self._drag_start_bone_y = bone.y
        self._drag_start_bone_rotation = bone.rotation
        self._drag_before_snapshot = commands.snapshot_bone(bone)

    def _begin_handle_drag(self, kind: str, scene_pos: QPointF) -> None:
        """Start a rotate-handle or scale-handle drag on the *currently
        selected* bone (handles only ever exist for it — see
        RigRenderer._update_selection_overlay)."""
        bone_id = self._rig_renderer.selected_bone_id()
        if bone_id is None:
            return
        bone, rig = self._rig_renderer.find_bone_and_rig(bone_id)
        if bone is None or rig is None:
            return

        self._drag_bone_id = bone_id
        self._drag_rig_id = rig.id
        self._drag_mode = "rotate-handle" if kind == "rotate" else "scale-handle"
        self._drag_before_snapshot = commands.snapshot_bone(bone)
        self._drag_start_bone_rotation = bone.rotation
        self._drag_start_bone_scale_x = bone.scale_x
        self._drag_start_bone_scale_y = bone.scale_y

        # Rotation/scale happen in the bone's *parent*-local space (same
        # space local_matrix rotates/scales the bone's own transform in),
        # so mouse deltas must be converted through the parent's world
        # transform composed with the camera, captured once here exactly
        # like a move-drag captures it in _begin_bone_drag/_update_bone_drag.
        parent_world = self._rig_renderer.parent_world_matrix(rig, bone)
        total_matrix = parent_world.then(self._rig_renderer.camera_screen_matrix())
        self._drag_parent_inverse = total_matrix.inverse()
        self._drag_anchor_local = (bone.x + bone.pivot_x, bone.y + bone.pivot_y)

        local_x, local_y = self._drag_parent_inverse.apply_point(scene_pos.x(), scene_pos.y())
        anchor_x, anchor_y = self._drag_anchor_local
        self._drag_start_angle = math.atan2(local_y - anchor_y, local_x - anchor_x)
        self._drag_start_distance = max(1e-6, math.hypot(local_x - anchor_x, local_y - anchor_y))

    def _cancel_bone_drag(self) -> None:
        """Escape mid-gesture: restore the bone exactly, build no command."""
        if self._drag_bone_id is not None and self._drag_before_snapshot is not None:
            bone, _rig = self._rig_renderer.find_bone_and_rig(self._drag_bone_id)
            if bone is not None:
                snapshot = self._drag_before_snapshot
                bone.x, bone.y = snapshot.x, snapshot.y
                bone.rotation = snapshot.rotation
                bone.scale_x, bone.scale_y = snapshot.scale_x, snapshot.scale_y
                self._rig_renderer.refresh_transforms()
        self._drag_bone_id = None
        self._drag_rig_id = None
        self._drag_mode = None
        self._drag_before_snapshot = None
        self._drag_parent_inverse = None

    def _commit_bone_drag(self) -> None:
        """Emit exactly one bone_transform_committed for the whole drag gesture that just ended."""
        if self._drag_bone_id is None or self._drag_rig_id is None or self._drag_before_snapshot is None:
            return
        bone, _rig = self._rig_renderer.find_bone_and_rig(self._drag_bone_id)
        if bone is None:
            return
        after_snapshot = commands.snapshot_bone(bone)
        if after_snapshot == self._drag_before_snapshot:
            return  # released without actually moving/rotating/scaling: nothing to record
        mode = "rotate" if self._drag_mode in ("rotate", "rotate-handle") else self._drag_mode
        mode = "scale" if self._drag_mode == "scale-handle" else mode
        self.bone_transform_committed.emit(
            mode or "move", self._drag_rig_id, self._drag_bone_id, self._drag_before_snapshot, after_snapshot
        )

    def _update_bone_drag(self, scene_pos: QPointF) -> None:
        if self._drag_bone_id is None:
            return
        bone, rig = self._rig_renderer.find_bone_and_rig(self._drag_bone_id)
        if bone is None or rig is None:
            return

        dx_scene = scene_pos.x() - self._drag_start_scene_pos.x()
        dy_scene = scene_pos.y() - self._drag_start_scene_pos.y()

        if self._drag_mode == "move":
            # Convert the scene-space mouse delta into the bone's parent's
            # local space. This must account for BOTH the parent's own FK
            # world transform AND the camera's screen transform (camera.zoom
            # scales the rendered bone, so it also scales how far it visibly
            # moves per pixel of mouse movement) — see camera_screen_matrix().
            parent_world: Matrix2D = self._rig_renderer.parent_world_matrix(rig, bone)
            total_matrix = parent_world.then(self._rig_renderer.camera_screen_matrix())
            dx_local, dy_local = total_matrix.inverse().apply_vector(dx_scene, dy_scene)
            bone.x = self._drag_start_bone_x + dx_local
            bone.y = self._drag_start_bone_y + dy_local
        elif self._drag_mode == "rotate":
            # Shift + horizontal drag rotates; vertical movement is ignored.
            bone.rotation = self._drag_start_bone_rotation + dx_scene * _ROTATE_DEGREES_PER_PIXEL
        elif self._drag_mode == "rotate-handle" and self._drag_parent_inverse is not None:
            # Angle-based rotation around the pivot, computed in the
            # parent's local space (see _begin_handle_drag) — correct even
            # under parent rotation/non-uniform scale, unlike the linear
            # dx-based shift-drag above.
            local_x, local_y = self._drag_parent_inverse.apply_point(scene_pos.x(), scene_pos.y())
            anchor_x, anchor_y = self._drag_anchor_local
            angle = math.atan2(local_y - anchor_y, local_x - anchor_x)
            bone.rotation = self._drag_start_bone_rotation + math.degrees(angle - self._drag_start_angle)
        elif self._drag_mode == "scale-handle" and self._drag_parent_inverse is not None:
            # Uniform scale from the distance (in parent-local space) between
            # the pivot and the mouse, relative to that same distance at
            # drag start.
            local_x, local_y = self._drag_parent_inverse.apply_point(scene_pos.x(), scene_pos.y())
            anchor_x, anchor_y = self._drag_anchor_local
            distance = math.hypot(local_x - anchor_x, local_y - anchor_y)
            factor = distance / self._drag_start_distance
            bone.scale_x = max(0.02, self._drag_start_bone_scale_x * factor)
            bone.scale_y = max(0.02, self._drag_start_bone_scale_y * factor)

        self._rig_renderer.refresh_transforms()
