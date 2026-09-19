"""QTimer-based timeline playback controller.

Playback respects ``Project.fps``/``Project.exposure``: each timeline pose
is held for ``exposure`` timer ticks before advancing, and the timer
interval is ``1000 / fps`` ms — the same cadence PNG/MP4 export uses (see
``domain.playback``). Playback never mutates rig/layer/camera state: the
only thing it writes is ``Project.current_frame_index``, via the same
``domain.timeline.select_frame`` every other frame-selection action in the
app already goes through, so playback and manual navigation stay
behaviourally identical.

:attr:`PlaybackController.current_sub_progress` additionally tracks
progress (``0.0``-``<1.0``) through the current pose's own exposure
window, mirroring ``domain.playback.expanded_timeline_progress`` — it's
what lets ``app/main_window.py`` ask ``services.playback_engine`` for a
live, eased in-between frame when ``Project.smooth_animation_enabled`` is
on. It's inert (always ``0.0``, and ``frame_changed`` fires no more often
than before) whenever that's off, so plain discrete playback is completely
unaffected.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from pivotcut.domain.models import Project
from pivotcut.domain.timeline import select_frame


class PlaybackController(QObject):
    """Drives ``Project.current_frame_index`` forward at fps/exposure cadence."""

    #: The selected Pose itself changed (or the loop wrapped back to the
    #: first Pose) — connect this to a full UI resync, exactly as before
    #: this class had any notion of Smooth Animation.
    frame_changed = Signal()

    #: Smooth Animation only: fires on a tick *within* the current pose's
    #: exposure window, where the pose itself hasn't changed but
    #: ``current_sub_progress`` has. Connect this to a canvas-only resync —
    #: never the same handler as ``frame_changed``, which would otherwise
    #: rebuild the whole timeline thumbnail strip on every tick.
    sub_frame_changed = Signal()

    playback_started = Signal()
    playback_stopped = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._project: Project | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._ticks_on_current_pose = 0
        self._is_playing = False
        self._loop = False
        self._sub_progress = 0.0

    def set_project(self, project: Project | None) -> None:
        """Rebind to a (possibly new) project, stopping playback first if needed."""
        if self._is_playing:
            self.stop()
        self._project = project

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def current_sub_progress(self) -> float:
        """Progress through the current pose's own exposure window, in
        ``[0.0, 1.0)`` — ``0.0`` whenever stopped. See module docstring."""
        return self._sub_progress

    def set_loop(self, loop: bool) -> None:
        self._loop = loop

    @property
    def loop(self) -> bool:
        return self._loop

    def toggle(self) -> None:
        """Play if stopped, stop if playing — what the Space shortcut/toolbar button call."""
        if self._is_playing:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        """Start playback from the currently selected frame. No-op if already playing or empty."""
        if self._project is None or self._is_playing or not self._project.frames:
            return
        fps = max(1, self._project.fps)
        interval_ms = max(1, round(1000 / fps))
        self._ticks_on_current_pose = 0
        self._sub_progress = 0.0
        self._is_playing = True
        self._timer.start(interval_ms)
        self.playback_started.emit()

    def stop(self) -> None:
        if not self._is_playing:
            return
        self._timer.stop()
        self._is_playing = False
        # Reset so a stopped canvas always shows the exact selected pose,
        # never a mid-ease frame left over from the instant playback stopped.
        self._sub_progress = 0.0
        self.playback_stopped.emit()

    def _on_tick(self) -> None:
        if self._project is None:
            self.stop()
            return
        exposure = max(1, self._project.exposure)
        self._ticks_on_current_pose += 1
        self._sub_progress = self._ticks_on_current_pose / exposure
        if self._ticks_on_current_pose < exposure:
            if self._project.smooth_animation_enabled:
                # Mid-exposure tick: the pose itself hasn't changed, but
                # there's a new eased in-between frame to draw — see
                # app/main_window.py's use of current_sub_progress. Plain
                # discrete playback (the default) never emits here, exactly
                # as before this feature existed.
                self.sub_frame_changed.emit()
            return
        self._ticks_on_current_pose = 0
        self._sub_progress = 0.0

        last_index = len(self._project.frames) - 1
        if self._project.current_frame_index >= last_index:
            if self._loop:
                select_frame(self._project, 0)
                self.frame_changed.emit()
            else:
                self.stop()
            return

        select_frame(self._project, self._project.current_frame_index + 1)
        self.frame_changed.emit()
