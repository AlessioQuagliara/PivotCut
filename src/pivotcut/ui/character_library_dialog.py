"""Character Library dialog (Milestone 6C).

A simple management console for ``Project.rig_templates``: preview, name,
part count and Ready/Missing-assets status for every template, plus Add to
Scene / Remove from Library / Import Character File actions. This dialog
holds **no authoritative state** — every action it offers is emitted as a
signal for ``app/main_window.py`` to turn into an undoable command (so
history/dirty-flag/thumbnail-cache bookkeeping stays centralized exactly
like every other mutation in this app); the dialog just stays open and
calls :meth:`refresh` afterwards to reflect the now-current ``Project``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pivotcut.domain.models import Project
from pivotcut.domain.rig import RigTemplate
from pivotcut.services.template_preview_cache import TemplatePreviewCache

_PREVIEW_SIZE = 96


def _qimage_to_icon(image) -> QIcon:  # noqa: ANN001 - QImage, kept loose to avoid a hard Qt-type import here
    return QIcon(QPixmap.fromImage(image))


class CharacterLibraryDialog(QDialog):
    #: (template_id) — MainWindow instantiates the template into the current
    #: frame as a new Rig Instance via one undoable command.
    add_to_scene_requested = Signal(str)

    #: (template_id) — MainWindow removes it from Project.rig_templates via
    #: one undoable command; existing Rig Instances/poses are untouched.
    remove_requested = Signal(str)

    #: MainWindow runs its existing "Import Character File…" flow, then
    #: calls refresh() on this dialog so the new entry shows up immediately.
    import_requested = Signal()

    def __init__(
        self,
        project: Project,
        get_project_dir: Callable[[], Path | None],
        preview_cache: TemplatePreviewCache,
        template_sources: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Character Library")
        self.resize(560, 420)
        self._project = project
        self._get_project_dir = get_project_dir
        self._preview_cache = preview_cache
        self._template_sources = template_sources
        self.setWhatsThis(
            "Every character saved to this project's Character Library, with its part count and "
            "whether all its PNGs are present ('Ready') or need relocating ('Missing assets'). "
            "Add a copy to the current Pose, remove an entry, or import one from a "
            ".pivotcut-rig.json file."
        )
        self._build_ui()
        self.refresh()

    # -- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._list = QListWidget()
        self._list.setIconSize(QSize(_PREVIEW_SIZE, _PREVIEW_SIZE))
        self._list.setWhatsThis(
            "Saved characters in this project. Double-click one (or select it and click Add to "
            "Scene) to place a fresh copy into the current Pose."
        )
        self._list.itemSelectionChanged.connect(self._update_button_states)
        self._list.itemDoubleClicked.connect(lambda *_: self._on_add_to_scene_clicked())
        layout.addWidget(self._list, 1)

        button_row = QHBoxLayout()
        self._add_to_scene_btn = QPushButton("Add to Scene")
        self._add_to_scene_btn.setWhatsThis(
            "Instantiates the selected character into the current Pose as a new, freely posable "
            "copy — the library entry itself is untouched."
        )
        self._add_to_scene_btn.clicked.connect(self._on_add_to_scene_clicked)
        self._remove_btn = QPushButton("Remove from Library")
        self._remove_btn.setWhatsThis(
            "Removes the selected entry from the Character Library only — existing posed "
            "instances already in the timeline, and its PNG assets, are untouched."
        )
        self._remove_btn.clicked.connect(self._on_remove_clicked)
        import_btn = QPushButton("Import Character File…")
        import_btn.setWhatsThis(
            "Imports a character from a .pivotcut-rig.json file into this library. Missing PNGs "
            "are matched automatically where possible, or you're asked to locate them."
        )
        import_btn.clicked.connect(self.import_requested.emit)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(self._add_to_scene_btn)
        button_row.addWidget(self._remove_btn)
        button_row.addStretch(1)
        button_row.addWidget(import_btn)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)

        self._update_button_states()

    # -- Data -----------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the list from the live ``Project.rig_templates``."""
        selected_id = self._selected_template_id()
        self._list.clear()

        assets_by_id = {a.id: a for a in self._project.assets}
        for template in self._project.rig_templates:
            item = QListWidgetItem(self._label_for(template, assets_by_id))
            item.setData(Qt.ItemDataRole.UserRole, template.id)
            image = self._preview_cache.get(template, assets_by_id, self._get_project_dir(), _PREVIEW_SIZE)
            item.setIcon(_qimage_to_icon(image))
            self._list.addItem(item)
            if template.id == selected_id:
                item.setSelected(True)
                self._list.setCurrentItem(item)

        self._update_button_states()

    def _label_for(self, template: RigTemplate, assets_by_id: dict) -> str:
        asset_ids = {a.id for a in self._project.assets}
        is_ready = all(bone.asset_id in asset_ids for bone in template.bones)
        status = "Ready" if is_ready else "Missing assets"
        part_word = "part" if len(template.bones) == 1 else "parts"
        source = self._template_sources.get(template.id)
        source_part = f"\nFrom: {Path(source).name}" if source else ""
        category_part = f"  [{template.category}]" if template.category else ""
        return f"{template.name}{category_part}\n{len(template.bones)} {part_word} — {status}{source_part}"

    def _selected_template_id(self) -> str | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def selected_template(self) -> RigTemplate | None:
        template_id = self._selected_template_id()
        if template_id is None:
            return None
        return next((t for t in self._project.rig_templates if t.id == template_id), None)

    # -- Handlers -----------------------------------------------------------

    def _update_button_states(self) -> None:
        has_selection = self._selected_template_id() is not None
        self._add_to_scene_btn.setEnabled(has_selection)
        self._remove_btn.setEnabled(has_selection)

    def _on_add_to_scene_clicked(self) -> None:
        template_id = self._selected_template_id()
        if template_id is not None:
            self.add_to_scene_requested.emit(template_id)

    def _on_remove_clicked(self) -> None:
        # Confirmation lives in MainWindow (one place, shared with the
        # Characters-menu "Remove Character From Library" action) rather
        # than here, so it never prompts twice for the same removal.
        template = self.selected_template()
        if template is not None:
            self.remove_requested.emit(template.id)
