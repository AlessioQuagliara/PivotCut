"""Right-side "Inspector" panel: contextual editor for a selected rig Part
(bone), a selected layer, or the current frame's camera when nothing is
selected.

Every field edit writes straight into the current frame's ``Bone``/
``Layer``/``Camera`` dataclass and emits ``changed``, so ``MainWindow`` can
resync the canvas from the single source of truth (the domain model) — this
panel holds no authoritative state of its own, only Qt widgets reflecting
it. Structural edits (reparenting a Part) are *not* handled here: they need
cycle validation and a whole-bone-list ``EditRigStructureCommand``, so they
go through the dedicated ``bone_reparent_requested`` signal instead of the
generic field-snapshot commit path — see ``app/main_window.py``.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pivotcut.domain.assets import Asset
from pivotcut.domain.camera import MAX_ZOOM, MIN_ZOOM, Camera
from pivotcut.domain.layer import Layer
from pivotcut.domain.models import Frame
from pivotcut.domain.rig import Bone, set_attach_point, set_pivot_preserving_attach_point

_WIDE_RANGE = 100_000.0
_MIN_SCALE = 0.01
_MAX_SCALE = 100.0
_MAX_Z_DEPTH = 1000.0


class InspectorPanel(QWidget):
    #: Emitted after any field edit (bone, layer or camera) mutates the current frame.
    changed = Signal()

    #: Emitted once per completed edit "session" (editingFinished/focus-out,
    #: or an atomic single action like a checkbox toggle or Reset Camera) —
    #: never per keystroke/spin-tick — with (frame_id, layer_id, before,
    #: after) so MainWindow can build exactly one undoable EditLayerCommand.
    layer_edit_committed = Signal(str, str, object, object)

    #: Same idea for a selected rig Part: (frame_id, rig_id, bone_id, before,
    #: after) — MainWindow builds one TransformBoneCommand from it, exactly
    #: like a canvas move/rotate/scale drag.
    bone_edit_committed = Signal(str, str, str, object, object)

    #: The Parent combo is a *structural* edit, not a plain field snapshot
    #: (needs cycle validation across the whole rig) — MainWindow handles it
    #: separately via EditRigStructureCommand: (frame_id, rig_id, bone_id, new_parent_id).
    bone_reparent_requested = Signal(str, str, str, object)

    #: Same idea for the frame camera: (frame_id, before, after).
    camera_edit_committed = Signal(str, object, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frame: Frame | None = None
        self._assets: list[Asset] = []
        self._selected_layer_id: str | None = None
        self._selected_bone_id: str | None = None
        self._selected_rig_id: str | None = None
        self._layer_snapshot_before: Layer | None = None
        self._bone_snapshot_before: Bone | None = None
        self._camera_snapshot_before: Camera | None = None
        self._build_ui()
        self.setWhatsThis(
            "The Inspector: a contextual editor for whatever is selected. Select a Part on the "
            "canvas to edit its transform, select a layer to edit that layer, or select nothing "
            "to edit the current Pose's camera instead. Every edit here applies to this Pose "
            "only — poses are keyframed one by one, never interpolated."
        )

    # -- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        title = QLabel("Inspector")
        bold_font = title.font()
        bold_font.setBold(True)
        title.setFont(bold_font)
        outer.addWidget(title)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        self._bone_page = self._build_bone_page()
        self._layer_page = self._build_layer_page()
        self._camera_page = self._build_camera_page()
        self._stack.addWidget(self._bone_page)
        self._stack.addWidget(self._layer_page)
        self._stack.addWidget(self._camera_page)

    def _make_double_spin(self, minimum: float, maximum: float, step: float = 1.0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setSingleStep(step)
        return spin

    def _build_bone_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        form = QFormLayout()
        outer.addLayout(form)

        self._bone_name_edit = QLineEdit()
        self._bone_name_edit.setWhatsThis(
            "This Part's display name — shown in the Character Rig Builder hierarchy and in "
            "menus. Purely cosmetic, no effect on the pose."
        )
        self._bone_name_edit.editingFinished.connect(self._on_bone_name_changed)
        form.addRow("Name", self._bone_name_edit)

        self._bone_parent_combo = QComboBox()
        self._bone_parent_combo.setWhatsThis(
            "Reattaches this Part to a different parent within the same character — a "
            "structural change, validated so it can't create a cycle. Disabled for the Root "
            "Part, which has no parent."
        )
        self._bone_parent_combo.currentIndexChanged.connect(self._on_bone_parent_combo_changed)
        form.addRow("Parent", self._bone_parent_combo)

        self._bone_x_spin = self._make_double_spin(-_WIDE_RANGE, _WIDE_RANGE)
        self._bone_y_spin = self._make_double_spin(-_WIDE_RANGE, _WIDE_RANGE)
        for spin in (self._bone_x_spin, self._bone_y_spin):
            spin.setWhatsThis(
                "World position of the character's Root Part. Only editable on the Root — "
                "every other Part is positioned relative to its parent via its Attach point "
                "instead."
            )
        form.addRow("X (world, root only)", self._bone_x_spin)
        form.addRow("Y (world, root only)", self._bone_y_spin)

        self._bone_rotation_spin = self._make_double_spin(-3600.0, 3600.0)
        self._bone_rotation_spin.setWhatsThis(
            "Rotation of this Part around its own Pivot, in degrees, relative to its parent's "
            "current orientation."
        )
        self._bone_scale_x_spin = self._make_double_spin(_MIN_SCALE, _MAX_SCALE, step=0.1)
        self._bone_scale_y_spin = self._make_double_spin(_MIN_SCALE, _MAX_SCALE, step=0.1)
        for spin in (self._bone_scale_x_spin, self._bone_scale_y_spin):
            spin.setWhatsThis(
                "Scale of this Part relative to its original PNG size (1.0 = original size). "
                "Combines with its parent's scale for the final on-screen size."
            )
        form.addRow("Rotation", self._bone_rotation_spin)
        form.addRow("Scale X", self._bone_scale_x_spin)
        form.addRow("Scale Y", self._bone_scale_y_spin)

        self._bone_pivot_x_spin = self._make_double_spin(-_WIDE_RANGE, _WIDE_RANGE)
        self._bone_pivot_y_spin = self._make_double_spin(-_WIDE_RANGE, _WIDE_RANGE)
        for spin in (self._bone_pivot_x_spin, self._bone_pivot_y_spin):
            spin.setWhatsThis(
                "The point (in the PNG's own pixel coordinates) this Part rotates and scales "
                "around. On a non-root Part, moving the Pivot keeps it visually anchored at its "
                "current Attach point — use it to fix where an image \"hinges\"."
            )
        form.addRow("Pivot X", self._bone_pivot_x_spin)
        form.addRow("Pivot Y", self._bone_pivot_y_spin)

        self._bone_attach_x_spin = self._make_double_spin(-_WIDE_RANGE, _WIDE_RANGE)
        self._bone_attach_y_spin = self._make_double_spin(-_WIDE_RANGE, _WIDE_RANGE)
        for spin in (self._bone_attach_x_spin, self._bone_attach_y_spin):
            spin.setWhatsThis(
                "Where this Part is attached to its parent, in the parent's own local space — "
                "this is what makes a child Part follow its parent's pose. Only meaningful for "
                "non-root Parts."
            )
        form.addRow("Attach X (parent space, child only)", self._bone_attach_x_spin)
        form.addRow("Attach Y (parent space, child only)", self._bone_attach_y_spin)

        self._bone_z_index_spin = QSpinBox()
        self._bone_z_index_spin.setRange(-100_000, 100_000)
        self._bone_z_index_spin.setWhatsThis(
            "Stacking order: higher values draw in front of lower ones. Use Bring Forward/Send "
            "Backward (Characters menu, or right on the canvas) for quick ±1 nudges."
        )
        form.addRow("Z Index", self._bone_z_index_spin)

        self._bone_opacity_spin = self._make_double_spin(0.0, 1.0, step=0.05)
        self._bone_opacity_spin.setWhatsThis("0 = fully transparent, 1 = fully opaque.")
        form.addRow("Opacity", self._bone_opacity_spin)

        self._bone_visible_check = QCheckBox()
        self._bone_visible_check.setWhatsThis("Hides this Part from the canvas and from export, without deleting it.")
        form.addRow("Visible", self._bone_visible_check)

        for spin in (
            self._bone_x_spin,
            self._bone_y_spin,
            self._bone_rotation_spin,
            self._bone_scale_x_spin,
            self._bone_scale_y_spin,
            self._bone_z_index_spin,
            self._bone_opacity_spin,
        ):
            spin.valueChanged.connect(self._on_bone_field_changed)
            spin.editingFinished.connect(self._commit_bone_edit)
        for spin in (self._bone_pivot_x_spin, self._bone_pivot_y_spin):
            spin.valueChanged.connect(self._on_bone_pivot_changed)
            spin.editingFinished.connect(self._commit_bone_edit)
        for spin in (self._bone_attach_x_spin, self._bone_attach_y_spin):
            spin.valueChanged.connect(self._on_bone_attach_changed)
            spin.editingFinished.connect(self._commit_bone_edit)
        self._bone_visible_check.stateChanged.connect(self._on_bone_field_changed)
        self._bone_visible_check.stateChanged.connect(lambda *_args: self._commit_bone_edit())

        info_label = QLabel("Asset")
        bold = info_label.font()
        bold.setBold(True)
        info_label.setFont(bold)
        outer.addWidget(info_label)

        info_form = QFormLayout()
        self._bone_asset_name_label = QLabel("—")
        self._bone_original_size_label = QLabel("—")
        self._bone_effective_size_label = QLabel("—")
        info_form.addRow("File", self._bone_asset_name_label)
        info_form.addRow("Original size", self._bone_original_size_label)
        info_form.addRow("Effective size", self._bone_effective_size_label)
        outer.addLayout(info_form)
        outer.addStretch(1)

        return page

    def _build_layer_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self._layer_name_edit = QLineEdit()
        self._layer_name_edit.setWhatsThis("This layer's display name. Purely cosmetic.")
        self._layer_name_edit.editingFinished.connect(self._on_layer_name_changed)
        form.addRow("Name", self._layer_name_edit)

        self._layer_x_spin = self._make_double_spin(-_WIDE_RANGE, _WIDE_RANGE)
        self._layer_y_spin = self._make_double_spin(-_WIDE_RANGE, _WIDE_RANGE)
        for spin in (self._layer_x_spin, self._layer_y_spin):
            spin.setWhatsThis("Position of this layer in the scene.")
        self._layer_rotation_spin = self._make_double_spin(-360.0, 360.0)
        self._layer_rotation_spin.setWhatsThis("Rotation of this layer around its Pivot, in degrees.")
        self._layer_scale_x_spin = self._make_double_spin(_MIN_SCALE, _MAX_SCALE, step=0.1)
        self._layer_scale_y_spin = self._make_double_spin(_MIN_SCALE, _MAX_SCALE, step=0.1)
        for spin in (self._layer_scale_x_spin, self._layer_scale_y_spin):
            spin.setWhatsThis("Scale of this layer relative to its original PNG size (1.0 = original size).")
        self._layer_pivot_x_spin = self._make_double_spin(-_WIDE_RANGE, _WIDE_RANGE)
        self._layer_pivot_y_spin = self._make_double_spin(-_WIDE_RANGE, _WIDE_RANGE)
        for spin in (self._layer_pivot_x_spin, self._layer_pivot_y_spin):
            spin.setWhatsThis("Point this layer rotates and scales around, in the layer's own pixel coordinates.")
        self._layer_z_depth_spin = self._make_double_spin(0.0, _MAX_Z_DEPTH, step=0.5)
        self._layer_z_depth_spin.setWhatsThis(
            "Controls how much this layer shifts relative to the camera for the 2.5D parallax "
            "effect — 0 moves exactly with the camera (feels close); a higher value moves less "
            "(feels farther away). Set the layer's overall depth plane (Background/Midground/"
            "Foreground) from the Assets panel when creating it."
        )
        self._layer_opacity_spin = self._make_double_spin(0.0, 1.0, step=0.05)
        self._layer_opacity_spin.setWhatsThis("0 = fully transparent, 1 = fully opaque.")

        self._layer_z_index_spin = QSpinBox()
        self._layer_z_index_spin.setRange(-100_000, 100_000)
        self._layer_z_index_spin.setWhatsThis(
            "Stacking order among layers and Parts — higher values draw in front."
        )

        self._layer_visible_check = QCheckBox()
        self._layer_visible_check.setWhatsThis("Hides this layer from the canvas and from export, without deleting it.")

        form.addRow("X", self._layer_x_spin)
        form.addRow("Y", self._layer_y_spin)
        form.addRow("Rotation", self._layer_rotation_spin)
        form.addRow("Scale X", self._layer_scale_x_spin)
        form.addRow("Scale Y", self._layer_scale_y_spin)
        form.addRow("Pivot X", self._layer_pivot_x_spin)
        form.addRow("Pivot Y", self._layer_pivot_y_spin)
        form.addRow("Z Index", self._layer_z_index_spin)
        form.addRow("Z Depth", self._layer_z_depth_spin)
        form.addRow("Opacity", self._layer_opacity_spin)
        form.addRow("Visible", self._layer_visible_check)

        for spin in (
            self._layer_x_spin,
            self._layer_y_spin,
            self._layer_rotation_spin,
            self._layer_scale_x_spin,
            self._layer_scale_y_spin,
            self._layer_pivot_x_spin,
            self._layer_pivot_y_spin,
            self._layer_z_depth_spin,
            self._layer_opacity_spin,
        ):
            spin.valueChanged.connect(self._on_layer_field_changed)
            spin.editingFinished.connect(self._commit_layer_edit)
        self._layer_z_index_spin.valueChanged.connect(self._on_layer_field_changed)
        self._layer_z_index_spin.editingFinished.connect(self._commit_layer_edit)
        # A checkbox toggle is already one atomic action (no partial-typing
        # concept), so it commits immediately rather than waiting for an
        # editingFinished-style signal that QCheckBox doesn't have.
        self._layer_visible_check.stateChanged.connect(self._on_layer_field_changed)
        self._layer_visible_check.stateChanged.connect(lambda *_args: self._commit_layer_edit())

        return page

    def _build_camera_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        label = QLabel("Camera")
        layout.addWidget(label)

        form = QFormLayout()
        self._camera_x_spin = self._make_double_spin(-_WIDE_RANGE, _WIDE_RANGE)
        self._camera_y_spin = self._make_double_spin(-_WIDE_RANGE, _WIDE_RANGE)
        for spin in (self._camera_x_spin, self._camera_y_spin):
            spin.setWhatsThis(
                "Position of the virtual camera for this Pose only — every Pose has its own "
                "camera position, set individually rather than interpolated between Poses."
            )
        self._camera_zoom_spin = self._make_double_spin(MIN_ZOOM, MAX_ZOOM, step=0.1)
        self._camera_zoom_spin.setWhatsThis(
            "Zoom of the virtual camera for this Pose (1.0 = normal). This is the project's "
            "actual camera zoom, rendered into the export — separate from scrolling/pinching on "
            "the canvas, which only changes your editor view and is never exported."
        )
        form.addRow("X", self._camera_x_spin)
        form.addRow("Y", self._camera_y_spin)
        form.addRow("Zoom", self._camera_zoom_spin)
        layout.addLayout(form)

        for spin in (self._camera_x_spin, self._camera_y_spin, self._camera_zoom_spin):
            spin.valueChanged.connect(self._on_camera_field_changed)
            spin.editingFinished.connect(self._commit_camera_edit)

        reset_btn = QPushButton("Reset Camera")
        reset_btn.setToolTip("Resets x=0, y=0, zoom=1.0 for the current frame's camera only.")
        reset_btn.clicked.connect(self._on_reset_camera_clicked)
        layout.addWidget(reset_btn)
        layout.addStretch(1)

        return page

    # -- Binding --------------------------------------------------------------

    def set_frame(self, frame: Frame) -> None:
        self._frame = frame
        self.refresh()

    def set_assets(self, assets: list[Asset]) -> None:
        self._assets = assets

    def set_selected_layer(self, layer_id: str | None) -> None:
        self._selected_layer_id = layer_id
        self.refresh()

    def set_selected_bone(self, bone_id: str | None, rig_id: str | None) -> None:
        self._selected_bone_id = bone_id
        self._selected_rig_id = rig_id
        self.refresh()

    def refresh(self) -> None:
        bone = self._find_selected_bone()
        layer = self._find_selected_layer()
        if bone is not None:
            self._stack.setCurrentWidget(self._bone_page)
            self._populate_bone_fields(bone)
        elif layer is not None:
            self._stack.setCurrentWidget(self._layer_page)
            self._populate_layer_fields(layer)
        else:
            self._stack.setCurrentWidget(self._camera_page)
            self._populate_camera_fields()

    def _find_selected_layer(self) -> Layer | None:
        if self._frame is None or self._selected_layer_id is None:
            return None
        for layer in self._frame.layers:
            if layer.id == self._selected_layer_id:
                return layer
        return None

    def _find_selected_bone(self) -> Bone | None:
        if self._frame is None or self._selected_bone_id is None or self._selected_rig_id is None:
            return None
        for rig in self._frame.rigs:
            if rig.id == self._selected_rig_id:
                for bone in rig.bones:
                    if bone.id == self._selected_bone_id:
                        return bone
        return None

    def _bone_widgets(self) -> tuple[QWidget, ...]:
        return (
            self._bone_x_spin,
            self._bone_y_spin,
            self._bone_rotation_spin,
            self._bone_scale_x_spin,
            self._bone_scale_y_spin,
            self._bone_pivot_x_spin,
            self._bone_pivot_y_spin,
            self._bone_attach_x_spin,
            self._bone_attach_y_spin,
            self._bone_z_index_spin,
            self._bone_opacity_spin,
            self._bone_visible_check,
        )

    def _current_rig_bones(self) -> list[Bone]:
        if self._frame is None or self._selected_rig_id is None:
            return []
        for rig in self._frame.rigs:
            if rig.id == self._selected_rig_id:
                return rig.bones
        return []

    def _populate_bone_fields(self, bone: Bone) -> None:
        bones = self._current_rig_bones()
        is_root = bone.parent_id is None

        for widget in self._bone_widgets():
            widget.blockSignals(True)
        self._bone_name_edit.blockSignals(True)
        self._bone_parent_combo.blockSignals(True)

        self._bone_name_edit.setText(bone.name)

        self._bone_parent_combo.clear()
        self._bone_parent_combo.addItem("(none — root)", None)
        descendants = self._descendant_ids(bones, bone.id)
        for other in bones:
            if other.id == bone.id or other.id in descendants:
                continue
            self._bone_parent_combo.addItem(other.name, other.id)
        if not is_root:
            index = self._bone_parent_combo.findData(bone.parent_id)
            self._bone_parent_combo.setCurrentIndex(max(index, 0))
        self._bone_parent_combo.setEnabled(not is_root)

        self._bone_x_spin.setValue(bone.x)
        self._bone_y_spin.setValue(bone.y)
        self._bone_x_spin.setEnabled(is_root)
        self._bone_y_spin.setEnabled(is_root)

        self._bone_rotation_spin.setValue(bone.rotation)
        self._bone_scale_x_spin.setValue(bone.scale_x)
        self._bone_scale_y_spin.setValue(bone.scale_y)
        self._bone_pivot_x_spin.setValue(bone.pivot_x)
        self._bone_pivot_y_spin.setValue(bone.pivot_y)
        self._bone_attach_x_spin.setValue(bone.attach_x)
        self._bone_attach_y_spin.setValue(bone.attach_y)
        self._bone_attach_x_spin.setEnabled(not is_root)
        self._bone_attach_y_spin.setEnabled(not is_root)
        self._bone_z_index_spin.setValue(bone.z_index)
        self._bone_opacity_spin.setValue(bone.opacity)
        self._bone_visible_check.setChecked(bone.visible)

        for widget in self._bone_widgets():
            widget.blockSignals(False)
        self._bone_name_edit.blockSignals(False)
        self._bone_parent_combo.blockSignals(False)

        asset = next((a for a in self._assets if a.id == bone.asset_id), None)
        if asset is not None:
            self._bone_asset_name_label.setText(asset.name)
            self._bone_original_size_label.setText(f"{asset.width}×{asset.height}")
            effective_w = asset.width * bone.scale_x
            effective_h = asset.height * bone.scale_y
            self._bone_effective_size_label.setText(f"{effective_w:.0f}×{effective_h:.0f}")
        else:
            self._bone_asset_name_label.setText("(missing asset)")
            self._bone_original_size_label.setText("—")
            self._bone_effective_size_label.setText("—")

        self._bone_snapshot_before = copy.deepcopy(bone)

    @staticmethod
    def _descendant_ids(bones: list[Bone], bone_id: str) -> set[str]:
        """Ids of ``bone_id``'s descendants — excluded from the Parent combo
        so the user can never pick a reparent target that would create a
        cycle (mirrors ``ui/rig_builder_dialog.py``'s ``_would_create_cycle``)."""
        result: set[str] = set()
        stack = [bone_id]
        while stack:
            current = stack.pop()
            for child in bones:
                if child.parent_id == current and child.id not in result:
                    result.add(child.id)
                    stack.append(child.id)
        return result

    def _layer_widgets(self) -> tuple[QWidget, ...]:
        return (
            self._layer_x_spin,
            self._layer_y_spin,
            self._layer_rotation_spin,
            self._layer_scale_x_spin,
            self._layer_scale_y_spin,
            self._layer_pivot_x_spin,
            self._layer_pivot_y_spin,
            self._layer_z_index_spin,
            self._layer_z_depth_spin,
            self._layer_opacity_spin,
            self._layer_visible_check,
        )

    def _populate_layer_fields(self, layer: Layer) -> None:
        for widget in self._layer_widgets():
            widget.blockSignals(True)
        self._layer_name_edit.blockSignals(True)

        self._layer_name_edit.setText(layer.name)
        self._layer_x_spin.setValue(layer.x)
        self._layer_y_spin.setValue(layer.y)
        self._layer_rotation_spin.setValue(layer.rotation)
        self._layer_scale_x_spin.setValue(layer.scale_x)
        self._layer_scale_y_spin.setValue(layer.scale_y)
        self._layer_pivot_x_spin.setValue(layer.pivot_x)
        self._layer_pivot_y_spin.setValue(layer.pivot_y)
        self._layer_z_index_spin.setValue(layer.z_index)
        self._layer_z_depth_spin.setValue(layer.z_depth)
        self._layer_opacity_spin.setValue(layer.opacity)
        self._layer_visible_check.setChecked(layer.visible)

        for widget in self._layer_widgets():
            widget.blockSignals(False)
        self._layer_name_edit.blockSignals(False)

        self._layer_snapshot_before = copy.deepcopy(layer)

    def _populate_camera_fields(self) -> None:
        camera = self._frame.camera if self._frame is not None else Camera()
        for spin in (self._camera_x_spin, self._camera_y_spin, self._camera_zoom_spin):
            spin.blockSignals(True)
        self._camera_x_spin.setValue(camera.x)
        self._camera_y_spin.setValue(camera.y)
        self._camera_zoom_spin.setValue(camera.zoom)
        for spin in (self._camera_x_spin, self._camera_y_spin, self._camera_zoom_spin):
            spin.blockSignals(False)

        self._camera_snapshot_before = copy.deepcopy(camera)

    # -- Handlers -----------------------------------------------------------

    def _on_bone_name_changed(self) -> None:
        bone = self._find_selected_bone()
        if bone is None:
            return
        bone.name = self._bone_name_edit.text()
        self.changed.emit()
        self._commit_bone_edit()

    def _on_bone_parent_combo_changed(self, _index: int) -> None:
        bone = self._find_selected_bone()
        if bone is None or self._frame is None or self._selected_rig_id is None:
            return
        new_parent_id = self._bone_parent_combo.currentData()
        if new_parent_id is None or new_parent_id == bone.parent_id:
            return  # "(none — root)" is a no-op here; re-rooting is a dedicated action, not a Parent-combo pick
        # Structural change (needs cycle validation across the whole rig) —
        # handled by MainWindow via EditRigStructureCommand, not the plain
        # field-snapshot commit path used by every other spin/checkbox here.
        self.bone_reparent_requested.emit(self._frame.id, self._selected_rig_id, bone.id, new_parent_id)

    def _on_bone_field_changed(self, *_args: object) -> None:
        bone = self._find_selected_bone()
        if bone is None:
            return
        is_root = bone.parent_id is None
        if is_root:
            bone.x = self._bone_x_spin.value()
            bone.y = self._bone_y_spin.value()
        bone.rotation = self._bone_rotation_spin.value()
        bone.scale_x = self._bone_scale_x_spin.value()
        bone.scale_y = self._bone_scale_y_spin.value()
        bone.z_index = self._bone_z_index_spin.value()
        bone.opacity = self._bone_opacity_spin.value()
        bone.visible = self._bone_visible_check.isChecked()
        self._refresh_bone_asset_info(bone)
        self.changed.emit()

    def _on_bone_pivot_changed(self, *_args: object) -> None:
        bone = self._find_selected_bone()
        if bone is None:
            return
        if bone.parent_id is None:
            bone.pivot_x = self._bone_pivot_x_spin.value()
            bone.pivot_y = self._bone_pivot_y_spin.value()
        else:
            set_pivot_preserving_attach_point(bone, self._bone_pivot_x_spin.value(), self._bone_pivot_y_spin.value())
        self._refresh_bone_asset_info(bone)
        self.changed.emit()

    def _on_bone_attach_changed(self, *_args: object) -> None:
        bone = self._find_selected_bone()
        if bone is None or bone.parent_id is None:
            return
        set_attach_point(bone, self._bone_attach_x_spin.value(), self._bone_attach_y_spin.value())
        self.changed.emit()

    def _refresh_bone_asset_info(self, bone: Bone) -> None:
        asset = next((a for a in self._assets if a.id == bone.asset_id), None)
        if asset is not None:
            self._bone_effective_size_label.setText(f"{asset.width * bone.scale_x:.0f}×{asset.height * bone.scale_y:.0f}")

    def _on_layer_name_changed(self) -> None:
        layer = self._find_selected_layer()
        if layer is None:
            return
        layer.name = self._layer_name_edit.text()
        self.changed.emit()
        self._commit_layer_edit()

    def _on_layer_field_changed(self, *_args: object) -> None:
        layer = self._find_selected_layer()
        if layer is None:
            return
        layer.x = self._layer_x_spin.value()
        layer.y = self._layer_y_spin.value()
        layer.rotation = self._layer_rotation_spin.value()
        layer.scale_x = self._layer_scale_x_spin.value()
        layer.scale_y = self._layer_scale_y_spin.value()
        layer.pivot_x = self._layer_pivot_x_spin.value()
        layer.pivot_y = self._layer_pivot_y_spin.value()
        layer.z_index = self._layer_z_index_spin.value()
        layer.z_depth = self._layer_z_depth_spin.value()
        layer.opacity = self._layer_opacity_spin.value()
        layer.visible = self._layer_visible_check.isChecked()
        self.changed.emit()

    def _on_camera_field_changed(self, *_args: object) -> None:
        if self._frame is None:
            return
        self._frame.camera.x = self._camera_x_spin.value()
        self._frame.camera.y = self._camera_y_spin.value()
        self._frame.camera.zoom = self._camera_zoom_spin.value()
        self.changed.emit()

    def _on_reset_camera_clicked(self) -> None:
        if self._frame is None:
            return
        before = copy.deepcopy(self._frame.camera)
        self._frame.camera.x = 0.0
        self._frame.camera.y = 0.0
        self._frame.camera.zoom = 1.0
        after = copy.deepcopy(self._frame.camera)
        self._populate_camera_fields()  # also re-baselines _camera_snapshot_before to `after`
        self.changed.emit()
        if after != before:
            self.camera_edit_committed.emit(self._frame.id, before, after)

    # -- Commit: one undoable command per finished edit "session" -----------

    def _commit_bone_edit(self) -> None:
        bone = self._find_selected_bone()
        if bone is None or self._bone_snapshot_before is None or self._frame is None or self._selected_rig_id is None:
            return
        after = copy.deepcopy(bone)
        if after != self._bone_snapshot_before:
            self.bone_edit_committed.emit(
                self._frame.id, self._selected_rig_id, bone.id, self._bone_snapshot_before, after
            )
        self._bone_snapshot_before = after

    def _commit_layer_edit(self) -> None:
        layer = self._find_selected_layer()
        if layer is None or self._layer_snapshot_before is None or self._frame is None:
            return
        after = copy.deepcopy(layer)
        if after != self._layer_snapshot_before:
            self.layer_edit_committed.emit(self._frame.id, layer.id, self._layer_snapshot_before, after)
        self._layer_snapshot_before = after

    def _commit_camera_edit(self) -> None:
        if self._frame is None or self._camera_snapshot_before is None:
            return
        after = copy.deepcopy(self._frame.camera)
        if after != self._camera_snapshot_before:
            self.camera_edit_committed.emit(self._frame.id, self._camera_snapshot_before, after)
        self._camera_snapshot_before = after
