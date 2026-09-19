"""Horizontal Pivot-style timeline: rendered thumbnail strip + transport controls.

All frame insertion/deletion/reorder/navigation logic is delegated to
``pivotcut.domain.timeline``/``pivotcut.domain.commands`` (pure functions/
command objects). This widget only *reflects* project state and constructs
commands for a caller-supplied executor to run — it holds no authoritative
data of its own (frame content, undo history, ...), only transient Qt
widget state (which thumbnail is being dragged, the current drop target).

Every editing action that should be undoable (New/Delete/Move/reorder) is
routed through ``set_command_executor()`` (wired by ``MainWindow`` to its
``_execute_command``, which applies the command via the shared
``UndoRedoStack`` and re-syncs the rest of the UI) rather than mutating
``Project``/``Frame`` directly. Pure navigation (selecting a frame) is not
a command — it stays a direct call into ``domain.timeline.select_frame``,
same as Milestone 1-4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pivotcut.domain import commands, timeline
from pivotcut.domain.models import Frame, Project
from pivotcut.services.thumbnail_cache import ThumbnailCache

_THUMB_IMAGE_WIDTH = 160
_THUMB_IMAGE_HEIGHT = 90
_THUMB_WIDTH = _THUMB_IMAGE_WIDTH + 8
_THUMB_HEIGHT = _THUMB_IMAGE_HEIGHT + 34
_FRAME_ID_MIME_TYPE = "application/x-pivotcut-frame-id"


class FrameThumbnail(QFrame):
    """A rendered-image thumbnail for one Frame; also the drag-and-drop reorder handle."""

    clicked = Signal(int)
    drag_started = Signal(str)  # frame_id
    drop_target_entered = Signal(object, bool)  # (FrameThumbnail | None hovered self, insert_before)
    drop_committed = Signal(str, int, bool)  # (dragged_frame_id, target_index, insert_before)

    def __init__(
        self, index: int, frame: Frame, image: QPixmap, exposure: int, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.index = index
        self.frame_id = frame.id
        self._selected = False
        self._drop_side: str | None = None
        self.setFixedSize(_THUMB_WIDTH, _THUMB_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(True)
        self.setWhatsThis(
            "Click to select this Pose. Drag it left or right onto another Pose to reorder the "
            "timeline."
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self._image_label = QLabel()
        self._image_label.setFixedSize(_THUMB_IMAGE_WIDTH, _THUMB_IMAGE_HEIGHT)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setPixmap(image)
        layout.addWidget(self._image_label)

        self._caption_label = QLabel()
        self._caption_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._caption_label.setStyleSheet("color: #cccccc; font-size: 10px;")
        layout.addWidget(self._caption_label)

        label_part = f" — {frame.label}" if frame.label else ""
        self._caption_label.setText(f"Pose {index + 1}{label_part}  ×{exposure}")

        self._drag_start_pos: QPoint | None = None
        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style()

    def _apply_style(self) -> None:
        if self._drop_side is not None:
            side_property = "border-left" if self._drop_side == "left" else "border-right"
            self.setStyleSheet(
                "FrameThumbnail { background-color: %s; border: 2px solid %s;"
                " %s: 5px solid #4ade80; border-radius: 4px; }"
                % (
                    "#3d6fb4" if self._selected else "#454545",
                    "#7db2ff" if self._selected else "#2a2a2a",
                    side_property,
                )
            )
        elif self._selected:
            self.setStyleSheet(
                "FrameThumbnail { background-color: #3d6fb4;"
                " border: 2px solid #7db2ff; border-radius: 4px; }"
            )
        else:
            self.setStyleSheet(
                "FrameThumbnail { background-color: #454545;"
                " border: 2px solid #2a2a2a; border-radius: 4px; }"
            )

    def set_drop_indicator(self, side: str | None) -> None:
        self._drop_side = side
        self._apply_style()

    # -- Mouse: click-to-select + drag-to-reorder ----------------------------

    def mousePressEvent(self, event) -> None:  # noqa: ANN001 - Qt event type
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
            self.clicked.emit(self.index)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001 - Qt event type
        if self._drag_start_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        from PySide6.QtWidgets import QApplication

        delta = event.position().toPoint() - self._drag_start_pos
        if delta.manhattanLength() < QApplication.startDragDistance():
            return
        self._drag_start_pos = None
        self.drag_started.emit(self.frame_id)

        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_FRAME_ID_MIME_TYPE, self.frame_id.encode("utf-8"))
        drag.setMimeData(mime)
        drag.setPixmap(self._image_label.pixmap())
        drag.exec(Qt.DropAction.MoveAction)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001 - Qt event type
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    # -- Drop target: hovering another thumbnail while dragging one ---------

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001 - Qt event type
        if event.mimeData().hasFormat(_FRAME_ID_MIME_TYPE):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: ANN001 - Qt event type
        if not event.mimeData().hasFormat(_FRAME_ID_MIME_TYPE):
            return
        insert_before = event.position().toPoint().x() < self.width() / 2
        self.drop_target_entered.emit(self, insert_before)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001 - Qt event type
        self.drop_target_entered.emit(None, False)

    def dropEvent(self, event) -> None:  # noqa: ANN001 - Qt event type
        if not event.mimeData().hasFormat(_FRAME_ID_MIME_TYPE):
            return
        dragged_frame_id = bytes(event.mimeData().data(_FRAME_ID_MIME_TYPE)).decode("utf-8")
        insert_before = event.position().toPoint().x() < self.width() / 2
        self.drop_committed.emit(dragged_frame_id, self.index, insert_before)
        event.acceptProposedAction()


class TimelineWidget(QWidget):
    """Timeline strip + transport/reorder controls + fps/exposure."""

    project_changed = Signal()

    def __init__(self, thumbnail_cache: ThumbnailCache, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project: Project | None = None
        self._get_project_dir: Callable[[], Path | None] = lambda: None
        self._thumbnail_cache = thumbnail_cache
        self._thumbnails: list[FrameThumbnail] = []
        self._playback_active = False
        self._command_executor: Callable[[commands.Command], bool] | None = None
        self._build_ui()
        self.setWhatsThis(
            "The timeline: one thumbnail per Pose, played back in order. Click a thumbnail to "
            "jump to it, drag one onto another to reorder, and use the controls below to "
            "navigate, add/remove Poses, and set FPS/Exposure."
        )

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        self._strip_container = QWidget()
        self._strip_layout = QHBoxLayout(self._strip_container)
        self._strip_layout.setContentsMargins(4, 4, 4, 4)
        self._strip_layout.setSpacing(6)
        self._strip_layout.addStretch(1)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setWidget(self._strip_container)
        self._scroll_area.setFixedHeight(_THUMB_HEIGHT + 24)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll_area)

        controls = QHBoxLayout()
        controls.setSpacing(6)

        self._prev_btn = QPushButton("◀ Previous")
        self._move_left_btn = QPushButton("Move Left")
        self._new_btn = QPushButton("New Frame")
        self._delete_btn = QPushButton("Delete Frame")
        self._move_right_btn = QPushButton("Move Right")
        self._next_btn = QPushButton("Next ▶")

        self._prev_btn.setWhatsThis("Selects the previous Pose. Shortcut: Left arrow.")
        self._next_btn.setWhatsThis("Selects the next Pose. Shortcut: Right arrow.")
        self._move_left_btn.setWhatsThis(
            "Moves the current Pose one position earlier in the timeline (reorders it; doesn't "
            "change which Pose is selected)."
        )
        self._move_right_btn.setWhatsThis(
            "Moves the current Pose one position later in the timeline (reorders it; doesn't "
            "change which Pose is selected)."
        )
        self._new_btn.setWhatsThis("Inserts a new Pose right after the current one and selects it. Shortcut: N.")
        self._delete_btn.setWhatsThis(
            "Deletes the current Pose. A project must always keep at least one Pose, so this is "
            "disabled when only one remains."
        )

        self._prev_btn.clicked.connect(self.previous_frame)
        self._move_left_btn.clicked.connect(self.move_left)
        self._new_btn.clicked.connect(self.new_frame)
        self._delete_btn.clicked.connect(self.delete_frame)
        self._move_right_btn.clicked.connect(self.move_right)
        self._next_btn.clicked.connect(self.next_frame)

        for button in (
            self._prev_btn,
            self._move_left_btn,
            self._new_btn,
            self._delete_btn,
            self._move_right_btn,
            self._next_btn,
        ):
            controls.addWidget(button)

        controls.addStretch(1)

        controls.addWidget(QLabel("FPS:"))
        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(1, 240)
        self._fps_spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._fps_spin.setWhatsThis(
            "Frames per second for playback and export — how many video frames make up one "
            "second. Applies to the whole project, not per Pose."
        )
        self._fps_spin.valueChanged.connect(self._on_fps_changed)
        controls.addWidget(self._fps_spin)

        controls.addWidget(QLabel("Exposure:"))
        self._exposure_spin = QSpinBox()
        self._exposure_spin.setRange(1, 24)
        self._exposure_spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._exposure_spin.setWhatsThis(
            "How many video frames each Pose is held for during playback/export (Pivot "
            "Animator calls this concept \"Exposure\"). E.g. at 24 FPS, an Exposure of 2 holds "
            "each Pose for 1/12 of a second. Applies to the whole project."
        )
        self._exposure_spin.valueChanged.connect(self._on_exposure_changed)
        controls.addWidget(self._exposure_spin)

        outer.addLayout(controls)

    # -- Wiring from MainWindow ------------------------------------------------

    def set_command_executor(self, executor: Callable[[commands.Command], bool]) -> None:
        self._command_executor = executor

    # -- Project binding --------------------------------------------------

    def set_project(self, project: Project, get_project_dir: Callable[[], Path | None] = lambda: None) -> None:
        self._project = project
        self._get_project_dir = get_project_dir
        self.refresh()

    def refresh(self) -> None:
        if self._project is None:
            return
        self._rebuild_thumbnails()
        self._update_button_states()
        self._sync_fps_exposure_controls()
        self._scroll_to_current()

    def set_playback_active(self, active: bool) -> None:
        """Lock destructive/modifying timeline operations while playback runs.

        Playback itself drives ``current_frame_index`` on a ``QTimer``, so
        manual navigation/new/delete/move/reorder/fps/exposure edits are
        disabled here to avoid racing it; Stop (Space or the toolbar
        button) remains available regardless, since that lives outside
        this widget.
        """
        self._playback_active = active
        if active:
            for widget in (
                self._prev_btn,
                self._move_left_btn,
                self._new_btn,
                self._delete_btn,
                self._move_right_btn,
                self._next_btn,
                self._fps_spin,
                self._exposure_spin,
            ):
                widget.setEnabled(False)
        else:
            self._fps_spin.setEnabled(True)
            self._exposure_spin.setEnabled(True)
            self._update_button_states()

    def _rebuild_thumbnails(self) -> None:
        assert self._project is not None
        while self._strip_layout.count():
            item = self._strip_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        project_dir = self._get_project_dir()
        self._thumbnails = []
        for index, frame in enumerate(self._project.frames):
            image = self._thumbnail_cache.get(
                self._project, index, _THUMB_IMAGE_WIDTH, _THUMB_IMAGE_HEIGHT, project_dir=project_dir
            )
            thumb = FrameThumbnail(index, frame, QPixmap.fromImage(image), self._project.exposure)
            thumb.clicked.connect(self._on_thumbnail_clicked)
            thumb.drop_target_entered.connect(self._on_drop_target_entered)
            thumb.drop_committed.connect(self._on_drop_committed)
            thumb.set_selected(index == self._project.current_frame_index)
            self._strip_layout.addWidget(thumb)
            self._thumbnails.append(thumb)
        self._strip_layout.addStretch(1)

    def _scroll_to_current(self) -> None:
        assert self._project is not None
        index = self._project.current_frame_index
        if 0 <= index < len(self._thumbnails):
            self._scroll_area.ensureWidgetVisible(self._thumbnails[index])

    def _update_button_states(self) -> None:
        assert self._project is not None
        count = len(self._project.frames)
        index = self._project.current_frame_index
        self._prev_btn.setEnabled(index > 0)
        self._next_btn.setEnabled(index < count - 1)
        self._move_left_btn.setEnabled(index > 0)
        self._move_right_btn.setEnabled(index < count - 1)
        self._delete_btn.setEnabled(count > 1)

    def _sync_fps_exposure_controls(self) -> None:
        assert self._project is not None
        self._fps_spin.blockSignals(True)
        self._fps_spin.setValue(self._project.fps)
        self._fps_spin.blockSignals(False)

        self._exposure_spin.blockSignals(True)
        self._exposure_spin.setValue(self._project.exposure)
        self._exposure_spin.blockSignals(False)

    # -- Handlers -----------------------------------------------------------

    def _on_fps_changed(self, value: int) -> None:
        if self._project is None:
            return
        self._project.fps = value
        self.project_changed.emit()

    def _on_exposure_changed(self, value: int) -> None:
        if self._project is None:
            return
        self._project.exposure = value
        self.project_changed.emit()

    def _on_thumbnail_clicked(self, index: int) -> None:
        if self._project is None or self._playback_active:
            return
        timeline.select_frame(self._project, index)
        self.refresh()
        self.project_changed.emit()

    def _on_drop_target_entered(self, hovered: FrameThumbnail | None, insert_before: bool) -> None:
        for thumb in self._thumbnails:
            if thumb is hovered:
                thumb.set_drop_indicator("left" if insert_before else "right")
            else:
                thumb.set_drop_indicator(None)

    def _on_drop_committed(self, dragged_frame_id: str, target_index: int, insert_before: bool) -> None:
        for thumb in self._thumbnails:
            thumb.set_drop_indicator(None)
        if self._project is None or self._playback_active:
            return
        from_index = next((i for i, f in enumerate(self._project.frames) if f.id == dragged_frame_id), None)
        if from_index is None:
            return
        to_index = target_index if insert_before else target_index + 1
        if from_index < to_index:
            to_index -= 1
        if to_index == from_index:
            return
        self._request_command(commands.MoveFrameCommand("Reorder Frame", from_index, to_index))

    # -- Command-backed actions (buttons + MainWindow shortcuts) -------------

    def _request_command(self, command: commands.Command) -> None:
        if self._command_executor is not None:
            self._command_executor(command)
        # No self.refresh()/project_changed.emit() here: a successful
        # execution triggers a full state resync (canvas/timeline/inspector/
        # status) from MainWindow's _execute_command, which also calls back
        # into this widget's refresh().

    def new_frame(self) -> None:
        if self._project is None or self._playback_active:
            return
        self._request_command(commands.NewFrameCommand())

    def delete_frame(self) -> None:
        if self._project is None or self._playback_active:
            return
        self._request_command(commands.DeleteFrameCommand())

    def previous_frame(self) -> None:
        if self._project is None or self._playback_active:
            return
        timeline.select_previous_frame(self._project)
        self.refresh()
        self.project_changed.emit()

    def next_frame(self) -> None:
        if self._project is None or self._playback_active:
            return
        timeline.select_next_frame(self._project)
        self.refresh()
        self.project_changed.emit()

    def move_left(self) -> None:
        if self._project is None or self._playback_active:
            return
        index = self._project.current_frame_index
        if index <= 0:
            return
        self._request_command(commands.MoveFrameCommand("Move Frame Left", index, index - 1))

    def move_right(self) -> None:
        if self._project is None or self._playback_active:
            return
        index = self._project.current_frame_index
        if index >= len(self._project.frames) - 1:
            return
        self._request_command(commands.MoveFrameCommand("Move Frame Right", index, index + 1))
