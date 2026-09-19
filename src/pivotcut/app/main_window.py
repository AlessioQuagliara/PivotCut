"""Main application window: toolbar + asset panel + canvas + timeline + inspector.

Milestone 4 adds timeline playback (respecting fps/exposure), PNG sequence
export and H.264 MP4 export via FFmpeg. Export rendering never goes through
this window or ``CanvasView`` — it is delegated entirely to the headless
``services.export_renderer``/``services.ffmpeg_export`` services; this
window only drives the file dialogs, the progress UI and error reporting.
"""

from __future__ import annotations

import copy
import subprocess
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWhatsThis,
    QWidget,
)

from pivotcut.domain import commands, playback, timeline
from pivotcut.domain.assets import Asset
from pivotcut.domain.layer import create_layer_from_asset
from pivotcut.domain.models import Frame, Project, new_project
from pivotcut.domain.rig import (
    Bone,
    Rig,
    RigTemplate,
    RigValidationError,
    create_rig_from_asset,
    duplicate_bone,
    find_bone_in_list,
    instantiate_rig_template,
    remove_bone_reparent_children,
    remove_bone_subtree,
    reparent_bone,
    rig_template_from_rig,
    set_root_bone,
    validate_rig_template,
)
from pivotcut.services import asset_manager, export_renderer, ffmpeg_export, project_io, rig_template_io
from pivotcut.services.playback_controller import PlaybackController
from pivotcut.services.playback_engine import PlaybackEngine
from pivotcut.services.template_preview_cache import TemplatePreviewCache
from pivotcut.services.thumbnail_cache import ThumbnailCache
from pivotcut.ui.asset_mapping_dialog import AssetMappingDialog
from pivotcut.ui.asset_panel import AssetPanel
from pivotcut.ui.canvas_view import CanvasView
from pivotcut.ui.character_library_dialog import CharacterLibraryDialog
from pivotcut.ui.inspector_panel import InspectorPanel
from pivotcut.ui.rig_builder_dialog import RigBuilderDialog
from pivotcut.ui.timeline_widget import TimelineWidget

PROJECT_FILE_FILTER = "PivotCut Project (*.pivotcut.json)"

