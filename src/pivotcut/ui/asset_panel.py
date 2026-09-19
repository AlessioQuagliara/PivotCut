"""Left-side "Assets" panel: PNG import, registry list, rig/layer creation triggers.

Rig/layer creation itself is orchestrated by ``MainWindow`` (it needs the
current frame, which this panel doesn't own) — this panel only imports/
lists assets and emits ``create_rig_requested``/``create_layer_requested``
with the selection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pivotcut.domain.layer import LAYER_TYPES
from pivotcut.domain.models import Project
from pivotcut.services import asset_manager

_THUMB_ICON_SIZE = 48
_LAYER_TYPE_LABELS: dict[str, str] = {
    "background": "Background",
    "midground": "Midground",
    "foreground": "Foreground",
}


class AssetPanel(QWidget):
    create_rig_requested = Signal(str)  # asset_id
    create_layer_requested = Signal(str, str)  # asset_id, layer_type

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project: Project | None = None
        self._get_project_dir: Callable[[], Path | None] = lambda: None
        self._rig_creation_allowed = True
        self._rig_creation_reason = ""
        self._build_ui()
        self.setWhatsThis(
            "The Assets panel. Import transparent PNGs here, then select one and either turn it "
            "into a posable Part on the canvas (Quick Add) or add it as a Background/Midground/"
            "Foreground layer. For a character built from several PNGs, use Characters -> Build "
            "Character from PNGs... in the menu bar instead."
        )

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("Assets")
        bold_font = title.font()
        bold_font.setBold(True)
        title.setFont(bold_font)
        layout.addWidget(title)

        self._import_btn = QPushButton("Import PNG…")
        self._import_btn.setToolTip("Adds a transparent PNG to this project's asset registry.")
        self._import_btn.setWhatsThis(
            "Opens a file picker for a PNG with transparency. The file is added to this "
            "project's asset registry and appears in the list below — it isn't placed in the "
            "scene yet, use Quick Add or Add Layer for that."
        )
        self._import_btn.clicked.connect(self._on_import_clicked)
        layout.addWidget(self._import_btn)

        self._list = QListWidget()
        self._list.setIconSize(QSize(_THUMB_ICON_SIZE, _THUMB_ICON_SIZE))
        self._list.setWhatsThis(
            "Every PNG imported into this project. Select one, then use Quick Add Single PNG "
            "Part or Add Layer From Selected Asset below."
        )
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list, 1)

        self._create_rig_btn = QPushButton("Quick Add Single PNG Part")
        self._create_rig_btn.setToolTip(
            "Quick Add: turns the selected PNG into one posable Part on the "
            "canvas — move/rotate/scale/delete it right away.\n"
            "For a character made of several PNG parts (body, arms, head…), "
            "use Characters -> Build Character from PNGs… instead."
        )
        self._create_rig_btn.setWhatsThis(
            "Turns the selected PNG into a single one-Part character on the current Pose, "
            "selected and ready to move/rotate/scale immediately — no dialog needed. Good for "
            "props or simple elements; for a multi-part character use Characters -> Build "
            "Character from PNGs… instead."
        )
        self._create_rig_btn.setEnabled(False)
        self._create_rig_btn.clicked.connect(self._on_create_rig_clicked)
        layout.addWidget(self._create_rig_btn)

        self._hint_label = QLabel("")
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet("color: #999999; font-size: 11px;")
        layout.addWidget(self._hint_label)

        layer_row = QHBoxLayout()
        layer_row.setSpacing(6)
        self._layer_type_combo = QComboBox()
        for layer_type in LAYER_TYPES:
            self._layer_type_combo.addItem(_LAYER_TYPE_LABELS[layer_type], layer_type)
        self._layer_type_combo.setCurrentIndex(LAYER_TYPES.index("midground"))
        self._layer_type_combo.setWhatsThis(
            "Which depth plane the new layer belongs to: Background (behind everything, most "
            "parallax shift), Midground, or Foreground (in front, least/no parallax shift). "
            "Fine-tune the exact amount afterwards with the layer's Z Depth in the Inspector."
        )
        layer_row.addWidget(self._layer_type_combo, 0)

        self._add_layer_btn = QPushButton("Add Layer From Selected Asset")
        self._add_layer_btn.setWhatsThis(
            "Adds the selected PNG to the current Pose as a new layer of the chosen type, sized "
            "to fill the canvas. Edit its position/scale/depth afterwards from the Inspector."
        )
        self._add_layer_btn.setEnabled(False)
        self._add_layer_btn.clicked.connect(self._on_add_layer_clicked)
        layer_row.addWidget(self._add_layer_btn, 1)
        layout.addLayout(layer_row)

    # -- Project binding ----------------------------------------------------

    def set_project(self, project: Project, get_project_dir: Callable[[], Path | None]) -> None:
        self._project = project
        self._get_project_dir = get_project_dir
        self.refresh_assets()

    def refresh_assets(self) -> None:
        self._list.clear()
        if self._project is None:
            return
        project_dir = self._get_project_dir()
        for asset in self._project.assets:
            item = QListWidgetItem(f"{asset.name}  ({asset.width}×{asset.height})")
            item.setData(Qt.ItemDataRole.UserRole, asset.id)
            resolved = asset_manager.resolve_asset_path(asset, project_dir)
            if resolved is not None:
                pixmap = QPixmap(str(resolved))
                if not pixmap.isNull():
                    thumbnail = pixmap.scaled(
                        _THUMB_ICON_SIZE,
                        _THUMB_ICON_SIZE,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    item.setIcon(QIcon(thumbnail))
            self._list.addItem(item)
        self._update_create_rig_button_state()

    def selected_asset_id(self) -> str | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    # -- Rig creation gating (driven by MainWindow; unrestricted since Milestone 6A) -----

    def set_rig_creation_enabled(self, enabled: bool, reason: str = "") -> None:
        self._rig_creation_allowed = enabled
        self._rig_creation_reason = reason
        self._update_create_rig_button_state()

    def _update_create_rig_button_state(self) -> None:
        has_selection = self._list.currentItem() is not None
        self._create_rig_btn.setEnabled(has_selection and self._rig_creation_allowed)
        self._hint_label.setText("" if self._rig_creation_allowed else self._rig_creation_reason)
        # Multiple layers per frame are always allowed (no "one per frame"
        # restriction like rigs), so Add Layer only needs a selected asset.
        self._add_layer_btn.setEnabled(has_selection)

    # -- Handlers -----------------------------------------------------------

    def _on_selection_changed(self) -> None:
        self._update_create_rig_button_state()

    def _on_import_clicked(self) -> None:
        if self._project is None:
            return
        file_path = asset_manager.pick_png_file_via_dialog(self)
        if file_path is None:
            return
        try:
            asset_manager.import_png_asset(self._project, file_path, self._get_project_dir())
        except asset_manager.AssetImportError as exc:
            QMessageBox.critical(self, "Cannot Import PNG", str(exc))
            return
        self.refresh_assets()

    def _on_create_rig_clicked(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        asset_id = item.data(Qt.ItemDataRole.UserRole)
        self.create_rig_requested.emit(asset_id)

    def _on_add_layer_clicked(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        asset_id = item.data(Qt.ItemDataRole.UserRole)
        layer_type = self._layer_type_combo.currentData()
        self.create_layer_requested.emit(asset_id, layer_type)
