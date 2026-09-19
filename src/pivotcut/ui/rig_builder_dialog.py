"""Character Rig Builder dialog (Milestone 6A).

Lets the user import multiple PNGs, assemble them into a hierarchical
character (parent/child parts, pivots, attach points, z-index), and either
commit the result as the current frame's rig *instance*
(``EditRigStructureCommand``) or save it into the project's Rig Library as
a reusable ``RigTemplate`` (``SaveRigTemplateCommand``) — both via the
caller-supplied ``execute_command`` callback, so the dialog itself never
touches ``UndoRedoStack``/dirty-flag/thumbnail-cache bookkeeping directly.

The dialog works on a **local, temporary copy** of the bone list
(``self._bones``) for its whole lifetime; nothing is written back to the
``Project`` until "Save" is pressed, and Cancel simply closes the dialog
without calling ``execute_command`` at all — so Cancel never mutates
``Project``, the undo/redo history, or the thumbnail cache. The one
exception, matching this app's existing convention (see
``ui/asset_panel.py``), is PNG import: importing a PNG here appends
directly to ``Project.assets`` immediately, exactly as "Import PNG…" in the
main Asset panel already does outside any command — imports were never
undo-tracked anywhere in this codebase, and re-importing the same file is a
no-op dedup, so this dialog doesn't special-case it either.

Preview rendering reuses ``domain.rig.world_transforms``/``local_matrix``
(pure, Qt-free) and the same stateless Qt item classes as the main canvas
(``BoneItem``/``MissingAssetItem`` from ``ui/graphics_items.py``), but is
its own dedicated, self-contained scene — it never touches ``CanvasView``.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable

from PIL import Image
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pivotcut.domain.assets import Asset
from pivotcut.domain.models import Project
from pivotcut.domain.rig import (
    Bone,
    Matrix2D,
    Rig,
    RigTemplate,
    RigValidationError,
    create_child_bone,
    duplicate_bone,
    find_bone_in_list,
    remove_bone_reparent_children,
    remove_bone_subtree,
    reparent_bone,
    set_attach_point,
    set_pivot_preserving_attach_point,
    set_root_bone,
    validate_rig_template,
    world_transforms,
)
from pivotcut.services import asset_manager
from pivotcut.ui.graphics_items import BoneItem, MissingAssetItem, _load_pixmap, _matrix_to_qtransform

_PIVOT_COLOR = QColor("#ffcc00")
_ATTACH_COLOR = QColor("#00d0ff")
_PIVOT_RADIUS = 5.0
_ATTACH_RADIUS = 11.0
_SKELETON_LINE_COLOR = QColor(255, 255, 255, 90)


def _children_of(bones: list[Bone], parent_id: str) -> list[Bone]:
    return [bone for bone in bones if bone.parent_id == parent_id]


class _PreviewCanvas(QGraphicsView):
    """Self-contained preview: renders the working bone list and the
    selected part's pivot/attach handles. Never touches CanvasView."""

    bone_clicked = Signal(str)
    pivot_dragged = Signal(float, float)  # new local pivot_x/pivot_y (in-progress)
    attach_dragged = Signal(float, float)  # new parent-local attach_x/attach_y (in-progress)
    drag_finished = Signal()

    def __init__(self, canvas_width: float, canvas_height: float, parent: QWidget | None = None) -> None:
        scene = QGraphicsScene(0, 0, canvas_width, canvas_height)
        super().__init__(scene, parent)
        # Keep a live Python reference: unlike CanvasView (which immediately
        # adds a background item, keeping the scene reachable through it),
        # this scene otherwise starts empty and setScene() alone doesn't
        # guarantee shiboken keeps the wrapper alive.
        self._scene_ref = scene
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QBrush(QColor("#3a3a3a")))
        self._pixmap_cache: dict[str, QPixmap] = {}
        self._items_by_bone: dict[str, object] = {}
        self._handle_items: list[object] = []
        self._skeleton_lines: list[QGraphicsLineItem] = []
        self._world: dict[str, Matrix2D] = {}
        self._drag_kind: str | None = None  # "pivot" | "attach"
        self._drag_start_pos = QPointF()
        self._drag_start_value = (0.0, 0.0)
        self._drag_inverse: Matrix2D | None = None

    def sync(
        self,
        bones: list[Bone],
        root_bone_id: str,
        assets_by_id: dict[str, Asset],
        project_dir: Path | None,
        selected_bone_id: str | None,
        show_skeleton: bool,
    ) -> None:
        for item in list(self._items_by_bone.values()):
            self.scene().removeItem(item)
        self._items_by_bone.clear()
        for line in self._skeleton_lines:
            self.scene().removeItem(line)
        self._skeleton_lines.clear()
        self._clear_handles()

        if not bones or root_bone_id is None or find_bone_in_list(bones, root_bone_id) is None:
            self._world = {}
            return

        rig = Rig(id="builder-preview", name="", root_bone_id=root_bone_id, bones=bones)
        try:
            self._world = world_transforms(rig)
        except RigValidationError:
            self._world = {}
            return

        for bone in bones:
            asset = assets_by_id.get(bone.asset_id)
            pixmap = _load_pixmap(self._pixmap_cache, asset, project_dir)
            if pixmap is None:
                item = MissingAssetItem(
                    bone.id,
                    float(asset.width) if asset is not None else 64.0,
                    float(asset.height) if asset is not None else 64.0,
                    asset.name if asset is not None else bone.asset_id,
                )
            else:
                item = BoneItem(bone.id, pixmap)
            item.setTransform(_matrix_to_qtransform(self._world[bone.id]))
            item.setZValue(bone.z_index)
            item.setVisible(bone.visible)
            item.setOpacity(max(0.0, min(1.0, bone.opacity)))
            self.scene().addItem(item)
            self._items_by_bone[bone.id] = item

        if show_skeleton:
            for bone in bones:
                if bone.parent_id is None:
                    continue
                parent = find_bone_in_list(bones, bone.parent_id)
                if parent is None or parent.id not in self._world or bone.id not in self._world:
                    continue
                px, py = self._world[parent.id].apply_point(parent.pivot_x, parent.pivot_y)
                cx, cy = self._world[bone.id].apply_point(bone.pivot_x, bone.pivot_y)
                line = QGraphicsLineItem(px, py, cx, cy)
                line.setPen(QPen(_SKELETON_LINE_COLOR, 1.5))
                line.setZValue(8000)
                self.scene().addItem(line)
                self._skeleton_lines.append(line)

        if selected_bone_id is not None:
            self._draw_handles(bones, root_bone_id, selected_bone_id)

    def _clear_handles(self) -> None:
        for item in self._handle_items:
            self.scene().removeItem(item)
        self._handle_items.clear()

    def _draw_handles(self, bones: list[Bone], root_bone_id: str, bone_id: str) -> None:
        bone = find_bone_in_list(bones, bone_id)
        if bone is None or bone_id == root_bone_id or bone.id not in self._world:
            return  # root has no attach point; nothing to drag there
        world_x, world_y = self._world[bone.id].apply_point(bone.pivot_x, bone.pivot_y)

        attach_circle = QGraphicsEllipseItem(
            world_x - _ATTACH_RADIUS, world_y - _ATTACH_RADIUS, _ATTACH_RADIUS * 2, _ATTACH_RADIUS * 2
        )
        attach_circle.setPen(QPen(_ATTACH_COLOR, 2))
        attach_circle.setBrush(Qt.BrushStyle.NoBrush)
        attach_circle.setZValue(9500)
        self.scene().addItem(attach_circle)
        self._handle_items.append(attach_circle)

        pivot_dot = QGraphicsEllipseItem(
            world_x - _PIVOT_RADIUS, world_y - _PIVOT_RADIUS, _PIVOT_RADIUS * 2, _PIVOT_RADIUS * 2
        )
        pivot_dot.setPen(QPen(_PIVOT_COLOR, 1))
        pivot_dot.setBrush(QBrush(_PIVOT_COLOR))
        pivot_dot.setZValue(9600)
        self.scene().addItem(pivot_dot)
        self._handle_items.append(pivot_dot)

        self._handle_center = QPointF(world_x, world_y)
        self._handle_bone = bone

    # -- Selection / handle-drag interaction ---------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        scene_pos = self.mapToScene(event.position().toPoint())

        if self._handle_items and hasattr(self, "_handle_center"):
            dist = ((scene_pos.x() - self._handle_center.x()) ** 2 + (scene_pos.y() - self._handle_center.y()) ** 2) ** 0.5
            bone = self._handle_bone
            if dist <= _PIVOT_RADIUS:
                self._drag_kind = "pivot"
                self._drag_start_pos = scene_pos
                self._drag_start_value = (bone.pivot_x, bone.pivot_y)
                self._drag_inverse = self._world.get(bone.id, Matrix2D.identity()).inverse()
                event.accept()
                return
            if dist <= _ATTACH_RADIUS:
                self._drag_kind = "attach"
                self._drag_start_pos = scene_pos
                self._drag_start_value = (bone.attach_x, bone.attach_y)
                parent_world = self._world.get(bone.parent_id, Matrix2D.identity())
                self._drag_inverse = parent_world.inverse()
                event.accept()
                return

        view_pos = event.position().toPoint()
        for candidate in self.items(view_pos):
            bone_id = getattr(candidate, "bone_id", None)
            if bone_id is not None:
                self.bone_clicked.emit(bone_id)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if self._drag_kind is None or self._drag_inverse is None:
            super().mouseMoveEvent(event)
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        dx = scene_pos.x() - self._drag_start_pos.x()
        dy = scene_pos.y() - self._drag_start_pos.y()
        local_dx, local_dy = self._drag_inverse.apply_vector(dx, dy)
        new_x = self._drag_start_value[0] + local_dx
        new_y = self._drag_start_value[1] + local_dy
        if self._drag_kind == "pivot":
            self.pivot_dragged.emit(new_x, new_y)
        else:
            self.attach_dragged.emit(new_x, new_y)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if self._drag_kind is not None:
            self._drag_kind = None
            self._drag_inverse = None
            self.drag_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class RigBuilderDialog(QDialog):
    """New Character Rig / Edit Selected Character Rig / New from a template.

    ``initial_bones``/``initial_root_bone_id`` seed the working copy (empty
    for "New Character Rig"). On accept, call :meth:`result_bones()`/
    :meth:`result_root_bone_id()` to read back the edited structure.
    """

    def __init__(
        self,
        project: Project,
        get_project_dir: Callable[[], Path | None],
        initial_bones: list[Bone] | None = None,
        initial_root_bone_id: str | None = None,
        title: str = "Character Rig Builder",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1100, 700)
        self._project = project
        self._get_project_dir = get_project_dir
        self._bones: list[Bone] = [Bone(**vars(b)) for b in (initial_bones or [])]
        self._root_bone_id: str | None = initial_root_bone_id
        self._selected_bone_id: str | None = initial_root_bone_id
        self._canvas_width = float(project.scene_settings.width)
        self._canvas_height = float(project.scene_settings.height)

        self.setWhatsThis(
            "The Character Rig Builder: import PNGs, add them as Parts, pick a Root, and attach "
            "each other Part to a parent to build a hierarchical character. Click any control "
            "for detail on what it does."
        )
        self._build_ui()
        self._refresh_asset_list()
        self._refresh_hierarchy()
        self._refresh_inspector()
        self._refresh_preview()

    # -- UI construction ------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        help_label = QLabel(
            "To get started: import your PNGs, Add Part for each one, pick the "
            "torso as Root, assign each Part's Parent, then Save Character."
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #9fd6a3; font-style: italic;")
        outer.addWidget(help_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)

        splitter.addWidget(self._build_assets_column())
        splitter.addWidget(self._build_hierarchy_column())
        splitter.addWidget(self._build_preview_column())
        splitter.addWidget(self._build_inspector_column())
        splitter.setSizes([200, 220, 480, 260])

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_save_clicked)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _build_assets_column(self) -> QWidget:
        box = QGroupBox("Assets")
        layout = QVBoxLayout(box)

        row = QHBoxLayout()
        import_one_btn = QPushButton("Import PNG…")
        import_one_btn.setWhatsThis("Imports a single transparent PNG into this project's asset registry.")
        import_one_btn.clicked.connect(self._on_import_single)
        import_many_btn = QPushButton("Import Multiple…")
        import_many_btn.setWhatsThis("Imports several PNGs at once — pick multiple files in the dialog.")
        import_many_btn.clicked.connect(self._on_import_multiple)
        row.addWidget(import_one_btn)
        row.addWidget(import_many_btn)
        layout.addLayout(row)

        import_folder_btn = QPushButton("Import Folder…")
        import_folder_btn.setWhatsThis("Imports every .png file found directly inside a folder you choose.")
        import_folder_btn.clicked.connect(self._on_import_folder)
        layout.addWidget(import_folder_btn)

        self._asset_list = QListWidget()
        self._asset_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._asset_list.setWhatsThis(
            "PNGs available in this project. Select one or more (Cmd-click/Shift-click for "
            "several), then click Add Part(s) below to bring them into the character."
        )
        layout.addWidget(self._asset_list, 1)

        self._add_part_btn = QPushButton("Add Part(s)")
        self._add_part_btn.setToolTip(
            "Select one or more PNGs above, then Add Part(s). The first part "
            "added becomes the Root; the rest attach to whichever Part is "
            "currently selected in the Hierarchy (or the Root, if none is)."
        )
        self._add_part_btn.setWhatsThis(
            "Adds the selected PNG(s) above as new Parts. The very first Part added to an empty "
            "character becomes its Root. Every Part added afterwards attaches as a child of "
            "whichever Part is currently selected in the Hierarchy (or the Root, if none is "
            "selected) — select a Part in the Hierarchy first to control where new Parts attach."
        )
        self._add_part_btn.clicked.connect(self._on_add_part_clicked)
        layout.addWidget(self._add_part_btn)

        return box

    def _build_hierarchy_column(self) -> QWidget:
        box = QGroupBox("Hierarchy")
        box.setWhatsThis(
            "The character's Part tree. The Root (marked \"(Root)\") is the character's anchor "
            "— every other Part is its child, directly or indirectly, and follows its pose. "
            "Click a Part here to select it and edit it on the right."
        )
        layout = QVBoxLayout(box)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        layout.addWidget(self._tree, 1)

        row = QHBoxLayout()
        self._remove_part_btn = QPushButton("Remove Part")
        self._remove_part_btn.setWhatsThis(
            "Removes the selected Part. If it has children, asks whether to delete them too or "
            "reparent them to this Part's own parent instead. The Root Part can't be removed "
            "this way — Set Root on another Part first if you need to replace it."
        )
        self._remove_part_btn.clicked.connect(self._on_remove_part_clicked)
        self._duplicate_part_btn = QPushButton("Duplicate Part")
        self._duplicate_part_btn.setWhatsThis(
            "Duplicates the selected Part as a sibling with the same parent and appearance."
        )
        self._duplicate_part_btn.clicked.connect(self._on_duplicate_part_clicked)
        row.addWidget(self._remove_part_btn)
        row.addWidget(self._duplicate_part_btn)
        layout.addLayout(row)

        self._set_root_btn = QPushButton("Set Root")
        self._set_root_btn.setWhatsThis(
            "Makes the selected Part the new Root of this character — every other Part becomes "
            "(directly or indirectly) its child."
        )
        self._set_root_btn.clicked.connect(self._on_set_root_clicked)
        layout.addWidget(self._set_root_btn)

        auto_layout_btn = QPushButton("Auto-layout Parts")
        auto_layout_btn.setToolTip(
            "Spreads all non-root Parts into a temporary grid so overlapping "
            "PNGs become individually visible/selectable in the preview. "
            "Only changes attach points (still freely re-editable afterwards) "
            "— never touches assets or scale."
        )
        auto_layout_btn.setWhatsThis(
            "Useful right after adding several Parts that all landed stacked on top of each "
            "other: spreads every non-root Part into a temporary grid in the preview, purely by "
            "changing their Attach points, so you can click each one individually and drag it "
            "into place. Never touches the PNG files or their scale."
        )
        auto_layout_btn.clicked.connect(self._on_auto_layout_clicked)
        layout.addWidget(auto_layout_btn)

        return box

    def _build_preview_column(self) -> QWidget:
        box = QGroupBox("Preview")
        layout = QVBoxLayout(box)

        self._skeleton_check = QCheckBox("Show skeleton guides")
        self._skeleton_check.setChecked(True)
        self._skeleton_check.setWhatsThis(
            "Draws a line from each Part's pivot to its parent's pivot, so you can see the "
            "hierarchy at a glance in the preview."
        )
        self._skeleton_check.stateChanged.connect(lambda *_: self._refresh_preview())
        layout.addWidget(self._skeleton_check)

        legend = QLabel(
            f'<span style="color:{_ATTACH_COLOR.name()}">○</span> Attach point &nbsp;&nbsp;'
            f'<span style="color:{_PIVOT_COLOR.name()}">●</span> Pivot'
        )
        layout.addWidget(legend)

        self._preview = _PreviewCanvas(self._canvas_width, self._canvas_height)
        self._preview.setWhatsThis(
            "Live preview of the character being built. Click a Part to select it, then drag "
            "its yellow Pivot dot or cyan Attach circle to reposition it (equivalent to editing "
            "Pivot X/Y or Attach X/Y on the right)."
        )
        self._preview.bone_clicked.connect(self._on_preview_bone_clicked)
        self._preview.pivot_dragged.connect(self._on_pivot_dragged)
        self._preview.attach_dragged.connect(self._on_attach_dragged)
        self._preview.drag_finished.connect(self._refresh_inspector)
        layout.addWidget(self._preview, 1)

        return box

    def _build_inspector_column(self) -> QWidget:
        box = QGroupBox("Selected Part")
        outer = QVBoxLayout(box)
        form = QFormLayout()

        self._name_edit = QLineEdit()
        self._name_edit.setWhatsThis("This Part's display name, shown in the Hierarchy tree. Purely cosmetic.")
        self._name_edit.editingFinished.connect(self._on_name_edited)
        form.addRow("Name", self._name_edit)

        self._parent_combo = QComboBox()
        self._parent_combo.setWhatsThis(
            "Reattaches this Part to a different parent — validated so it can't create a cycle. "
            "Disabled for the Root Part."
        )
        self._parent_combo.currentIndexChanged.connect(self._on_parent_combo_changed)
        form.addRow("Parent", self._parent_combo)

        self._x_spin = self._make_spin(-100_000, 100_000)
        self._y_spin = self._make_spin(-100_000, 100_000)
        for spin in (self._x_spin, self._y_spin):
            spin.setWhatsThis(
                "World position of the character's Root Part. Only editable on the Root — every "
                "other Part is positioned via its Attach point instead."
            )
        self._x_spin.valueChanged.connect(self._on_root_xy_changed)
        self._y_spin.valueChanged.connect(self._on_root_xy_changed)
        form.addRow("X (world, root only)", self._x_spin)
        form.addRow("Y (world, root only)", self._y_spin)

        self._rotation_spin = self._make_spin(-3600, 3600)
        self._rotation_spin.setWhatsThis("Rotation of this Part around its own Pivot, in degrees.")
        self._rotation_spin.valueChanged.connect(self._on_field_changed)
        form.addRow("Rotation", self._rotation_spin)

        self._scale_x_spin = self._make_spin(0.01, 100.0, step=0.1)
        self._scale_y_spin = self._make_spin(0.01, 100.0, step=0.1)
        for spin in (self._scale_x_spin, self._scale_y_spin):
            spin.setWhatsThis("Scale of this Part relative to its original PNG size (1.0 = original size).")
        self._scale_x_spin.valueChanged.connect(self._on_field_changed)
        self._scale_y_spin.valueChanged.connect(self._on_field_changed)
        form.addRow("Scale X", self._scale_x_spin)
        form.addRow("Scale Y", self._scale_y_spin)

        self._pivot_x_spin = self._make_spin(-100_000, 100_000)
        self._pivot_y_spin = self._make_spin(-100_000, 100_000)
        for spin in (self._pivot_x_spin, self._pivot_y_spin):
            spin.setWhatsThis(
                "The point (in the PNG's own pixel coordinates) this Part rotates/scales "
                "around. Equivalent to dragging the yellow dot in the preview."
            )
        self._pivot_x_spin.valueChanged.connect(self._on_pivot_spin_changed)
        self._pivot_y_spin.valueChanged.connect(self._on_pivot_spin_changed)
        form.addRow("Pivot X", self._pivot_x_spin)
        form.addRow("Pivot Y", self._pivot_y_spin)

        self._attach_x_spin = self._make_spin(-100_000, 100_000)
        self._attach_y_spin = self._make_spin(-100_000, 100_000)
        for spin in (self._attach_x_spin, self._attach_y_spin):
            spin.setWhatsThis(
                "Where this Part attaches to its parent, in the parent's own local space — "
                "equivalent to dragging the cyan circle in the preview. Only meaningful for "
                "non-root Parts."
            )
        self._attach_x_spin.valueChanged.connect(self._on_attach_spin_changed)
        self._attach_y_spin.valueChanged.connect(self._on_attach_spin_changed)
        form.addRow("Attach X (parent space)", self._attach_x_spin)
        form.addRow("Attach Y (parent space)", self._attach_y_spin)

        self._z_index_spin = QSpinBox()
        self._z_index_spin.setRange(-100_000, 100_000)
        self._z_index_spin.setWhatsThis("Stacking order: higher values draw in front of lower ones.")
        self._z_index_spin.valueChanged.connect(self._on_field_changed)
        form.addRow("Z Index", self._z_index_spin)

        self._opacity_spin = self._make_spin(0.0, 1.0, step=0.05)
        self._opacity_spin.setWhatsThis("0 = fully transparent, 1 = fully opaque.")
        self._opacity_spin.valueChanged.connect(self._on_field_changed)
        form.addRow("Opacity", self._opacity_spin)

        self._visible_check = QCheckBox()
        self._visible_check.setWhatsThis("Hides this Part from the preview and from export, without deleting it.")
        self._visible_check.stateChanged.connect(self._on_field_changed)
        form.addRow("Visible", self._visible_check)

        outer.addLayout(form)

        tools_label = QLabel("Non-destructive tools")
        bold = tools_label.font()
        bold.setBold(True)
        tools_label.setFont(bold)
        outer.addWidget(tools_label)

        fit_btn = QPushButton("Fit to Canvas")
        fit_btn.setWhatsThis(
            "Scales the selected Part down (never up) so it fits within the reference canvas "
            "size below — handy right after adding an oversized PNG."
        )
        fit_btn.clicked.connect(self._on_fit_to_canvas)
        outer.addWidget(fit_btn)

        reset_scale_btn = QPushButton("Reset Scale")
        reset_scale_btn.setWhatsThis("Resets the selected Part's scale to 1.0 x 1.0 (its original PNG size).")
        reset_scale_btn.clicked.connect(self._on_reset_scale)
        outer.addWidget(reset_scale_btn)

        center_pivot_btn = QPushButton("Center Selected Pivot")
        center_pivot_btn.setWhatsThis(
            "Moves the selected Part's pivot to the exact center of its PNG (width/2, height/2)."
        )
        center_pivot_btn.clicked.connect(self._on_set_pivot_center)
        outer.addWidget(center_pivot_btn)

        crop_btn = QPushButton("Crop Preview to Alpha Bounds")
        crop_btn.setToolTip(
            "Non-destructive: re-centers the pivot on the PNG's visible "
            "(non-transparent) content. Never modifies the source file."
        )
        crop_btn.setWhatsThis(
            "Re-centers the selected Part's pivot on the actual visible (non-transparent) "
            "content of its PNG, ignoring any empty transparent margins. Purely a pivot "
            "adjustment — it never crops, resamples or otherwise modifies the source PNG file."
        )
        crop_btn.clicked.connect(self._on_crop_to_alpha_bounds)
        outer.addWidget(crop_btn)

        scene_ref_row = QHBoxLayout()
        scene_ref_row.addWidget(QLabel("Scene reference size:"))
        self._scene_width_spin = QSpinBox()
        self._scene_width_spin.setRange(1, 20000)
        self._scene_width_spin.setValue(int(self._canvas_width))
        self._scene_height_spin = QSpinBox()
        self._scene_height_spin.setRange(1, 20000)
        self._scene_height_spin.setValue(int(self._canvas_height))
        for spin in (self._scene_width_spin, self._scene_height_spin):
            spin.setWhatsThis(
                "Reference canvas size used only while building this character (for Fit to "
                "Canvas and for centering a new Root Part) — it does not change the project's "
                "actual scene/export size."
            )
        self._scene_width_spin.valueChanged.connect(self._on_scene_reference_size_changed)
        self._scene_height_spin.valueChanged.connect(self._on_scene_reference_size_changed)
        scene_ref_row.addWidget(self._scene_width_spin)
        scene_ref_row.addWidget(self._scene_height_spin)
        outer.addLayout(scene_ref_row)

        outer.addStretch(1)
        return box

    def _make_spin(self, minimum: float, maximum: float, step: float = 1.0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setSingleStep(step)
        return spin

    # -- Data access ------------------------------------------------------

    def result_bones(self) -> list[Bone]:
        return self._bones

    def result_root_bone_id(self) -> str | None:
        return self._root_bone_id

    def _selected_bone(self) -> Bone | None:
        if self._selected_bone_id is None:
            return None
        return find_bone_in_list(self._bones, self._selected_bone_id)

    def _assets_by_id(self) -> dict[str, Asset]:
        return {asset.id: asset for asset in self._project.assets}

    # -- Refresh --------------------------------------------------------------

    def _refresh_asset_list(self) -> None:
        self._asset_list.clear()
        for asset in self._project.assets:
            item = QListWidgetItem(f"{asset.name}  ({asset.width}×{asset.height})")
            item.setData(Qt.ItemDataRole.UserRole, asset.id)
            self._asset_list.addItem(item)

    def _refresh_hierarchy(self) -> None:
        self._tree.blockSignals(True)
        self._tree.clear()
        items_by_id: dict[str, QTreeWidgetItem] = {}

        def build(bone: Bone, parent_item: QTreeWidgetItem | None) -> None:
            label = bone.name + (" (Root)" if bone.id == self._root_bone_id else "")
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, bone.id)
            if parent_item is None:
                self._tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            items_by_id[bone.id] = item
            for child in _children_of(self._bones, bone.id):
                build(child, item)

        if self._root_bone_id is not None:
            root = find_bone_in_list(self._bones, self._root_bone_id)
            if root is not None:
                build(root, None)
        self._tree.expandAll()

        if self._selected_bone_id is not None and self._selected_bone_id in items_by_id:
            items_by_id[self._selected_bone_id].setSelected(True)
        self._tree.blockSignals(False)

    def _refresh_preview(self) -> None:
        self._preview.sync(
            self._bones,
            self._root_bone_id,
            self._assets_by_id(),
            self._get_project_dir(),
            self._selected_bone_id,
            self._skeleton_check.isChecked(),
        )

    def _refresh_inspector(self) -> None:
        bone = self._selected_bone()
        widgets = (
            self._name_edit,
            self._parent_combo,
            self._x_spin,
            self._y_spin,
            self._rotation_spin,
            self._scale_x_spin,
            self._scale_y_spin,
            self._pivot_x_spin,
            self._pivot_y_spin,
            self._attach_x_spin,
            self._attach_y_spin,
            self._z_index_spin,
            self._opacity_spin,
            self._visible_check,
        )
        for widget in widgets:
            widget.blockSignals(True)

        if bone is None:
            for widget in widgets:
                widget.setEnabled(False)
            for widget in widgets:
                widget.blockSignals(False)
            return

        for widget in widgets:
            widget.setEnabled(True)

        is_root = bone.id == self._root_bone_id
        self._name_edit.setText(bone.name)

        self._parent_combo.clear()
        self._parent_combo.addItem("(none — root)", None)
        for other in self._bones:
            if other.id == bone.id:
                continue
            if self._would_create_cycle(bone.id, other.id):
                continue
            self._parent_combo.addItem(other.name, other.id)
        if not is_root:
            index = self._parent_combo.findData(bone.parent_id)
            self._parent_combo.setCurrentIndex(max(index, 0))
        self._parent_combo.setEnabled(not is_root)

        self._x_spin.setValue(bone.x)
        self._y_spin.setValue(bone.y)
        self._x_spin.setEnabled(is_root)
        self._y_spin.setEnabled(is_root)

        self._rotation_spin.setValue(bone.rotation)
        self._scale_x_spin.setValue(bone.scale_x)
        self._scale_y_spin.setValue(bone.scale_y)
        self._pivot_x_spin.setValue(bone.pivot_x)
        self._pivot_y_spin.setValue(bone.pivot_y)
        self._attach_x_spin.setValue(bone.attach_x)
        self._attach_y_spin.setValue(bone.attach_y)
        self._attach_x_spin.setEnabled(not is_root)
        self._attach_y_spin.setEnabled(not is_root)
        self._z_index_spin.setValue(bone.z_index)
        self._opacity_spin.setValue(bone.opacity)
        self._visible_check.setChecked(bone.visible)

        for widget in widgets:
            widget.blockSignals(False)

        self._remove_part_btn.setEnabled(not is_root)
        self._duplicate_part_btn.setEnabled(True)
        self._set_root_btn.setEnabled(not is_root)

    def _would_create_cycle(self, bone_id: str, candidate_parent_id: str) -> bool:
        bones_by_id = {b.id: b for b in self._bones}
        current: str | None = candidate_parent_id
        while current is not None:
            if current == bone_id:
                return True
            current = bones_by_id[current].parent_id
        return False

    # -- Asset import -----------------------------------------------------

    def _import_paths(self, paths: list[Path]) -> None:
        imported = 0
        errors: list[str] = []
        for path in paths:
            try:
                asset_manager.import_png_asset(self._project, path, self._get_project_dir())
                imported += 1
            except asset_manager.AssetImportError as exc:
                errors.append(str(exc))
        self._refresh_asset_list()
        if errors:
            QMessageBox.warning(self, "Some PNGs Could Not Be Imported", "\n".join(errors))

    def _on_import_single(self) -> None:
        path = asset_manager.pick_png_file_via_dialog(self)
        if path is not None:
            self._import_paths([path])

    def _on_import_multiple(self) -> None:
        paths_str, _ = QFileDialog.getOpenFileNames(self, "Import PNGs", str(Path.home()), "PNG Images (*.png)")
        if paths_str:
            self._import_paths([Path(p) for p in paths_str])

    def _on_import_folder(self) -> None:
        directory_str = QFileDialog.getExistingDirectory(self, "Import Folder of PNGs", str(Path.home()))
        if not directory_str:
            return
        paths = sorted(Path(directory_str).glob("*.png"))
        if not paths:
            QMessageBox.information(self, "No PNGs Found", "That folder has no .png files.")
            return
        self._import_paths(paths)

    # -- Part creation / removal / duplication / reparent --------------------

    @staticmethod
    def _display_name_from_filename(asset: Asset) -> str:
        # "upper_arm_L" -> "Upper arm L"; asset.name is already the filename
        # stem (see services/asset_manager.py), so this only needs to turn
        # underscores into spaces and capitalize the first letter.
        readable = asset.name.replace("_", " ").strip()
        return readable[:1].upper() + readable[1:] if readable else asset.name

    def _on_add_part_clicked(self) -> None:
        items = self._asset_list.selectedItems() or (
            [self._asset_list.currentItem()] if self._asset_list.currentItem() is not None else []
        )
        if not items:
            QMessageBox.information(self, "Add Part(s)", "Select one or more assets first.")
            return

        for item in items:
            asset_id = item.data(Qt.ItemDataRole.UserRole)
            asset = next((a for a in self._project.assets if a.id == asset_id), None)
            if asset is None:
                continue
            self._add_part_for_asset(asset)

        self._refresh_hierarchy()
        self._refresh_inspector()
        self._refresh_preview()

    def _add_part_for_asset(self, asset: Asset) -> None:
        """Add one Part for ``asset``: becomes Root if none exists yet,
        otherwise attaches to the currently selected Part (or the Root)."""
        display_name = self._display_name_from_filename(asset)
        if self._root_bone_id is None:
            bone = Bone(
                id=str(uuid.uuid4()),
                name=display_name,
                parent_id=None,
                asset_id=asset.id,
                x=self._canvas_width / 2,
                y=self._canvas_height / 2,
                pivot_x=asset.width / 2,
                pivot_y=asset.height / 2,
            )
            self._bones.append(bone)
            self._root_bone_id = bone.id
            self._selected_bone_id = bone.id
        else:
            parent = self._selected_bone() or find_bone_in_list(self._bones, self._root_bone_id)
            bone = create_child_bone(asset, parent, name=display_name)
            self._bones.append(bone)
            self._selected_bone_id = bone.id

    def _on_auto_layout_clicked(self) -> None:
        """Spread all non-root Parts into a temporary grid (attach points
        only) purely so overlapping PNGs become individually visible/
        selectable in the preview — never touches assets, scale, or the
        source PNGs; the user can freely drag/edit attach points afterwards."""
        non_root = [bone for bone in self._bones if bone.id != self._root_bone_id]
        if not non_root:
            return
        columns = max(1, int(len(non_root) ** 0.5 + 0.999))
        cell = 160.0
        for index, bone in enumerate(non_root):
            col, row = index % columns, index // columns
            set_attach_point(bone, 80.0 + col * cell, 80.0 + row * cell)
        self._refresh_hierarchy()
        self._refresh_inspector()
        self._refresh_preview()

    def _on_remove_part_clicked(self) -> None:
        bone = self._selected_bone()
        if bone is None or bone.id == self._root_bone_id:
            return
        has_children = bool(_children_of(self._bones, bone.id))
        if not has_children:
            self._bones = remove_bone_subtree(self._bones, bone.id, self._root_bone_id)
        else:
            reply = QMessageBox.question(
                self,
                "Remove Part",
                f'"{bone.name}" has child parts. Remove them too (Yes), or reparent them to '
                f'"{bone.name}"\'s parent instead (No)?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Yes:
                self._bones = remove_bone_subtree(self._bones, bone.id, self._root_bone_id)
            else:
                self._bones = remove_bone_reparent_children(self._bones, bone.id, self._root_bone_id)
        self._selected_bone_id = self._root_bone_id
        self._refresh_hierarchy()
        self._refresh_inspector()
        self._refresh_preview()

    def _on_duplicate_part_clicked(self) -> None:
        bone = self._selected_bone()
        if bone is None:
            return
        new_bone = duplicate_bone(self._bones, bone.id)
        if bone.id == self._root_bone_id:
            # Duplicating the root creates an independent second root-like
            # part parented to the (existing) root, since a rig can only
            # have one true root.
            new_bone.parent_id = self._root_bone_id
        self._bones.append(new_bone)
        self._selected_bone_id = new_bone.id
        self._refresh_hierarchy()
        self._refresh_inspector()
        self._refresh_preview()

    def _on_set_root_clicked(self) -> None:
        bone = self._selected_bone()
        if bone is None or self._root_bone_id is None:
            return
        set_root_bone(self._bones, bone.id)
        self._root_bone_id = bone.id
        self._refresh_hierarchy()
        self._refresh_inspector()
        self._refresh_preview()

    # -- Selection ----------------------------------------------------------

    def _on_tree_selection_changed(self) -> None:
        items = self._tree.selectedItems()
        if not items:
            return
        self._selected_bone_id = items[0].data(0, Qt.ItemDataRole.UserRole)
        self._refresh_inspector()
        self._refresh_preview()

    def _on_preview_bone_clicked(self, bone_id: str) -> None:
        self._selected_bone_id = bone_id
        self._refresh_hierarchy()
        self._refresh_inspector()
        self._refresh_preview()

    # -- Inspector field edits ------------------------------------------------

    def _on_name_edited(self) -> None:
        bone = self._selected_bone()
        if bone is None:
            return
        bone.name = self._name_edit.text()
        self._refresh_hierarchy()

    def _on_parent_combo_changed(self, _index: int) -> None:
        bone = self._selected_bone()
        if bone is None or bone.id == self._root_bone_id:
            return
        new_parent_id = self._parent_combo.currentData()
        if new_parent_id is None or new_parent_id == bone.parent_id:
            return
        try:
            reparent_bone(self._bones, bone.id, new_parent_id)
        except RigValidationError as exc:
            QMessageBox.warning(self, "Cannot Reparent", str(exc))
            self._refresh_inspector()
            return
        self._refresh_hierarchy()
        self._refresh_preview()

    def _on_root_xy_changed(self, *_args: object) -> None:
        bone = self._selected_bone()
        if bone is None or bone.id != self._root_bone_id:
            return
        bone.x = self._x_spin.value()
        bone.y = self._y_spin.value()
        self._refresh_preview()

    def _on_field_changed(self, *_args: object) -> None:
        bone = self._selected_bone()
        if bone is None:
            return
        bone.rotation = self._rotation_spin.value()
        bone.scale_x = self._scale_x_spin.value() or 1.0
        bone.scale_y = self._scale_y_spin.value() or 1.0
        bone.z_index = self._z_index_spin.value()
        bone.opacity = self._opacity_spin.value()
        bone.visible = self._visible_check.isChecked()
        self._refresh_preview()

    def _on_pivot_spin_changed(self, *_args: object) -> None:
        bone = self._selected_bone()
        if bone is None:
            return
        if bone.id == self._root_bone_id:
            bone.pivot_x = self._pivot_x_spin.value()
            bone.pivot_y = self._pivot_y_spin.value()
        else:
            set_pivot_preserving_attach_point(bone, self._pivot_x_spin.value(), self._pivot_y_spin.value())
        self._refresh_preview()

    def _on_attach_spin_changed(self, *_args: object) -> None:
        bone = self._selected_bone()
        if bone is None or bone.id == self._root_bone_id:
            return
        set_attach_point(bone, self._attach_x_spin.value(), self._attach_y_spin.value())
        self._refresh_preview()

    # -- Preview handle drags -------------------------------------------------

    def _on_pivot_dragged(self, new_x: float, new_y: float) -> None:
        bone = self._selected_bone()
        if bone is None or bone.id == self._root_bone_id:
            return
        set_pivot_preserving_attach_point(bone, new_x, new_y)
        self._refresh_preview()

    def _on_attach_dragged(self, new_x: float, new_y: float) -> None:
        bone = self._selected_bone()
        if bone is None or bone.id == self._root_bone_id:
            return
        set_attach_point(bone, new_x, new_y)
        self._refresh_preview()

    # -- Non-destructive tools ------------------------------------------------

    def _on_fit_to_canvas(self) -> None:
        bone = self._selected_bone()
        if bone is None:
            return
        asset = self._assets_by_id().get(bone.asset_id)
        if asset is None or asset.width <= 0 or asset.height <= 0:
            return
        scale = min(self._canvas_width / asset.width, self._canvas_height / asset.height, 1.0)
        bone.scale_x = scale
        bone.scale_y = scale
        self._refresh_inspector()
        self._refresh_preview()

    def _on_reset_scale(self) -> None:
        bone = self._selected_bone()
        if bone is None:
            return
        bone.scale_x = 1.0
        bone.scale_y = 1.0
        self._refresh_inspector()
        self._refresh_preview()

    def _on_set_pivot_center(self) -> None:
        bone = self._selected_bone()
        if bone is None:
            return
        asset = self._assets_by_id().get(bone.asset_id)
        if asset is None:
            return
        new_pivot_x, new_pivot_y = asset.width / 2.0, asset.height / 2.0
        if bone.id == self._root_bone_id:
            bone.pivot_x, bone.pivot_y = new_pivot_x, new_pivot_y
        else:
            set_pivot_preserving_attach_point(bone, new_pivot_x, new_pivot_y)
        self._refresh_inspector()
        self._refresh_preview()

    def _on_crop_to_alpha_bounds(self) -> None:
        """Non-destructive: re-centers the pivot on the PNG's visible (alpha
        > 0) content — never writes a new file or touches the source PNG."""
        bone = self._selected_bone()
        if bone is None:
            return
        asset = self._assets_by_id().get(bone.asset_id)
        project_dir = self._get_project_dir()
        if asset is None:
            return
        resolved = asset_manager.resolve_asset_path(asset, project_dir)
        if resolved is None:
            QMessageBox.warning(self, "Crop Preview to Alpha Bounds", "Cannot locate this asset's PNG file on disk.")
            return
        try:
            with Image.open(resolved) as image:
                bbox = image.convert("RGBA").getbbox()
        except OSError as exc:
            QMessageBox.warning(self, "Crop Preview to Alpha Bounds", f"Cannot read PNG: {exc}")
            return
        if bbox is None:
            QMessageBox.information(self, "Crop Preview to Alpha Bounds", "This PNG is fully transparent.")
            return
        left, top, right, bottom = bbox
        new_pivot_x = (left + right) / 2.0
        new_pivot_y = (top + bottom) / 2.0
        if bone.id == self._root_bone_id:
            bone.pivot_x, bone.pivot_y = new_pivot_x, new_pivot_y
        else:
            set_pivot_preserving_attach_point(bone, new_pivot_x, new_pivot_y)
        self._refresh_inspector()
        self._refresh_preview()

    def _on_scene_reference_size_changed(self, *_args: object) -> None:
        self._canvas_width = float(self._scene_width_spin.value())
        self._canvas_height = float(self._scene_height_spin.value())

    # -- Save / Cancel ----------------------------------------------------

    def _on_save_clicked(self) -> None:
        if self._root_bone_id is None or not self._bones:
            QMessageBox.warning(self, "Cannot Save", "Add at least one part before saving.")
            return
        try:
            validate_rig_template(
                RigTemplate(id="validation-only", name="", root_bone_id=self._root_bone_id, bones=self._bones),
                self._project.assets,
            )
        except RigValidationError as exc:
            QMessageBox.warning(self, "Cannot Save", f"This rig is not valid: {exc}")
            return
        self.accept()
