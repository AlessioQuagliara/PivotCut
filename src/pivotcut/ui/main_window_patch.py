"""AI Text-to-Animation: UI integration example for ``app/main_window.py``.

This is deliberately kept as a *separate, opt-in patch* rather than code
edited directly into ``MainWindow`` — it shows exactly how an AI animation
feature plugs into this app's existing, established machinery (no new
patterns invented):

- ``MainWindow._execute_command`` — the same single entry point every other
  undoable action already goes through (rig creation, inspector edits,
  frame reorder, ...). AI-generated frames are inserted via
  ``domain.commands.InsertFramesCommand`` through this exact call, so they
  are just as undoable as a manual edit and need no special-case refresh
  logic: ``_execute_command`` already re-syncs the canvas/timeline/inspector
  and thumbnail cache for us (see ``app/main_window.py``'s
  ``_after_history_change``/``_on_project_state_changed``).
- ``MainWindow._set_mutation_ui_enabled`` — the same lock already used for
  playback/export, reused here so a user can't edit the project while an AI
  request is in flight.
- ``ui.workers.AIAnimationWorker`` — keeps the network call off the main
  thread; this module only ever touches ``MainWindow`` from its
  ``finished``/``error`` slots, which Qt always delivers on the main thread.

To wire this in, call :func:`install_ai_animation_action` once after
building a ``MainWindow`` (e.g. at the end of ``main.py``, or inside
``MainWindow.__init__`` once this feature graduates from example to a
permanent part of the app)::

    window = MainWindow()
    install_ai_animation_action(window)
    window.show()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pivotcut.domain import ai_keyframe, commands
from pivotcut.domain.models import KeyframeState
from pivotcut.services.ai_animator import (
    AIAnimatorError,
    AnimationContext,
    AnthropicAIAnimator,
    BaseAIAnimator,
    OpenAIAnimator,
    build_animation_context,
)
from pivotcut.ui.workers import AIAnimationWorker

if TYPE_CHECKING:
    from pivotcut.app.main_window import MainWindow

_PROVIDERS: dict[str, type[BaseAIAnimator]] = {
    "Anthropic (Claude)": AnthropicAIAnimator,
    "OpenAI": OpenAIAnimator,
}


class AIAnimationPromptDialog(QDialog):
    """Gathers what one AI animation request needs: the action to stage, how
    many keyframes to generate, which provider, and its API key.

    Holds no project state of its own — purely a form. See
    :meth:`result` for the four values read back after Accept.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI Animate Selected Character")
        self.resize(480, 320)

        outer = QVBoxLayout(self)
        form = QFormLayout()
        outer.addLayout(form)

        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setPlaceholderText("e.g. \"waves hello with the right arm, then smiles\"")
        self._prompt_edit.setWhatsThis(
            "Describe the action in plain language. The AI stages it as a sequence of new "
            "Poses inserted right after the current one — nothing is generated until you click OK."
        )
        form.addRow("Action to animate", self._prompt_edit)

        self._frame_count_spin = QSpinBox()
        self._frame_count_spin.setRange(1, 60)
        self._frame_count_spin.setValue(8)
        self._frame_count_spin.setWhatsThis("How many new Poses to generate for this action.")
        form.addRow("Number of Poses", self._frame_count_spin)

        self._provider_combo = QComboBox()
        self._provider_combo.addItems(_PROVIDERS.keys())
        self._provider_combo.setWhatsThis("Which cloud AI provider generates the animation.")
        form.addRow("AI Provider", self._provider_combo)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setWhatsThis(
            "Your API key for the selected provider. Used only for this request — PivotCut "
            "never stores it in the project file."
        )
        form.addRow("API Key", self._api_key_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def result_values(self) -> tuple[str, int, str, str]:
        """``(prompt, frame_count, provider_name, api_key)`` — call after Accept."""
        return (
            self._prompt_edit.toPlainText().strip(),
            self._frame_count_spin.value(),
            self._provider_combo.currentText(),
            self._api_key_edit.text().strip(),
        )


def install_ai_animation_action(window: "MainWindow") -> QAction:
    """Add "AI Animate Selected Character…" to ``window``'s menu bar.

    Returns the created ``QAction`` (also reachable afterwards as
    ``window._ai_animate_action``, mirroring how every other action already
    lives on ``MainWindow``).
    """
    action = QAction("AI Animate Selected Character…", window)
    action.setWhatsThis(
        "Describe an action in plain language (e.g. \"waves hello, then smiles\") and an AI "
        "provider generates a sequence of new Poses for it — bone rotations, PNG swaps for "
        "expressions, environment/camera movement — inserted right after the current Pose. "
        "Requires your own API key for the chosen provider; runs in the background."
    )
    action.triggered.connect(lambda: _on_ai_animate_triggered(window))
    ai_menu = window.menuBar().addMenu("&AI")
    ai_menu.addAction(action)
    window._ai_animate_action = action  # noqa: SLF001 - intentional, see docstring
    return action


def _on_ai_animate_triggered(window: "MainWindow") -> None:
    dialog = AIAnimationPromptDialog(window)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    prompt, frame_count, provider_name, api_key = dialog.result_values()
    if not prompt:
        QMessageBox.warning(window, "AI Animate", "Describe the action you want animated first.")
        return
    if not api_key:
        QMessageBox.warning(window, "AI Animate", "An API key is required.")
        return

    scene = window._project.scene_settings  # noqa: SLF001 - see module docstring
    base_frame = window._current_frame()  # noqa: SLF001
    context = build_animation_context(
        base_frame, window._project.assets, prompt, frame_count, scene.width, scene.height  # noqa: SLF001
    )

    animator_cls = _PROVIDERS[provider_name]
    try:
        animator: BaseAIAnimator = animator_cls(api_key)
    except AIAnimatorError as exc:
        QMessageBox.critical(window, "AI Animate", str(exc))
        return

    worker = AIAnimationWorker(animator, context, parent=window)
    # Keep a reference on the window itself so the QThread isn't garbage
    # collected mid-run (PySide6 gives no other owner of a bare local
    # variable) and a second click can't start an overlapping request.
    window._ai_animation_worker = worker  # noqa: SLF001

    window._set_mutation_ui_enabled(False)  # noqa: SLF001 - same lock export/playback already use

    def on_finished(keyframes: list[KeyframeState]) -> None:
        insert_index = window._project.current_frame_index + 1  # noqa: SLF001
        new_frames = [
            ai_keyframe.build_frame_from_keyframe(base_frame, keyframe, label=f"AI: {prompt[:24]}")
            for keyframe in keyframes
        ]
        # Routed through the shared undo/redo stack exactly like every
        # other editing action — Undo removes the whole generated sequence
        # in one step. _execute_command already re-syncs the canvas,
        # timeline (including thumbnails, rendered fresh for these brand
        # new frame ids), inspector and Undo/Redo menu items — no manual
        # refresh call needed here.
        window._execute_command(  # noqa: SLF001
            commands.InsertFramesCommand(f"AI Animate: {prompt[:40]}", insert_index, new_frames)
        )
        window._set_mutation_ui_enabled(True)  # noqa: SLF001

    def on_error(message: str) -> None:
        window._set_mutation_ui_enabled(True)  # noqa: SLF001
        QMessageBox.critical(window, "AI Animate Failed", message)

    worker.finished.connect(on_finished)
    worker.error.connect(on_error)
    worker.start()