#: Widgets that must keep receiving a literal Space keystroke as text input
#: rather than have it hijacked as the global Play/Stop toggle.
_TEXT_INPUT_WIDGET_TYPES = (QLineEdit, QAbstractSpinBox, QComboBox)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PivotCut")
        self.resize(1500, 900)

        self._project: Project = new_project()
        self._current_path: Path | None = None
        self._history = commands.UndoRedoStack()
        self._thumbnail_cache = ThumbnailCache()
        self._template_preview_cache = TemplatePreviewCache()
        # Which Character File a given rig_templates[i] was imported from, if
        # any (id -> path string) — a display-only convenience for the
        # Character Library dialog's "From: ..." line, deliberately kept out
        # of Project/RigTemplate (not serialized, reset on New/Open) rather
        # than adding a new persisted field for it.
        self._template_sources: dict[str, str] = {}
        self._is_exporting = False
        self._is_dirty = False

        self._playback_engine = PlaybackEngine()
        self._playback = PlaybackController(self)
        self._playback.set_project(self._project)
        self._playback.frame_changed.connect(self._on_project_state_changed)
        # Smooth Animation only (see PlaybackController.sub_frame_changed):
        # a lightweight canvas-only resync, deliberately not the full
        # _on_project_state_changed pipeline, which would otherwise rebuild
        # the whole timeline thumbnail strip on every sub-frame tick.
        self._playback.sub_frame_changed.connect(self._sync_canvas)
        self._playback.playback_started.connect(self._on_playback_started)
        self._playback.playback_stopped.connect(self._on_playback_stopped)

        self._canvas = CanvasView(self._project.scene_settings)
        self._canvas.playback_requested.connect(self._toggle_playback)
        self._canvas.selection_changed.connect(self._on_canvas_selection_changed)
        self._canvas.bone_transform_committed.connect(self._on_bone_transform_committed)

        self._timeline = TimelineWidget(self._thumbnail_cache)
        self._timeline.set_command_executor(self._execute_command)
        self._timeline.set_project(self._project, self._project_dir)
        self._timeline.project_changed.connect(self._on_project_changed)

        self._asset_panel = AssetPanel()
        self._asset_panel.set_project(self._project, self._project_dir)
        self._asset_panel.create_rig_requested.connect(self._on_create_rig_requested)
        self._asset_panel.create_layer_requested.connect(self._on_create_layer_requested)

        self._inspector = InspectorPanel()
        self._inspector.set_assets(self._project.assets)
        self._inspector.changed.connect(self._on_inspector_changed)
        self._inspector.layer_edit_committed.connect(self._on_layer_edit_committed)
        self._inspector.camera_edit_committed.connect(self._on_camera_edit_committed)
        self._inspector.bone_edit_committed.connect(self._on_bone_edit_committed)
        self._inspector.bone_reparent_requested.connect(self._on_bone_reparent_requested)

        self.setCentralWidget(self._build_central_widget())
        self._build_actions()
        self._build_menu_bar()
        self._build_toolbar()
        self._build_shortcuts()

        self._status_label = QLabel()
        self.statusBar().addPermanentWidget(self._status_label)
        self._on_project_state_changed()
        self._update_window_title()

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    # -- Layout -------------------------------------------------------------

    def _build_central_widget(self) -> QWidget:
        canvas_and_timeline = QWidget()
        layout = QVBoxLayout(canvas_and_timeline)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._canvas, 1)
        layout.addWidget(self._timeline, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._asset_panel)
        splitter.addWidget(canvas_and_timeline)
        splitter.addWidget(self._inspector)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([220, 1000, 260])
        return splitter

    def _build_actions(self) -> None:
        self._new_action = QAction("New Project", self)
        self._new_action.setShortcut(QKeySequence.StandardKey.New)
        self._new_action.setToolTip("New Project (Cmd+N)")
        self._new_action.setWhatsThis(
            "Starts a brand-new, empty project with one blank Pose at the default canvas size. "
            "If the current project has unsaved changes, PivotCut asks whether to save them first."
        )
        self._new_action.triggered.connect(self._on_new_project)

        self._open_action = QAction("Open…", self)
        self._open_action.setShortcut(QKeySequence.StandardKey.Open)
        self._open_action.setToolTip("Open… (Cmd+O)")
        self._open_action.setWhatsThis(
            "Opens a previously saved .pivotcut.json project file. If the current project has "
            "unsaved changes, PivotCut asks whether to save them first."
        )
        self._open_action.triggered.connect(self._on_open_project)

        self._save_action = QAction("Save", self)
        self._save_action.setShortcut(QKeySequence.StandardKey.Save)
        self._save_action.setToolTip("Save (Cmd+S)")
        self._save_action.setWhatsThis(
            "Saves the current project. For a never-saved (Untitled) project this behaves like "
            "Save As… and asks where to put the file."
        )
        self._save_action.triggered.connect(self._on_save_project)

        self._save_as_action = QAction("Save As…", self)
        self._save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        self._save_as_action.setToolTip("Save As… (Shift+Cmd+S)")
        self._save_as_action.setWhatsThis(
            "Saves the current project to a new .pivotcut.json file, leaving any previously "
            "saved file untouched."
        )
        self._save_as_action.triggered.connect(self._on_save_project_as)

        self._undo_action = QAction("Undo", self)
        self._undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self._undo_action.setWhatsThis(
            "Reverts the last editing action — moving/rotating/scaling a Part, adding or "
            "deleting a Pose, an Inspector edit, and so on. PivotCut keeps a full undo history "
            "for the current session (cleared on New/Open)."
        )
        self._undo_action.triggered.connect(self._undo)
        self._undo_action.setEnabled(False)

        self._redo_action = QAction("Redo", self)
        self._redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self._redo_action.setWhatsThis("Re-applies the last action that was undone.")
        self._redo_action.triggered.connect(self._redo)
        self._redo_action.setEnabled(False)

        self._play_action = QAction("Play", self)
        self._play_action.setToolTip("Play/Stop timeline playback (Space)")
        self._play_action.setWhatsThis(
            "Plays the timeline back from the current Pose, respecting each Pose's Exposure "
            "(how many video frames it's held for) and the project's FPS. Press Space or click "
            "Play/Stop again to stop. Editing is locked while playing."
        )
        self._play_action.triggered.connect(self._toggle_playback)

        self._loop_action = QAction("Loop", self)
        self._loop_action.setCheckable(True)
        self._loop_action.setToolTip("Restart from the first frame after the last when playing")
        self._loop_action.setWhatsThis(
            "When enabled, playback jumps back to the first Pose after reaching the last one "
            "instead of stopping there."
        )
        self._loop_action.toggled.connect(self._playback.set_loop)

        self._smooth_animation_action = QAction("Smooth Animation", self)
        self._smooth_animation_action.setCheckable(True)
        self._smooth_animation_action.setToolTip("Ease motion between Poses instead of a hard cut")
        self._smooth_animation_action.setWhatsThis(
            "When on, PivotCut eases bone rotations/positions, the camera and environment layers "
            "smoothly between one Pose and the next — during both live playback and PNG/MP4 "
            "export — instead of holding each Pose as a hard cut for its whole Exposure. Off by "
            "default, so every project keeps its exact original look until you turn this on. "
            "Fine-tuning individual properties' easing curves isn't available from this UI yet — "
            "everything eases with the same gentle default curve."
        )
        self._smooth_animation_action.toggled.connect(self._on_smooth_animation_toggled)

        self._export_png_action = QAction("Export PNG Sequence…", self)
        self._export_png_action.setWhatsThis(
            "Renders every Pose to a numbered PNG sequence (frame_0001.png, frame_0002.png, …) "
            "in a folder you choose, respecting Exposure. Never modifies the project. Works with "
            "no extra setup — no FFmpeg needed."
        )
        self._export_png_action.triggered.connect(self._on_export_png_sequence)

        self._export_mp4_action = QAction("Export MP4…", self)
        self._export_mp4_action.setWhatsThis(
            "Encodes the timeline to an H.264 .mp4 file via FFmpeg. Needs FFmpeg installed "
            "(brew install ffmpeg) — if it isn't found, PivotCut offers Export PNG Sequence… as "
            "a fallback instead."
        )
        self._export_mp4_action.triggered.connect(self._on_export_mp4)

        self._new_frame_action = QAction("New Frame", self)
        self._new_frame_action.setToolTip("N")
        self._new_frame_action.setWhatsThis("Inserts a new Pose right after the current one and selects it. Shortcut: N.")
        self._new_frame_action.triggered.connect(self._timeline.new_frame)

        self._delete_frame_action = QAction("Delete Frame", self)
        self._delete_frame_action.setToolTip("Backspace / Delete")
        self._delete_frame_action.setWhatsThis(
            "Deletes the current Pose. A project must always keep at least one Pose, so this is "
            "disabled when only one remains. Shortcut: Backspace/Delete when no Part is selected."
        )
        self._delete_frame_action.triggered.connect(self._timeline.delete_frame)

        self._new_character_rig_action = QAction("Build Character from PNGs…", self)
        self._new_character_rig_action.setToolTip(
            "Build Character: assemble a multi-part character (body, arms, "
            "head…) with parent/child hierarchy in the Character Rig Builder."
        )
        self._new_character_rig_action.setWhatsThis(
            "Opens the Character Rig Builder: import several PNGs, add them as Parts, pick a "
            "Root (usually the torso), and attach every other Part to a parent to form a "
            "hierarchy — children inherit their parent's pose. Saving here places the finished "
            "character into the current Pose only; use Save Selected Rig as Template… "
            "afterwards to also keep it in the Character Library for reuse."
        )
        self._new_character_rig_action.triggered.connect(self._on_new_character_rig)

        self._quick_create_rig_action = QAction("Quick Add Single PNG Part", self)
        self._quick_create_rig_action.setToolTip(
            "Quick Add: turns the selected PNG into one posable Part, ready "
            "to move/rotate/scale/delete right away — no dialog needed."
        )
        self._quick_create_rig_action.setWhatsThis(
            "Select a PNG in the Assets panel first, then use this to turn it into a single "
            "one-Part character on the current Pose — no Rig Builder needed. Good for props or "
            "simple background elements; for a multi-part character use Build Character from "
            "PNGs… instead."
        )
        self._quick_create_rig_action.triggered.connect(self._on_quick_create_rig)

        self._save_character_to_file_action = QAction("Save Selected Character to File…", self)
        self._save_character_to_file_action.setToolTip(
            "Turns the selected character rig instance into a portable "
            ".pivotcut-rig.json file — doesn't touch its pose or the library."
        )
        self._save_character_to_file_action.setWhatsThis(
            "Exports the selected character (click a Part of it on the canvas first, if the "
            "frame has more than one) as a standalone .pivotcut-rig.json file you can share or "
            "import into another PivotCut project. This is a pure file export: it doesn't "
            "change the current Pose or the Character Library."
        )
        self._save_character_to_file_action.triggered.connect(self._on_save_character_to_file)

        self._import_character_file_action = QAction("Import Character File…", self)
        self._import_character_file_action.setToolTip(
            "Import a .pivotcut-rig.json Character File into this project's Character Library."
        )
        self._import_character_file_action.setWhatsThis(
            "Adds a character from a .pivotcut-rig.json file to this project's Character "
            "Library. PivotCut tries to match its PNGs to assets already in this project "
            "automatically; anything it can't match, it asks you to locate — or you can Skip "
            "for now and fix it later (missing parts render as a placeholder, never a crash)."
        )
        self._import_character_file_action.triggered.connect(self._on_import_character_file)

        self._open_character_library_action = QAction("Character Library…", self)
        self._open_character_library_action.setToolTip(
            "Browse this project's saved characters, add one to the current frame, or remove one."
        )
        self._open_character_library_action.setWhatsThis(
            "Opens the Character Library: every character saved to this project via Save "
            "Selected Rig as Template… or Import Character File…. From here you can add a copy "
            "to the current Pose, remove a saved entry, or import another Character File."
        )
        self._open_character_library_action.triggered.connect(self._on_open_character_library)

        self._add_character_from_library_action = QAction("Add Selected Character to Scene", self)
        self._add_character_from_library_action.setWhatsThis(
            "Asks which saved character to use, then instantiates a fresh, freely posable copy "
            "of it into the current Pose, centered on the canvas. The library entry itself is "
            "untouched."
        )
        self._add_character_from_library_action.triggered.connect(self._on_add_character_from_library)

        self._remove_character_from_library_action = QAction("Remove Character From Library", self)
        self._remove_character_from_library_action.setWhatsThis(
            "Removes a character from the Character Library only. Any copies of it already "
            "posed in the timeline, and its PNG assets, are left untouched."
        )
        self._remove_character_from_library_action.triggered.connect(self._on_remove_character_from_library)

        self._reveal_character_assets_action = QAction("Reveal Character Assets", self)
        self._reveal_character_assets_action.setToolTip(
            "Shows the PNG files behind a Character Library entry in Finder."
        )
        self._reveal_character_assets_action.setWhatsThis(
            "Opens Finder with every PNG used by a Character Library entry selected — handy for "
            "checking where the source files actually live on disk."
        )
        self._reveal_character_assets_action.triggered.connect(self._on_reveal_character_assets)

        self._save_rig_as_template_action = QAction("Save Selected Rig as Template…", self)
        self._save_rig_as_template_action.setToolTip(
            "Adds the selected character rig instance to the Character Library directly, without a file."
        )
        self._save_rig_as_template_action.setWhatsThis(
            "Saves the selected character's current structure (Parts, hierarchy, pivots, attach "
            "points) into this project's Character Library under a name you choose, so it can be "
            "reused on other Poses via Add Selected Character to Scene. Its current pose "
            "(rotation/position on this Pose) is not part of what gets saved."
        )
        self._save_rig_as_template_action.triggered.connect(self._on_save_rig_as_template)

        self._export_rig_template_action = QAction("Export Selected Rig Template…", self)
        self._export_rig_template_action.setToolTip("Exports a Character Library entry (not a scene instance) to a file.")
        self._export_rig_template_action.setWhatsThis(
            "Exports a saved Character Library entry to a .pivotcut-rig.json file you can share "
            "or import into another project. Unlike Save Selected Character to File…, this "
            "exports the library entry, not a specific posed instance from the timeline."
        )
        self._export_rig_template_action.triggered.connect(self._on_export_rig_template)

        self._edit_selected_rig_action = QAction("Edit Selected Character Rig…", self)
        self._edit_selected_rig_action.setWhatsThis(
            "Reopens the Character Rig Builder for the selected character so you can add/remove "
            "Parts or re-arrange the hierarchy. This edits only this Pose's rig instance — it "
            "never updates a Character Library entry the character may have come from."
        )
        self._edit_selected_rig_action.triggered.connect(self._on_edit_selected_rig)

        self._delete_part_action = QAction("Delete Selected Part", self)
        self._delete_part_action.setToolTip("Backspace / Delete")
        self._delete_part_action.setWhatsThis(
            "Deletes the selected Part. Deleting the character's Root Part removes the whole "
            "character from this Pose (asks to confirm); deleting a Part that has children asks "
            "whether to delete the whole subtree or keep the children by reparenting them."
        )
        self._delete_part_action.triggered.connect(self._on_delete_selected_part)

        self._duplicate_part_action = QAction("Duplicate Selected Part", self)
        self._duplicate_part_action.setWhatsThis(
            "Duplicates the selected Part (not the whole character) as a sibling with the same "
            "parent, pose and appearance. The Root Part can't be duplicated this way."
        )
        self._duplicate_part_action.triggered.connect(self._on_duplicate_selected_part)

        self._bring_forward_action = QAction("Bring Forward", self)
        self._bring_forward_action.setWhatsThis(
            "Raises the selected Part's Z Index by 1, drawing it in front of Parts with a lower value."
        )
        self._bring_forward_action.triggered.connect(self._on_bring_forward)

        self._send_backward_action = QAction("Send Backward", self)
        self._send_backward_action.setWhatsThis(
            "Lowers the selected Part's Z Index by 1, drawing it behind Parts with a higher value."
        )
        self._send_backward_action.triggered.connect(self._on_send_backward)

        self._set_root_part_action = QAction("Set Selected Part as Root", self)
        self._set_root_part_action.setWhatsThis(
            "Makes the selected Part the new Root of its character rig — every other Part "
            "becomes (directly or indirectly) its child. Useful for re-anchoring a hierarchy "
            "without rebuilding the character from scratch."
        )
        self._set_root_part_action.triggered.connect(self._on_set_selected_part_as_root)

        self._reparent_part_action = QAction("Reparent Selected Part…", self)
        self._reparent_part_action.setWhatsThis(
            "Attaches the selected Part to a different parent Part within the same character — "
            "only where it's anchored in the hierarchy changes, its own appearance doesn't. Not "
            "available for the Root Part, which has no parent."
        )
        self._reparent_part_action.triggered.connect(self._on_reparent_selected_part)

        self._getting_started_action = QAction("Getting Started…", self)
        self._getting_started_action.setWhatsThis(
            "Shows a short step-by-step introduction to PivotCut's workflow: import PNGs, build "
            "a character, pose it, add Poses, and export."
        )
        self._getting_started_action.triggered.connect(self._on_getting_started)

        # Qt's own ready-made "?" toggle: click it, then click any control in
        # the app to see that control's help text (its setWhatsThis()
        # content) in a small popup — this is the per-feature, click-to-learn
        # help the rest of this file's setWhatsThis() calls feed into.
        self._whats_this_action = QWhatsThis.createAction(self)

    def _build_menu_bar(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self._new_action)
        file_menu.addAction(self._open_action)
        file_menu.addSeparator()
        file_menu.addAction(self._save_action)
        file_menu.addAction(self._save_as_action)

        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addAction(self._undo_action)
        edit_menu.addAction(self._redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self._smooth_animation_action)

        characters_menu = self.menuBar().addMenu("&Characters")
        characters_menu.addAction(self._new_character_rig_action)
        characters_menu.addAction(self._quick_create_rig_action)
        characters_menu.addSeparator()
        characters_menu.addAction(self._save_character_to_file_action)
        characters_menu.addAction(self._import_character_file_action)
        characters_menu.addAction(self._open_character_library_action)
        characters_menu.addSeparator()
        characters_menu.addAction(self._add_character_from_library_action)
        characters_menu.addAction(self._remove_character_from_library_action)
        characters_menu.addAction(self._reveal_character_assets_action)
        characters_menu.addSeparator()
        characters_menu.addAction(self._save_rig_as_template_action)
        characters_menu.addAction(self._export_rig_template_action)
        characters_menu.addSeparator()
        characters_menu.addAction(self._edit_selected_rig_action)
        characters_menu.addSeparator()
        characters_menu.addAction(self._delete_part_action)
        characters_menu.addAction(self._duplicate_part_action)
        characters_menu.addAction(self._bring_forward_action)
        characters_menu.addAction(self._send_backward_action)
        characters_menu.addAction(self._set_root_part_action)
        characters_menu.addAction(self._reparent_part_action)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._getting_started_action)
        help_menu.addSeparator()
        help_menu.addAction(self._whats_this_action)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        toolbar.addAction(self._new_action)
        toolbar.addAction(self._open_action)
        toolbar.addAction(self._save_action)
        toolbar.addSeparator()
        toolbar.addAction(self._undo_action)
        toolbar.addAction(self._redo_action)
        toolbar.addSeparator()
        toolbar.addAction(self._play_action)
        toolbar.addAction(self._loop_action)
        toolbar.addAction(self._smooth_animation_action)
        toolbar.addAction(self._new_frame_action)
        toolbar.addAction(self._delete_frame_action)
        toolbar.addSeparator()
        toolbar.addAction(self._new_character_rig_action)
        toolbar.addAction(self._quick_create_rig_action)
        toolbar.addAction(self._open_character_library_action)
        toolbar.addSeparator()
        toolbar.addAction(self._export_png_action)
        toolbar.addAction(self._export_mp4_action)
        toolbar.addSeparator()
        toolbar.addAction(self._whats_this_action)

    def _build_shortcuts(self) -> None:
        # Space is handled by the application-wide eventFilter below (global
        # Play/Stop) rather than as a QShortcut, so it can special-case
        # CanvasView (Space+drag pan) and text-input widgets. See eventFilter.
        # Backspace/Delete are handled there too (context-sensitive: delete
        # the selected Part if one is selected, else delete the current
        # frame — see eventFilter), instead of an unconditional QShortcut.
        QShortcut(QKeySequence(Qt.Key.Key_N), self, activated=self._timeline.new_frame)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=self._timeline.previous_frame)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=self._timeline.next_frame)

    # -- Help -------------------------------------------------------------

    def _on_getting_started(self) -> None:
        QMessageBox.information(
            self,
            "Getting Started with PivotCut",
            "1. Import PNGs — click “Import PNG…” in the Assets panel (left) to bring "
            "in transparent character parts, props and background art.\n\n"
            "2. Build a character — select one PNG and click “Quick Add Single PNG Part” "
            "for one posable piece, or Characters → Build Character from PNGs… to "
            "assemble several parts into a hierarchical rig (body, arms, head…).\n\n"
            "3. Pose it — click a Part on the canvas to select it, then drag to move, "
            "Shift+drag to rotate, or use the handles that appear. Fine-tune exact values in "
            "the Inspector panel (right).\n\n"
            "4. Add Poses — click “New Frame” (or press N) in the timeline to add "
            "another Pose. PivotCut plays them back at the project's FPS, each held for its own "
            "Exposure (in video frames).\n\n"
            "5. Layers & camera — add Background/Midground/Foreground layers from the Assets "
            "panel for parallax depth; adjust the per-Pose camera from the Inspector when "
            "nothing else is selected.\n\n"
            "6. Export — “Export PNG Sequence…” or “Export MP4…” in the "
            "toolbar render the animation. MP4 needs FFmpeg (brew install ffmpeg); PNG Sequence "
            "always works.\n\n"
            "Tip: click the “?” button in the toolbar (or press Shift+F1), then click "
            "almost anything in the app for an explanation of that specific control.",
        )

    # -- Project/frame helpers ------------------------------------------------

    def _current_frame(self) -> Frame:
        return self._project.frames[self._project.current_frame_index]

    def _project_dir(self) -> Path | None:
        return self._current_path.parent if self._current_path else None

    def _sync_canvas(self) -> None:
        scene = self._project.scene_settings
        self._canvas.sync_scene(
            self._frame_to_render(), self._project.assets, self._project_dir(), scene.width, scene.height
        )

    def _frame_to_render(self) -> Frame:
        """The Frame to actually draw on the canvas right now.

        Ordinarily just the selected Pose. While playback is running *and*
        ``Project.smooth_animation_enabled`` is on, this instead asks
        ``services.playback_engine`` for a virtual, eased in-between frame
        toward the next Pose, at ``self._playback.current_sub_progress`` —
        the same computation ``services.export_renderer`` uses for export,
        so what's previewed live matches what gets exported. Purely a
        display value: never written back into the project, never selected,
        never touches undo/redo.
        """
        if not (self._project.smooth_animation_enabled and self._playback.is_playing):
            return self._current_frame()
        progress = self._playback.current_sub_progress
        if progress <= 0.0:
            return self._current_frame()
        index = self._project.current_frame_index
        next_index = index + 1 if index + 1 < len(self._project.frames) else index
        return self._playback_engine.get_interpolated_frame(
            self._project.frames[index], self._project.frames[next_index], progress
        )

    # -- Status / title -------------------------------------------------------

    def _on_project_changed(self) -> None:
        """Timeline signal: fires on frame nav/new/delete/move and fps/exposure edits."""
        self._on_project_state_changed()

    def _on_project_state_changed(self) -> None:
        self._update_status_label()
        self._sync_canvas()
        self._timeline.refresh()
        self._update_rig_creation_availability()
        self._inspector.set_frame(self._current_frame())
        self._sync_inspector_selection()

    def _on_canvas_selection_changed(self) -> None:
        self._sync_inspector_selection()

    def _sync_inspector_selection(self) -> None:
        self._inspector.set_selected_layer(self._canvas.selected_layer_id())
        self._inspector.set_selected_bone(self._canvas.selected_bone_id(), self._canvas.selected_rig_id())

    def _on_inspector_changed(self) -> None:
        # Inspector edits mutate the current frame's layer/camera directly;
        # only the canvas needs a resync (frame identity/selection unchanged).
        # This fires on every keystroke/tick for live preview — the actual
        # undoable command only gets built once editing settles, via
        # layer_edit_committed/camera_edit_committed (see _execute_command).
        self._sync_canvas()

    # -- Undo/redo command execution -------------------------------------------

    def _can_mutate_model(self) -> bool:
        return not self._is_exporting and not self._playback.is_playing

    def _execute_command(self, command: commands.Command) -> bool:
        """Apply a new command through the shared history. Used by every
        undoable UI action (timeline new/delete/move/reorder, rig/layer
        creation, bone drag commit, inspector edit commit)."""
        if not self._can_mutate_model():
            return False
        try:
            self._history.execute(command, self._project)
        except timeline.CannotDeleteLastFrameError:
            return False
        self._after_history_change(command)
        return True

    def _undo(self) -> None:
        if not self._can_mutate_model():
            return
        command = self._history.undo(self._project)
        if command is not None:
            self._after_history_change(command)

    def _redo(self) -> None:
        if not self._can_mutate_model():
            return
        command = self._history.redo(self._project)
        if command is not None:
            self._after_history_change(command)

    def _after_history_change(self, command: commands.Command) -> None:
        """Common post-processing after execute/undo/redo: thumbnail cache
        invalidation, dirty flag, full UI resync, Undo/Redo action state."""
        self._bump_thumbnail_for_command(command)
        self._mark_dirty()
        self._on_project_state_changed()
        self._update_undo_redo_actions()

    def _bump_thumbnail_for_command(self, command: commands.Command) -> None:
        frame_id = getattr(command, "frame_id", None)
        if frame_id is not None:
            self._thumbnail_cache.bump(frame_id)
        elif isinstance(command, (commands.NewFrameCommand, commands.DeleteFrameCommand)):
            # A new/removed frame changes which ids exist at all; bump_all()
            # is cheap here since New/Delete Frame are low-frequency actions.
            self._thumbnail_cache.bump_all()
        # MoveFrameCommand touches only ordering, never a frame's rendered
        # content, so no thumbnail needs invalidating for it.

    def _mark_dirty(self) -> None:
        self._is_dirty = True
        self._update_window_title()

    def _update_undo_redo_actions(self) -> None:
        self._undo_action.setEnabled(self._history.can_undo)
        self._undo_action.setText(
            f"Undo {self._history.undo_description}" if self._history.can_undo else "Undo"
        )
        self._redo_action.setEnabled(self._history.can_redo)
        self._redo_action.setText(
            f"Redo {self._history.redo_description}" if self._history.can_redo else "Redo"
        )

    def _on_bone_transform_committed(self, mode: str, rig_id: str, bone_id: str, before: object, after: object) -> None:
        description = {"rotate": "Rotate Bone", "scale": "Scale Bone"}.get(mode, "Move Bone")
        command = commands.TransformBoneCommand(description, self._current_frame().id, rig_id, bone_id, before, after)
        self._execute_command(command)

    def _on_bone_edit_committed(
        self, frame_id: str, rig_id: str, bone_id: str, before: object, after: object
    ) -> None:
        command = commands.TransformBoneCommand("Edit Part", frame_id, rig_id, bone_id, before, after)
        self._execute_command(command)

    def _on_bone_reparent_requested(self, frame_id: str, rig_id: str, bone_id: str, new_parent_id: str | None) -> None:
        self._reparent_part(frame_id, rig_id, bone_id, new_parent_id)

    def _on_layer_edit_committed(self, frame_id: str, layer_id: str, before: object, after: object) -> None:
        command = commands.EditLayerCommand("Edit Layer", frame_id, layer_id, before, after)
        self._execute_command(command)

    def _on_camera_edit_committed(self, frame_id: str, before: object, after: object) -> None:
        command = commands.EditCameraCommand("Edit Camera", frame_id, before, after)
        self._execute_command(command)

    def _update_rig_creation_availability(self) -> None:
        # Milestone 6A lifts the one-rig-per-frame UI restriction: the
        # architecture (Frame.rigs: list[Rig], RigRenderer, export_renderer)
        # already supported several rigs per frame since Milestone 2 — only
        # this button ever artificially blocked it.
        self._asset_panel.set_rig_creation_enabled(True)

    def _update_status_label(self) -> None:
        index = self._project.current_frame_index
        frame = self._project.frames[index]
        total_poses = len(self._project.frames)
        label_part = f" — {frame.label}" if frame.label else ""

        try:
            video_frame_total = playback.output_frame_count(total_poses, self._project.exposure)
            duration = playback.output_duration_seconds(total_poses, self._project.exposure, self._project.fps)
            video_frame_start = index * self._project.exposure + 1
            video_part = f"    Video frame {video_frame_start}/{video_frame_total}  ~{duration:.2f}s"
        except playback.PlaybackValidationError:
            video_part = ""

        playing_part = "    ▶ Playing" if self._playback.is_playing else ""

        self._status_label.setText(
            f"Pose {index + 1}/{total_poses}{label_part}    "
            f"FPS {self._project.fps}  Exposure {self._project.exposure}{video_part}{playing_part}"
        )

    def _update_window_title(self) -> None:
        name = self._current_path.name if self._current_path else "Untitled"
        dirty_marker = " *" if self._is_dirty else ""
        self.setWindowTitle(f"PivotCut — {name}{dirty_marker}")

    # -- Playback / export mutation lock ---------------------------------------
    #
    # Playback and export share the exact same lock: neither may coexist with
    # a model mutation (dragging a bone, editing the inspector, reordering
    # frames, creating a rig/layer, Undo/Redo, New/Open/Save). Both call the
    # same _set_mutation_ui_enabled(); _can_mutate_model() additionally
    # guards the actual mutation entry points (_execute_command/_undo/_redo)
    # as defense-in-depth against re-entrancy from QProgressDialog.setValue()
    # pumping the Qt event loop mid-export.

    def _set_mutation_ui_enabled(self, enabled: bool) -> None:
        self._canvas.set_playback_active(not enabled)
        self._timeline.set_playback_active(not enabled)
        self._inspector.setEnabled(enabled)
        self._asset_panel.setEnabled(enabled)
        for action in (
            self._new_action,
            self._open_action,
            self._save_action,
            self._save_as_action,
            self._new_frame_action,
            self._delete_frame_action,
            self._export_png_action,
            self._export_mp4_action,
            self._undo_action,
            self._redo_action,
            self._new_character_rig_action,
            self._quick_create_rig_action,
            self._save_character_to_file_action,
            self._import_character_file_action,
            self._open_character_library_action,
            self._add_character_from_library_action,
            self._remove_character_from_library_action,
            self._reveal_character_assets_action,
            self._save_rig_as_template_action,
            self._export_rig_template_action,
            self._edit_selected_rig_action,
            self._delete_part_action,
            self._duplicate_part_action,
            self._bring_forward_action,
            self._send_backward_action,
            self._set_root_part_action,
            self._reparent_part_action,
        ):
            action.setEnabled(enabled)
        if enabled:
            # Undo/Redo must reflect actual stack contents, not a blanket True.
            self._update_undo_redo_actions()

    def _toggle_playback(self) -> None:
        if self._is_exporting:
            return
        self._playback.toggle()

    def _on_smooth_animation_toggled(self, enabled: bool) -> None:
        self._project.smooth_animation_enabled = enabled
        self._sync_canvas()

    def _on_playback_started(self) -> None:
        self._play_action.setText("Stop")
        self._set_mutation_ui_enabled(False)
        self._update_status_label()

    def _on_playback_stopped(self) -> None:
        self._play_action.setText("Play")
        self._set_mutation_ui_enabled(True)
        self._on_project_state_changed()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Application-wide filter making Space a global Play/Stop toggle.

        Text-input widgets (``QLineEdit``/``QAbstractSpinBox`` — covers both
        ``QSpinBox`` and ``QDoubleSpinBox`` — and ``QComboBox``) keep
        receiving Space normally so it can still be typed/used to open a
        dropdown. ``CanvasView`` also keeps receiving it unfiltered so its
        own keyPressEvent/keyReleaseEvent can tell a bare Space press/release
        (toggle playback) apart from Space+drag (pan) — see
        ``CanvasView.keyReleaseEvent``.
        """
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
                focused = QApplication.focusWidget()
                if isinstance(focused, _TEXT_INPUT_WIDGET_TYPES):
                    return False
                if focused is self._canvas or (focused is not None and self._canvas.isAncestorOf(focused)):
                    return False
                if event.type() == QEvent.Type.KeyPress:
                    self._toggle_playback()
                return True
            if (
                event.type() == QEvent.Type.KeyPress
                and not event.isAutoRepeat()
                and event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete)
            ):
                # Context-sensitive: a selected Part takes priority (delete
                # the Part), otherwise this falls back to the pre-existing
                # delete-current-frame behavior. A selected bone is only
                # ever non-None outside playback/export (selection is
                # cleared by set_playback_active), so this never needs its
                # own separate _can_mutate_model() check for that half.
                focused = QApplication.focusWidget()
                if isinstance(focused, _TEXT_INPUT_WIDGET_TYPES):
                    return False
                if self._canvas.selected_bone_id() is not None:
                    self._on_delete_selected_part()
                else:
                    self._timeline.delete_frame()
                return True
        return super().eventFilter(watched, event)

    # -- Rig / layer creation ------------------------------------------------

    def _on_create_rig_requested(self, asset_id: str) -> None:
        asset = next((a for a in self._project.assets if a.id == asset_id), None)
        if asset is None:
            return
        frame = self._current_frame()
        scene = self._project.scene_settings
        rig = create_rig_from_asset(asset, scene.width, scene.height)
        if self._execute_command(commands.CreateRigCommand("Quick Add Single PNG Part", frame.id, rig)):
            # Select it immediately: this is the fix for "Quick Add feels
            # like a static PNG" — without a selection, the new Part's
            # selection box/handles never appear, so there is nothing on
            # screen suggesting it can be moved/rotated/scaled/deleted.
            self._canvas.select_bone(rig.root_bone_id)

    def _on_create_layer_requested(self, asset_id: str, layer_type: str) -> None:
        asset = next((a for a in self._project.assets if a.id == asset_id), None)
        if asset is None:
            return
        frame = self._current_frame()
        scene = self._project.scene_settings
        layer = create_layer_from_asset(asset, layer_type, scene.width, scene.height)
        self._execute_command(commands.AddLayerCommand("Add Layer", frame.id, layer))

    # -- Character Rig Builder / Rig Library (Milestone 6A) ---------------------
    #
    # The dialog itself never touches history/dirty-flag/thumbnail cache: it
    # works on a local bone-list copy and hands back a finished result only
    # on Save (Cancel closes it with nothing to read back), and every path
    # here always goes through _execute_command — one atomic, descriptive
    # command per action, exactly like every other undoable mutation.

    def _selected_rig_id(self) -> str | None:
        """Which of the current frame's (possibly several) rigs the user means.

        Prefers whichever rig the currently-selected bone belongs to; falls
        back to the frame's only rig if there's exactly one; otherwise asks
        the user to click a part of the rig they mean first.
        """
        rig_id = self._canvas.selected_rig_id()
        if rig_id is not None:
            return rig_id
        rigs = self._current_frame().rigs
        if len(rigs) == 1:
            return rigs[0].id
        if not rigs:
            QMessageBox.information(self, "No Character Rig", "This frame has no character rig yet.")
        else:
            QMessageBox.information(
                self,
                "Select a Character Rig",
                "This frame has more than one character rig — click a part of "
                "the one you mean first.",
            )
        return None

    def _pick_rig_template(self) -> RigTemplate | None:
        if not self._project.rig_templates:
            QMessageBox.information(
                self, "Character Library", "The Character Library is empty. Save a character to it first."
            )
            return None
        names = [template.name for template in self._project.rig_templates]
        name, ok = QInputDialog.getItem(self, "Choose a Character", "Template:", names, 0, False)
        if not ok:
            return None
        return next((t for t in self._project.rig_templates if t.name == name), None)

    def _on_new_character_rig(self) -> None:
        dialog = RigBuilderDialog(self._project, self._project_dir, title="New Character Rig", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        root_bone_id = dialog.result_root_bone_id()
        bones = dialog.result_bones()
        if root_bone_id is None or not bones:
            return
        root_bone = find_bone_in_list(bones, root_bone_id)
        rig = Rig(
            id=str(uuid.uuid4()),
            name=root_bone.name if root_bone is not None else "Character",
            root_bone_id=root_bone_id,
            bones=bones,
        )
        if self._execute_command(commands.CreateRigCommand("Build Character from PNGs", self._current_frame().id, rig)):
            self._canvas.select_bone(rig.root_bone_id)

    def _on_quick_create_rig(self) -> None:
        asset_id = self._asset_panel.selected_asset_id()
        if asset_id is None:
            QMessageBox.information(
                self, "Quick Create Single-Part Rig", "Select a PNG asset in the Assets panel first."
            )
            return
        self._on_create_rig_requested(asset_id)

    def _add_character_template_to_scene(self, template: RigTemplate) -> None:
        """Instantiate ``template`` into the current frame as a brand-new Rig
        Instance — fresh rig/bone ids, parent mapping/pivot/attach/z-index/
        properties preserved exactly, root centered in the scene, selected
        immediately. One undoable command ("Add Character to Scene")."""
        scene = self._project.scene_settings
        rig = instantiate_rig_template(template, scene.width / 2.0, scene.height / 2.0)
        if self._execute_command(
            commands.CreateRigCommand(f"Add Character to Scene: {template.name}", self._current_frame().id, rig)
        ):
            self._canvas.select_bone(rig.root_bone_id)

    def _on_add_character_from_library(self) -> None:
        template = self._pick_rig_template()
        if template is not None:
            self._add_character_template_to_scene(template)

    def _remove_character_template(self, template: RigTemplate) -> None:
        reply = QMessageBox.question(
            self,
            "Remove Character From Library",
            f'Remove "{template.name}" from the Character Library?\n\n'
            "This only removes the reusable template — PNG assets and any "
            "existing Rig Instances/poses already in the timeline are untouched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._execute_command(commands.DeleteRigTemplateCommand(f"Remove Character: {template.name}", template.id))

    def _on_remove_character_from_library(self) -> None:
        template = self._pick_rig_template()
        if template is not None:
            self._remove_character_template(template)

    def _on_reveal_character_assets(self) -> None:
        template = self._pick_rig_template()
        if template is None:
            return
        assets_by_id = {a.id: a for a in self._project.assets}
        resolved_paths: list[Path] = []
        for bone in template.bones:
            asset = assets_by_id.get(bone.asset_id)
            if asset is None:
                continue
            path = asset_manager.resolve_asset_path(asset, self._project_dir())
            if path is not None and path not in resolved_paths:
                resolved_paths.append(path)
        if not resolved_paths:
            QMessageBox.information(
                self,
                "Reveal Character Assets",
                f'None of "{template.name}"\'s PNG assets could be found on disk in this project.',
            )
            return
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", *[str(p) for p in resolved_paths]], check=False)
        else:
            QMessageBox.information(
                self,
                "Reveal Character Assets",
                "Assets found at:\n" + "\n".join(str(p) for p in resolved_paths),
            )

    def _on_open_character_library(self) -> None:
        dialog = CharacterLibraryDialog(
            self._project, self._project_dir, self._template_preview_cache, self._template_sources, parent=self
        )
        dialog.add_to_scene_requested.connect(lambda template_id: self._on_library_add_to_scene(dialog, template_id))
        dialog.remove_requested.connect(lambda template_id: self._on_library_remove(dialog, template_id))
        dialog.import_requested.connect(lambda: self._on_library_import(dialog))
        dialog.exec()

    def _on_library_add_to_scene(self, dialog: CharacterLibraryDialog, template_id: str) -> None:
        template = next((t for t in self._project.rig_templates if t.id == template_id), None)
        if template is not None:
            self._add_character_template_to_scene(template)
        dialog.refresh()

    def _on_library_remove(self, dialog: CharacterLibraryDialog, template_id: str) -> None:
        template = next((t for t in self._project.rig_templates if t.id == template_id), None)
        if template is not None:
            self._remove_character_template(template)
        dialog.refresh()

    def _on_library_import(self, dialog: CharacterLibraryDialog) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Import Character File", str(Path.home()), "PivotCut Character File (*.pivotcut-rig.json)"
        )
        if path_str:
            self._import_character_file(Path(path_str))
        dialog.refresh()

    def _on_save_rig_as_template(self) -> None:
        rig_id = self._selected_rig_id()
        if rig_id is None:
            return
        rig = next((r for r in self._current_frame().rigs if r.id == rig_id), None)
        if rig is None:
            return
        name, ok = QInputDialog.getText(self, "Save Selected Rig as Template", "Template name:", text=rig.name)
        name = name.strip()
        if not ok or not name:
            return
        template = rig_template_from_rig(rig, name=name)
        self._execute_command(commands.SaveRigTemplateCommand(f"Save Template: {name}", template))

    def _on_edit_selected_rig(self) -> None:
        rig_id = self._selected_rig_id()
        if rig_id is None:
            return
        rig = next((r for r in self._current_frame().rigs if r.id == rig_id), None)
        if rig is None:
            return
        dialog = RigBuilderDialog(
            self._project,
            self._project_dir,
            initial_bones=rig.bones,
            initial_root_bone_id=rig.root_bone_id,
            title=f"Edit Character Rig — {rig.name}",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        # Structural edits here only ever touch this frame's rig *instance* —
        # never propagated to other frames or to any RigTemplate. Use "Save
        # Selected Rig as Template…" separately to update the library.
        command = commands.EditRigStructureCommand(
            "Edit Character Rig",
            self._current_frame().id,
            rig.id,
            commands.snapshot_bones(rig.bones),
            rig.root_bone_id,
            dialog.result_bones(),
            dialog.result_root_bone_id(),
        )
        self._execute_command(command)

    # -- Quick part manipulation: Delete/Duplicate/Reorder/Reparent/Set Root ----
    #
    # These act on whichever Part is selected on the main canvas (Hotfix
    # UX-1). Every path here is a thin wrapper around the same pure
    # domain.rig functions already used by RigBuilderDialog, executed as one
    # EditRigStructureCommand/TransformBoneCommand — never a bespoke command.

    def _selected_part(self) -> tuple[Bone | None, Rig | None]:
        bone_id = self._canvas.selected_bone_id()
        rig_id = self._canvas.selected_rig_id()
        if bone_id is None or rig_id is None:
            return None, None
        rig = next((r for r in self._current_frame().rigs if r.id == rig_id), None)
        if rig is None:
            return None, None
        return find_bone_in_list(rig.bones, bone_id), rig

    def _reparent_part(self, frame_id: str, rig_id: str, bone_id: str, new_parent_id: str) -> None:
        frame = next((f for f in self._project.frames if f.id == frame_id), None)
        rig = next((r for r in frame.rigs if r.id == rig_id), None) if frame is not None else None
        if rig is None:
            return
        before_bones = commands.snapshot_bones(rig.bones)
        working_bones = commands.snapshot_bones(rig.bones)
        try:
            reparent_bone(working_bones, bone_id, new_parent_id)
        except RigValidationError as exc:
            QMessageBox.warning(self, "Cannot Reparent Part", str(exc))
            return
        command = commands.EditRigStructureCommand(
            "Reparent Part", frame_id, rig_id, before_bones, rig.root_bone_id, working_bones, rig.root_bone_id
        )
        self._execute_command(command)

    def _on_delete_selected_part(self) -> None:
        bone, rig = self._selected_part()
        if bone is None or rig is None:
            QMessageBox.information(self, "Delete Selected Part", "Select a Part on the canvas first.")
            return
        frame = self._current_frame()

        if bone.id == rig.root_bone_id:
            reply = QMessageBox.question(
                self,
                "Delete Character",
                f'"{bone.name}" is the root Part of this character — deleting it removes the whole '
                "character instance from this frame. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            if self._execute_command(commands.DeleteRigCommand("Delete Character", frame.id, copy.deepcopy(rig))):
                self._canvas.clear_selection()
            return

        has_children = any(b.parent_id == bone.id for b in rig.bones)
        before_bones = commands.snapshot_bones(rig.bones)
        if has_children:
            choice = QMessageBox.question(
                self,
                "Delete Part",
                f'"{bone.name}" has child Parts attached to it.\n\n'
                "Yes = Delete subtree (removes it and all its children)\n"
                "No = Reparent children (keeps the children, reattached to "
                f'"{bone.name}"\'s own parent)\n',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return
            after_bones = (
                remove_bone_subtree(rig.bones, bone.id, rig.root_bone_id)
                if choice == QMessageBox.StandardButton.Yes
                else remove_bone_reparent_children(rig.bones, bone.id, rig.root_bone_id)
            )
        else:
            after_bones = remove_bone_subtree(rig.bones, bone.id, rig.root_bone_id)

        parent_id = bone.parent_id
        command = commands.EditRigStructureCommand(
            "Delete Part", frame.id, rig.id, before_bones, rig.root_bone_id, after_bones, rig.root_bone_id
        )
        if self._execute_command(command):
            self._canvas.select_bone(parent_id)

    def _on_duplicate_selected_part(self) -> None:
        bone, rig = self._selected_part()
        if bone is None or rig is None:
            QMessageBox.information(self, "Duplicate Selected Part", "Select a Part on the canvas first.")
            return
        if bone.id == rig.root_bone_id:
            QMessageBox.information(
                self,
                "Duplicate Selected Part",
                "The root Part can't be duplicated this way (a rig needs exactly one root) — "
                "duplicate a child Part instead, or use Build Character from PNGs… for another character.",
            )
            return
        before_bones = commands.snapshot_bones(rig.bones)
        working_bones = commands.snapshot_bones(rig.bones)
        new_bone = duplicate_bone(working_bones, bone.id)
        working_bones.append(new_bone)
        command = commands.EditRigStructureCommand(
            "Duplicate Part",
            self._current_frame().id,
            rig.id,
            before_bones,
            rig.root_bone_id,
            working_bones,
            rig.root_bone_id,
        )
        if self._execute_command(command):
            self._canvas.select_bone(new_bone.id)

    def _nudge_selected_part_z_index(self, delta: int, description: str) -> None:
        bone, rig = self._selected_part()
        if bone is None or rig is None:
            QMessageBox.information(self, description, "Select a Part on the canvas first.")
            return
        before = commands.snapshot_bone(bone)
        after = copy.deepcopy(bone)
        after.z_index += delta
        command = commands.TransformBoneCommand(description, self._current_frame().id, rig.id, bone.id, before, after)
        self._execute_command(command)

    def _on_bring_forward(self) -> None:
        self._nudge_selected_part_z_index(1, "Bring Forward")

    def _on_send_backward(self) -> None:
        self._nudge_selected_part_z_index(-1, "Send Backward")

    def _on_set_selected_part_as_root(self) -> None:
        bone, rig = self._selected_part()
        if bone is None or rig is None:
            QMessageBox.information(self, "Set Selected Part as Root", "Select a Part on the canvas first.")
            return
        if bone.id == rig.root_bone_id:
            return
        before_bones = commands.snapshot_bones(rig.bones)
        working_bones = commands.snapshot_bones(rig.bones)
        try:
            set_root_bone(working_bones, bone.id)
        except RigValidationError as exc:
            QMessageBox.warning(self, "Cannot Set Root", str(exc))
            return
        command = commands.EditRigStructureCommand(
            "Set Root Part", self._current_frame().id, rig.id, before_bones, rig.root_bone_id, working_bones, bone.id
        )
        self._execute_command(command)

    def _on_reparent_selected_part(self) -> None:
        bone, rig = self._selected_part()
        if bone is None or rig is None:
            QMessageBox.information(self, "Reparent Selected Part…", "Select a Part on the canvas first.")
            return
        if bone.id == rig.root_bone_id:
            QMessageBox.information(
                self,
                "Reparent Selected Part…",
                "The root Part has no parent to change — use Set Selected Part as Root "
                "on a different Part instead.",
            )
            return
        candidates = [b for b in rig.bones if b.id != bone.id]
        names = [b.name for b in candidates]
        current_index = next((i for i, b in enumerate(candidates) if b.id == bone.parent_id), 0)
        name, ok = QInputDialog.getItem(
            self, "Reparent Selected Part", f'New parent for "{bone.name}":', names, current_index, False
        )
        if not ok:
            return
        new_parent = candidates[names.index(name)]
        self._reparent_part(self._current_frame().id, rig.id, bone.id, new_parent.id)

    def _on_save_character_to_file(self) -> None:
        """"Save Selected Character to File…": one step from a posed rig
        instance straight to a portable Character File — no Rig Builder,
        no Character Library mutation, no dirty flag (pure file export)."""
        rig_id = self._selected_rig_id()
        if rig_id is None:
            return
        rig = next((r for r in self._current_frame().rigs if r.id == rig_id), None)
        if rig is None:
            return
        name, ok = QInputDialog.getText(self, "Save Selected Character to File", "Character name:", text=rig.name)
        name = name.strip()
        if not ok or not name:
            return
        template = rig_template_from_rig(rig, name=name)

        default_dir = str(self._current_path.parent) if self._current_path else str(Path.home())
        default_name = f"{name}{rig_template_io.TEMPLATE_FILE_SUFFIX}"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save Selected Character to File",
            str(Path(default_dir) / default_name),
            "PivotCut Character File (*.pivotcut-rig.json)",
        )
        if not path_str:
            return
        path = Path(path_str)
        if not path.name.endswith(rig_template_io.TEMPLATE_FILE_SUFFIX):
            path = path.with_name(path.name + rig_template_io.TEMPLATE_FILE_SUFFIX)
        try:
            rig_template_io.export_rig_template(template, self._project.assets, path)
        except rig_template_io.RigTemplateIOError as exc:
            QMessageBox.critical(self, "Cannot Save Character", str(exc))
            return
        QMessageBox.information(self, "Character Saved", f"Saved to:\n{path}")
        # Pure file export — the rig instance/pose already in the timeline
        # and Project.rig_templates are both left untouched, so this must
        # never mark the project dirty (see Milestone 6C spec §6).

    def _on_import_character_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Import Character File", str(Path.home()), "PivotCut Character File (*.pivotcut-rig.json)"
        )
        if path_str:
            self._import_character_file(Path(path_str))

    def _import_character_file(self, path: Path) -> None:
        """Shared by the "Import Character File…" action and the Character
        Library dialog's own Import button.

        Resolution order: :func:`rig_template_io.auto_resolve_manifest`
        (no UI) first, then ``AssetMappingDialog`` for whatever remains
        missing. The template may still end up with unresolved assets after
        that (the user can "Skip for now") — it is inserted into the
        library anyway with a "Missing assets" status; nothing here ever
        requires every PNG to be present to finish the import.
        """
        try:
            result = rig_template_io.import_rig_template(path)
        except rig_template_io.RigTemplateIOError as exc:
            QMessageBox.critical(self, "Cannot Import Character File", str(exc))
            return

        template = result.template
        auto_mapping = rig_template_io.auto_resolve_manifest(
            result.asset_references, path, self._project, self._project_dir()
        )
        if auto_mapping:
            template = rig_template_io.remap_template_asset_ids(template, auto_mapping)

        missing_ids = rig_template_io.missing_asset_ids(template, self._project.assets)
        if missing_ids:
            refs_by_id = {ref.id: ref for ref in result.asset_references}
            missing_refs = [refs_by_id[missing_id] for missing_id in missing_ids if missing_id in refs_by_id]
            dialog = AssetMappingDialog(self._project, self._project_dir, missing_refs, parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return  # Cancel: Project is untouched (see module docstring).
            mapping = dialog.result_mapping()
            if mapping:
                template = rig_template_io.remap_template_asset_ids(template, mapping)

        # Structural validity (single root, no cycles, valid parent refs,
        # opacity/scale bounds) is still required — but asset *availability*
        # is deliberately not: a template with unresolved assets is valid
        # and inserted with "Missing assets" status (see class docstring),
        # so validation runs against a placeholder asset for every id still
        # referenced, never against self._project.assets directly here.
        referenced_ids = {bone.asset_id for bone in template.bones}
        placeholder_assets = [Asset(id=rid, name="", source_path="", relative_path=None, width=1, height=1) for rid in referenced_ids]
        try:
            validate_rig_template(template, placeholder_assets)
        except RigValidationError as exc:
            QMessageBox.critical(self, "Cannot Import Character File", f"This Character File is not valid: {exc}")
            return

        template = copy.deepcopy(template)
        template.id = str(uuid.uuid4())  # avoid clashing with an existing library entry
        self._template_sources[template.id] = str(path)
        self._execute_command(commands.SaveRigTemplateCommand(f"Import Character: {template.name}", template))

    def _on_export_rig_template(self) -> None:
        template = self._pick_rig_template()
        if template is None:
            return
        default_dir = str(self._current_path.parent) if self._current_path else str(Path.home())
        default_name = f"{template.name}{rig_template_io.TEMPLATE_FILE_SUFFIX}"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Export Rig Template",
            str(Path(default_dir) / default_name),
            "PivotCut Rig Template (*.pivotcut-rig.json)",
        )
        if not path_str:
            return
        path = Path(path_str)
        if not path.name.endswith(rig_template_io.TEMPLATE_FILE_SUFFIX):
            path = path.with_name(path.name + rig_template_io.TEMPLATE_FILE_SUFFIX)
        try:
            rig_template_io.export_rig_template(template, self._project.assets, path)
        except rig_template_io.RigTemplateIOError as exc:
            QMessageBox.critical(self, "Cannot Export Rig Template", str(exc))
            return
        QMessageBox.information(self, "Rig Template Exported", f"Saved to:\n{path}")
        # Exporting to an external file doesn't change Project content, so
        # this deliberately does not mark the project dirty.

    # -- Project actions --------------------------------------------------

    def _rebind_project(self) -> None:
        self._history.clear()
        self._thumbnail_cache.bump_all()
        self._template_preview_cache.clear()
        self._template_sources = {}
        self._is_dirty = False
        self._smooth_animation_action.blockSignals(True)
        self._smooth_animation_action.setChecked(self._project.smooth_animation_enabled)
        self._smooth_animation_action.blockSignals(False)
        self._playback.set_project(self._project)
        self._timeline.set_project(self._project, self._project_dir)
        self._canvas.apply_scene_settings(self._project.scene_settings)
        self._asset_panel.set_project(self._project, self._project_dir)
        self._on_project_state_changed()
        self._update_undo_redo_actions()
        self._update_window_title()

    def _confirm_discard_unsaved_changes(self) -> bool:
        """True if it's OK to proceed (nothing unsaved, or the user chose to
        save/discard); False if the user cancelled the pending action."""
        if not self._is_dirty:
            return True
        reply = QMessageBox.warning(
            self,
            "Unsaved Changes",
            "This project has unsaved changes. Save before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Save:
            self._on_save_project()
            return not self._is_dirty  # stays blocked if the user cancelled the Save As dialog
        return True  # Discard

    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt event type
        if not self._confirm_discard_unsaved_changes():
            event.ignore()
            return
        super().closeEvent(event)

    def _on_new_project(self) -> None:
        if not self._confirm_discard_unsaved_changes():
            return
        self._project = new_project()
        self._current_path = None
        self._rebind_project()

    def _on_open_project(self) -> None:
        if not self._confirm_discard_unsaved_changes():
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Open PivotCut Project", str(Path.home()), PROJECT_FILE_FILTER
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            project = project_io.load_project(path)
        except project_io.ProjectIOError as exc:
            QMessageBox.critical(self, "Cannot Open Project", str(exc))
            return
        self._project = project
        self._current_path = path
        self._rebind_project()

    def _on_save_project(self) -> None:
        if self._current_path is None:
            self._on_save_project_as()
            return
        self._save_to(self._current_path)

    def _on_save_project_as(self) -> None:
        default_dir = str(self._current_path.parent) if self._current_path else str(Path.home())
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save PivotCut Project As", default_dir, PROJECT_FILE_FILTER
        )
        if not path_str:
            return
        path = Path(path_str)
        if not path.name.endswith(".pivotcut.json"):
            path = path.with_name(path.name + ".pivotcut.json")
        self._save_to(path)

    def _save_to(self, path: Path) -> None:
        asset_manager.refresh_relative_paths(self._project, path.parent)
        try:
            project_io.save_project(self._project, path)
        except project_io.ProjectIOError as exc:
            QMessageBox.critical(self, "Cannot Save Project", str(exc))
            return
        self._current_path = path
        self._is_dirty = False
        self._update_window_title()
        self._asset_panel.refresh_assets()
        self._sync_canvas()

    # -- Export ---------------------------------------------------------------
    #
    # Rendering is entirely delegated to services.export_renderer /
    # services.ffmpeg_export — both are headless and take only Project data,
    # never this window, the canvas or any selection state. Everything here
    # is file-dialog/progress-UI/error-reporting glue.
    #
    # Export runs on the main thread: QPixmap/QGraphicsScene/QPainter (used
    # while rendering each frame) are documented by Qt as GUI-thread-only,
    # so a background QThread would risk crashing rather than helping. A
    # QProgressDialog is used instead — its setValue() pumps the Qt event
    # loop internally, which is what keeps the window responsive and the
    # Cancel button live while frames render in short, event-processed
    # chunks. This matches the milestone's own "QThread OR a carefully
    # chunked approach with event processing" allowance.

    def _existing_frame_pngs(self, directory: Path) -> list[Path]:
        return sorted(directory.glob("frame_*.png"))

    def _on_export_png_sequence(self) -> None:
        if not self._project.frames:
            return

        directory_str = QFileDialog.getExistingDirectory(self, "Export PNG Sequence", str(Path.home()))
        if not directory_str:
            return
        output_dir = Path(directory_str)

        existing = self._existing_frame_pngs(output_dir)
        if existing:
            reply = QMessageBox.question(
                self,
                "Folder Not Empty",
                f"{output_dir} already contains {len(existing)} file(s) matching 'frame_*.png'.\n"
                "Exporting here will overwrite them. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        try:
            total = playback.output_frame_count(len(self._project.frames), self._project.exposure)
        except playback.PlaybackValidationError as exc:
            QMessageBox.critical(self, "Cannot Export", str(exc))
            return

        self._is_exporting = True
        self._set_mutation_ui_enabled(False)
        try:
            progress = QProgressDialog("Rendering…", "Cancel", 0, total, self)
            progress.setWindowTitle("Export PNG Sequence")
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)

            def on_progress(done: int, done_total: int) -> None:
                progress.setLabelText(f"Rendering frame {done} / {done_total}")
                progress.setValue(done)

            try:
                written = export_renderer.save_png_sequence(
                    self._project,
                    output_dir,
                    progress_callback=on_progress,
                    cancel_requested=progress.wasCanceled,
                    project_dir=self._project_dir(),
                )
            except OSError as exc:
                progress.close()
                QMessageBox.critical(self, "Export Failed", str(exc))
                return

            was_cancelled = progress.wasCanceled()
            progress.close()

            if was_cancelled:
                QMessageBox.information(
                    self,
                    "Export Cancelled",
                    f"Export cancelled: {len(written)} of {total} frame(s) were written to:\n{output_dir}",
                )
                return

            duration = playback.output_duration_seconds(
                len(self._project.frames), self._project.exposure, self._project.fps
            )
            QMessageBox.information(
                self,
                "Export Complete",
                f"Wrote {len(written)} PNG frame(s) to:\n{output_dir}\nEstimated duration: {duration:.2f}s",
            )
        finally:
            self._is_exporting = False
            self._set_mutation_ui_enabled(True)

    def _on_export_mp4(self) -> None:
        if not self._project.frames:
            return

        ffmpeg_path = ffmpeg_export.find_ffmpeg()
        if ffmpeg_path is None:
            QMessageBox.warning(
                self,
                "FFmpeg Not Found",
                "ffmpeg was not found on PATH or at /opt/homebrew/bin/ffmpeg.\n\n"
                "PNG sequence export remains available.\n"
                "Install ffmpeg with: brew install ffmpeg",
            )
            return

        default_dir = str(self._current_path.parent) if self._current_path else str(Path.home())
        path_str, _ = QFileDialog.getSaveFileName(self, "Export MP4", default_dir, "MP4 Video (*.mp4)")
        if not path_str:
            return
        output_path = Path(path_str)
        if not output_path.name.endswith(".mp4"):
            output_path = output_path.with_name(output_path.name + ".mp4")

        if output_path.exists():
            reply = QMessageBox.question(
                self,
                "Overwrite File?",
                f"{output_path} already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        try:
            total = playback.output_frame_count(len(self._project.frames), self._project.exposure)
        except playback.PlaybackValidationError as exc:
            QMessageBox.critical(self, "Cannot Export", str(exc))
            return

        self._is_exporting = True
        self._set_mutation_ui_enabled(False)
        try:
            progress = QProgressDialog("Rendering…", "Cancel", 0, total, self)
            progress.setWindowTitle("Export MP4")
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)

            def on_progress(phase: str, done: int, done_total: int) -> None:
                if phase == "render":
                    progress.setMaximum(done_total)
                    progress.setLabelText(f"Rendering frame {done} / {done_total}")
                    progress.setValue(done)
                elif done == 0:
                    progress.setLabelText("Encoding MP4 with ffmpeg…")

            try:
                ffmpeg_export.export_mp4(
                    self._project,
                    output_path,
                    progress_callback=on_progress,
                    cancel_requested=progress.wasCanceled,
                    project_dir=self._project_dir(),
                    ffmpeg_path=ffmpeg_path,
                )
            except ffmpeg_export.ExportCancelledError:
                progress.close()
                QMessageBox.information(
                    self, "Export Cancelled", "MP4 export was cancelled before encoding started."
                )
                return
            except ffmpeg_export.FfmpegNotFoundError as exc:
                progress.close()
                QMessageBox.warning(self, "FFmpeg Not Found", str(exc))
                return
            except ffmpeg_export.FfmpegExecutionError as exc:
                progress.close()
                QMessageBox.critical(self, "FFmpeg Error", str(exc))
                return
            except OSError as exc:
                progress.close()
                QMessageBox.critical(self, "Export Failed", str(exc))
                return

            progress.close()
            duration = playback.output_duration_seconds(
                len(self._project.frames), self._project.exposure, self._project.fps
            )
            QMessageBox.information(
                self, "Export Complete", f"Saved MP4 to:\n{output_path}\nEstimated duration: {duration:.2f}s"
            )
        finally:
            self._is_exporting = False
            self._set_mutation_ui_enabled(True)
