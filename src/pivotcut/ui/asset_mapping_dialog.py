"""Asset Mapping Dialog (Milestone 6C).

Shown after ``rig_template_io.auto_resolve_manifest()`` has already
resolved whatever it could automatically (relative path, absolute path,
filename/dimensions match) — this dialog handles whatever is *still*
missing, one row per :class:`~pivotcut.services.rig_template_io.AssetReference`,
with a manual "Locate PNG…" per row plus a global "Auto-match by filename"
retry (useful if the user just located a folder full of sibling PNGs via
one row and wants the rest picked up automatically) and "Skip for now" to
finish the import anyway — missing parts render as the same dashed
placeholder used everywhere else in the app, never a crash.

Cancel here aborts the whole Character File import (the caller must not
touch ``Project.rig_templates`` if this dialog is rejected); "Skip for
now" accepts with whatever got resolved during this session, which may be
a partial (or even empty) mapping.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pivotcut.domain.models import Project
from pivotcut.services import asset_manager
from pivotcut.services.rig_template_io import AssetReference, find_asset_by_fingerprint

_ICON_SIZE = 48


class AssetMappingDialog(QDialog):
    def __init__(
        self,
        project: Project,
        get_project_dir: Callable[[], Path | None],
        missing_refs: list[AssetReference],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Missing Character Assets")
        self.resize(560, 420)
        self._project = project
        self._get_project_dir = get_project_dir
        self._refs = missing_refs
        self._mapping: dict[str, str] = {}
        self._rows: dict[str, dict[str, QWidget]] = {}
        self.setWhatsThis(
            "Some PNGs referenced by the character being imported weren't found automatically "
            "in this project. Locate each one, try Auto-match by filename, or Skip for now to "
            "finish the import with placeholders for whatever's still missing — those parts "
            "render as a dashed placeholder, never a crash, and can be relocated later."
        )
        self._build_ui()

    # -- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        intro = QLabel(
            "This character needs PNGs that weren't found automatically. "
            "Locate each one below, try Auto-match by filename, or Skip for "
            "now — parts still missing render as a placeholder, never a crash."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._list = QListWidget()
        layout.addWidget(self._list, 1)
        for ref in self._refs:
            self._add_row(ref)

        auto_match_btn = QPushButton("Auto-match by filename")
        auto_match_btn.setToolTip(
            "Looks for an asset already in this project with the same "
            "filename and dimensions as each still-missing part."
        )
        auto_match_btn.clicked.connect(self._on_auto_match_clicked)
        layout.addWidget(auto_match_btn)

        buttons = QDialogButtonBox()
        skip_btn = buttons.addButton("Skip for now", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_btn = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        skip_btn.setToolTip("Finish the import with whatever was resolved — missing parts stay as placeholders.")
        skip_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _add_row(self, ref: AssetReference) -> None:
        item = QListWidgetItem(self._list)
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(4, 2, 4, 2)

        icon_label = QLabel()
        icon_label.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        icon_label.setStyleSheet("background: #3a3a3a; border: 1px dashed #888888;")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row_layout.addWidget(icon_label)

        text_label = QLabel(f"{ref.name}  ({ref.width}×{ref.height})")
        row_layout.addWidget(text_label, 1)

        status_label = QLabel("Missing")
        status_label.setStyleSheet("color: #e06666;")
        row_layout.addWidget(status_label)

        locate_btn = QPushButton("Locate PNG…")
        locate_btn.setWhatsThis("Pick the PNG file on disk that corresponds to this missing part.")
        row_layout.addWidget(locate_btn)

        item.setSizeHint(row_widget.sizeHint())
        self._list.addItem(item)
        self._list.setItemWidget(item, row_widget)
        self._rows[ref.id] = {"status_label": status_label, "icon_label": icon_label, "locate_btn": locate_btn}
        locate_btn.clicked.connect(lambda *_args, r=ref: self._on_locate_clicked(r))

    # -- Resolution ---------------------------------------------------------

    def _mark_resolved(self, ref: AssetReference, asset_id: str, preview_path: Path | None) -> None:
        self._mapping[ref.id] = asset_id
        widgets = self._rows.get(ref.id)
        if widgets is None:
            return
        status_label = widgets["status_label"]
        status_label.setText("Ready")
        status_label.setStyleSheet("color: #7fbf7f;")
        widgets["locate_btn"].setEnabled(False)
        if preview_path is not None:
            pixmap = QPixmap(str(preview_path))
            if not pixmap.isNull():
                widgets["icon_label"].setPixmap(
                    pixmap.scaled(
                        _ICON_SIZE, _ICON_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                    )
                )

    def _on_locate_clicked(self, ref: AssetReference) -> None:
        path = asset_manager.pick_png_file_via_dialog(self)
        if path is None:
            return
        try:
            asset = asset_manager.import_png_asset(self._project, path, self._get_project_dir())
        except asset_manager.AssetImportError as exc:
            QMessageBox.critical(self, "Cannot Import PNG", str(exc))
            return
        self._mark_resolved(ref, asset.id, path)

    def _on_auto_match_clicked(self) -> None:
        matched = 0
        for ref in self._refs:
            if ref.id in self._mapping:
                continue
            existing = find_asset_by_fingerprint(self._project.assets, ref)
            if existing is not None:
                self._mark_resolved(ref, existing.id, None)
                matched += 1
        if matched == 0:
            QMessageBox.information(
                self, "Auto-match by Filename", "No additional matches found among this project's assets."
            )

    # -- Result ---------------------------------------------------------------

    def result_mapping(self) -> dict[str, str]:
        """``{old asset id: resolved project asset id}`` for whatever this
        session resolved — possibly partial, possibly empty (all skipped)."""
        return dict(self._mapping)
