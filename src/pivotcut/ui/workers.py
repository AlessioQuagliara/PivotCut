"""Qt worker objects that run blocking ``services`` calls off the main thread.

Every ``services`` module in this app (``ffmpeg_export``, ``ai_animator``,
...) is deliberately synchronous/blocking and Qt-free — see their module
docstrings. This module is the one place that bridges a blocking service
call onto a background ``QThread`` and back onto the main thread via
signals, so ``app/main_window.py`` (and ``ui/main_window_patch.py``) never
has to block the UI event loop waiting on a network request.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from pivotcut.domain.models import KeyframeState
from pivotcut.services.ai_animator import AIAnimatorError, AnimationContext, BaseAIAnimator


class AIAnimationWorker(QThread):
    """Runs one :class:`~pivotcut.services.ai_animator.BaseAIAnimator` request
    on a background thread.

    Usage (see ``ui/main_window_patch.py`` for a full example)::

        worker = AIAnimationWorker(animator, context)
        worker.finished.connect(on_finished)   # list[KeyframeState]
        worker.error.connect(on_error)         # str
        worker.start()

    Exactly one of ``finished``/``error`` fires per run, never both — this
    class never lets an exception escape :meth:`run` (which would silently
    terminate the thread with no signal at all).
    """

    #: Emitted on success with the validated keyframe sequence, ordered by
    #: ``KeyframeState.frame_index``.
    finished = Signal(list)

    #: Emitted on any failure (SDK missing, network/API error, malformed
    #: response) with a human-readable message ready to show the user.
    error = Signal(str)

    def __init__(self, animator: BaseAIAnimator, context: AnimationContext, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._animator = animator
        self._context = context

    def run(self) -> None:
        try:
            keyframes: list[KeyframeState] = self._animator.generate_keyframes(self._context)
        except AIAnimatorError as exc:
            self.error.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - last-resort guard: run() must never raise
            self.error.emit(f"Unexpected error during AI animation: {exc}")
            return
        self.finished.emit(keyframes)
