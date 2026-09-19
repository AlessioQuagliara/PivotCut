"""Command pattern for undoable/redoable edits, plus the ``UndoRedoStack``.

``Project``/``Frame`` remain the single source of truth: every command here
operates *only* on a ``Project`` instance (no Qt, no UI references), and the
UI layer's job is limited to constructing a command from a before/after
snapshot (or letting a command wrap an existing pure ``domain`` function)
and handing it to a shared ``UndoRedoStack``. The stack applies the command,
then the caller re-syncs the UI from the (now authoritative) model — undo/
redo never reaches into widgets directly.

Two families of command live here:

- **Creation/structural commands** (``NewFrameCommand``, ``DeleteFrameCommand``)
  wrap an existing pure ``domain.timeline`` function and lazily capture
  whatever it produced (a fresh id, a duplicate-label suffix, ...) on their
  *first* ``apply()`` call, so a subsequent redo can replay the exact same
  effect without re-deriving it (which would mint a new random id and break
  identity across undo/redo cycles). ``MoveFrameCommand``/
  ``CreateRigCommand``/``AddLayerCommand`` don't need this laziness: a
  reorder is already fully described by two indices, and rig/layer creation
  already happens (via the existing pure factory functions) before the
  command object even exists.
- **Field-snapshot commands** (``TransformBoneCommand``, ``EditLayerCommand``,
  ``EditCameraCommand``) hold a *deep copy* of the edited object's state
  immediately before and immediately after one atomic edit "session" (one
  finished drag, one completed inspector edit) — never a snapshot of the
  whole ``Project``. ``apply()``/``revert()`` just copy every field from the
  relevant snapshot back onto the live object in place.
"""

from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass, field, fields
from typing import Protocol

from pivotcut.domain import timeline
from pivotcut.domain.camera import Camera
from pivotcut.domain.layer import Layer
from pivotcut.domain.models import Frame, Project
from pivotcut.domain.rig import Bone, Rig, RigTemplate


class Command(Protocol):
    """Anything with a human-readable description plus apply()/revert()."""

    description: str

    def apply(self, project: Project) -> None: ...

    def revert(self, project: Project) -> None: ...


def _restore_fields(target: object, snapshot: object) -> None:
    """Copy every dataclass field value from ``snapshot`` onto ``target`` in place."""
    for f in fields(snapshot):  # type: ignore[arg-type]
        setattr(target, f.name, getattr(snapshot, f.name))


def _find_frame(project: Project, frame_id: str) -> Frame | None:
    for frame in project.frames:
        if frame.id == frame_id:
            return frame
    return None


def _find_bone(project: Project, frame_id: str, rig_id: str, bone_id: str) -> Bone | None:
    frame = _find_frame(project, frame_id)
    if frame is None:
        return None
    for rig in frame.rigs:
        if rig.id == rig_id:
            for bone in rig.bones:
                if bone.id == bone_id:
                    return bone
    return None


def _find_layer(project: Project, frame_id: str, layer_id: str) -> Layer | None:
    frame = _find_frame(project, frame_id)
    if frame is None:
        return None
    for layer in frame.layers:
        if layer.id == layer_id:
            return layer
    return None


def _find_rig(project: Project, frame_id: str, rig_id: str) -> Rig | None:
    frame = _find_frame(project, frame_id)
    if frame is None:
        return None
    for rig in frame.rigs:
        if rig.id == rig_id:
            return rig
    return None


# -- Frame-list structural commands ------------------------------------------------


@dataclass
class NewFrameCommand:
    """Wraps ``domain.timeline.new_frame_after_current``."""

    description: str = "New Frame"
    _created_frame: Frame | None = field(default=None, init=False, repr=False)
    _insert_index: int = field(default=-1, init=False, repr=False)
    _previous_current_index: int = field(default=-1, init=False, repr=False)

    def apply(self, project: Project) -> None:
        if self._created_frame is None:
            self._previous_current_index = project.current_frame_index
            self._created_frame = timeline.new_frame_after_current(project)
            self._insert_index = project.current_frame_index
        else:
            project.frames.insert(self._insert_index, self._created_frame)
            project.current_frame_index = self._insert_index

    def revert(self, project: Project) -> None:
        del project.frames[self._insert_index]
        project.current_frame_index = self._previous_current_index


@dataclass
class DeleteFrameCommand:
    """Wraps ``domain.timeline.delete_current_frame``.

    ``apply()`` may raise ``timeline.CannotDeleteLastFrameError`` on its
    first call (deleting the only remaining frame) — callers must not push
    this command onto the undo stack unless ``apply()`` succeeds, which
    ``UndoRedoStack.execute`` already guarantees (it only records the
    command *after* a successful ``apply()``).
    """

    description: str = "Delete Frame"
    _removed_frame: Frame | None = field(default=None, init=False, repr=False)
    _removed_index: int = field(default=-1, init=False, repr=False)
    _previous_current_index: int = field(default=-1, init=False, repr=False)
    _resulting_current_index: int = field(default=-1, init=False, repr=False)

    def apply(self, project: Project) -> None:
        if self._removed_frame is None:
            self._previous_current_index = project.current_frame_index
            self._removed_index = project.current_frame_index
            self._removed_frame = timeline.delete_current_frame(project)
            self._resulting_current_index = project.current_frame_index
        else:
            del project.frames[self._removed_index]
            project.current_frame_index = self._resulting_current_index

    def revert(self, project: Project) -> None:
        project.frames.insert(self._removed_index, self._removed_frame)
        project.current_frame_index = self._previous_current_index


@dataclass
class MoveFrameCommand:
    """Wraps ``domain.timeline.move_frame`` — backs Move Left/Right and drag-and-drop reorder."""

    description: str
    from_index: int
    to_index: int
    _previous_current_index: int = field(default=-1, init=False, repr=False)

    def apply(self, project: Project) -> None:
        self._previous_current_index = project.current_frame_index
        timeline.move_frame(project, self.from_index, self.to_index)

    def revert(self, project: Project) -> None:
        timeline.move_frame(project, self.to_index, self.from_index)
        project.current_frame_index = self._previous_current_index


@dataclass
class InsertFramesCommand:
    """Inserts a batch of already-built ``Frame`` objects at ``insert_index``.

    Generic (not AI-specific): ``NewFrameCommand`` builds exactly one blank
    frame via ``domain.timeline``; this command instead takes frames the
    caller already constructed in full — its one real use today is
    ``domain.ai_keyframe.build_frame_from_keyframe`` output from an AI
    animation request, one ``Frame`` per generated ``KeyframeState``, kept
    exactly as undoable as every other editing action in this app. Selects
    the first inserted frame on ``apply()``.
    """

    description: str
    insert_index: int
    frames: list[Frame]
    _previous_current_index: int = field(default=-1, init=False, repr=False)

    def apply(self, project: Project) -> None:
        self._previous_current_index = project.current_frame_index
        for offset, frame in enumerate(self.frames):
            project.frames.insert(self.insert_index + offset, frame)
        project.current_frame_index = self.insert_index

    def revert(self, project: Project) -> None:
        del project.frames[self.insert_index : self.insert_index + len(self.frames)]
        project.current_frame_index = self._previous_current_index


@dataclass
class CreateRigCommand:
    """Adds an already-constructed ``Rig`` (via ``create_rig_from_asset``) to a frame."""

    description: str
    frame_id: str
    rig: Rig

    def apply(self, project: Project) -> None:
        frame = _find_frame(project, self.frame_id)
        if frame is not None and not any(r.id == self.rig.id for r in frame.rigs):
            frame.rigs.append(self.rig)

    def revert(self, project: Project) -> None:
        frame = _find_frame(project, self.frame_id)
        if frame is not None:
            frame.rigs = [r for r in frame.rigs if r.id != self.rig.id]


@dataclass
class DeleteRigCommand:
    """Removes an existing ``Rig`` instance from a frame — the mirror image
    of ``CreateRigCommand``. Used by "delete the selected Part" when the
    selected bone is a rig's root (deleting the root deletes the whole
    character instance, since a rig always needs exactly one root)."""

    description: str
    frame_id: str
    rig: Rig

    def apply(self, project: Project) -> None:
        frame = _find_frame(project, self.frame_id)
        if frame is not None:
            frame.rigs = [r for r in frame.rigs if r.id != self.rig.id]

    def revert(self, project: Project) -> None:
        frame = _find_frame(project, self.frame_id)
        if frame is not None and not any(r.id == self.rig.id for r in frame.rigs):
            frame.rigs.append(self.rig)


@dataclass
class AddLayerCommand:
    """Adds an already-constructed ``Layer`` (via ``create_layer_from_asset``) to a frame."""

    description: str
    frame_id: str
    layer: Layer

    def apply(self, project: Project) -> None:
        frame = _find_frame(project, self.frame_id)
        if frame is not None and not any(l.id == self.layer.id for l in frame.layers):
            frame.layers.append(self.layer)

    def revert(self, project: Project) -> None:
        frame = _find_frame(project, self.frame_id)
        if frame is not None:
            frame.layers = [l for l in frame.layers if l.id != self.layer.id]


# -- Character Rig Builder (Milestone 6A) --------------------------------------------


@dataclass
class EditRigStructureCommand:
    """One atomic "Save" from the Character Rig Builder, editing a single
    ``Rig`` *instance*'s bones/root — never the whole ``Project``.

    Per the Milestone 6A strategy, editing a rig's structure in the builder
    (Add/Remove/Reparent/Duplicate Part, pivot/attach edits, ...) only ever
    touches the *current frame's* rig instance, never propagating to other
    frames or to any ``RigTemplate`` — that stays an explicit, separate
    action (:class:`SaveRigTemplateCommand`, "Save as Template"). This is
    what keeps existing poses on other frames from silently breaking when
    someone reshapes a character's skeleton.
    """

    description: str
    frame_id: str
    rig_id: str
    before_bones: list[Bone]
    before_root_bone_id: str
    after_bones: list[Bone]
    after_root_bone_id: str

    def apply(self, project: Project) -> None:
        rig = _find_rig(project, self.frame_id, self.rig_id)
        if rig is not None:
            rig.bones = copy.deepcopy(self.after_bones)
            rig.root_bone_id = self.after_root_bone_id

    def revert(self, project: Project) -> None:
        rig = _find_rig(project, self.frame_id, self.rig_id)
        if rig is not None:
            rig.bones = copy.deepcopy(self.before_bones)
            rig.root_bone_id = self.before_root_bone_id


@dataclass
class SaveRigTemplateCommand:
    """Create or update one entry in ``Project.rig_templates`` (the Rig Library).

    If ``after_template.id`` already names an existing template, that entry
    is overwritten (and the previous content restored on undo); otherwise a
    new entry is appended (and removed on undo). Which case applies is
    re-derived from live project state on every ``apply()`` — safe because
    ``revert()`` always restores that exact prior state first, so a redo
    sees the identical starting condition as the original ``apply()``.
    """

    description: str
    after_template: RigTemplate
    _existed_before: bool = field(default=False, init=False, repr=False)
    _before_template: RigTemplate | None = field(default=None, init=False, repr=False)

    def apply(self, project: Project) -> None:
        index = next((i for i, t in enumerate(project.rig_templates) if t.id == self.after_template.id), None)
        if index is not None:
            self._existed_before = True
            self._before_template = copy.deepcopy(project.rig_templates[index])
            project.rig_templates[index] = copy.deepcopy(self.after_template)
        else:
            self._existed_before = False
            self._before_template = None
            project.rig_templates.append(copy.deepcopy(self.after_template))

    def revert(self, project: Project) -> None:
        if self._existed_before and self._before_template is not None:
            for i, t in enumerate(project.rig_templates):
                if t.id == self.after_template.id:
                    project.rig_templates[i] = copy.deepcopy(self._before_template)
                    return
        else:
            project.rig_templates = [t for t in project.rig_templates if t.id != self.after_template.id]


@dataclass
class DeleteRigTemplateCommand:
    """Remove one entry from ``Project.rig_templates`` (Rig Library management)."""

    description: str
    template_id: str
    _removed_template: RigTemplate | None = field(default=None, init=False, repr=False)
    _removed_index: int = field(default=-1, init=False, repr=False)

    def apply(self, project: Project) -> None:
        for i, t in enumerate(project.rig_templates):
            if t.id == self.template_id:
                self._removed_index = i
                self._removed_template = copy.deepcopy(t)
                del project.rig_templates[i]
                return

    def revert(self, project: Project) -> None:
        if self._removed_template is not None:
            project.rig_templates.insert(self._removed_index, copy.deepcopy(self._removed_template))


# -- Field-snapshot edit commands --------------------------------------------------


@dataclass
class TransformBoneCommand:
    """One committed bone move/rotate (a whole drag gesture, not per-mouse-move)."""

    description: str
    frame_id: str
    rig_id: str
    bone_id: str
    before: Bone
    after: Bone

    def apply(self, project: Project) -> None:
        bone = _find_bone(project, self.frame_id, self.rig_id, self.bone_id)
        if bone is not None:
            _restore_fields(bone, self.after)

    def revert(self, project: Project) -> None:
        bone = _find_bone(project, self.frame_id, self.rig_id, self.bone_id)
        if bone is not None:
            _restore_fields(bone, self.before)


@dataclass
class EditLayerCommand:
    """One committed inspector edit session on a layer (all fields changed together)."""

    description: str
    frame_id: str
    layer_id: str
    before: Layer
    after: Layer

    def apply(self, project: Project) -> None:
        layer = _find_layer(project, self.frame_id, self.layer_id)
        if layer is not None:
            _restore_fields(layer, self.after)

    def revert(self, project: Project) -> None:
        layer = _find_layer(project, self.frame_id, self.layer_id)
        if layer is not None:
            _restore_fields(layer, self.before)


@dataclass
class EditCameraCommand:
    """One committed inspector edit session on a frame's camera (includes Reset Camera)."""

    description: str
    frame_id: str
    before: Camera
    after: Camera

    def apply(self, project: Project) -> None:
        frame = _find_frame(project, self.frame_id)
        if frame is not None:
            _restore_fields(frame.camera, self.after)

    def revert(self, project: Project) -> None:
        frame = _find_frame(project, self.frame_id)
        if frame is not None:
            _restore_fields(frame.camera, self.before)


# -- Undo/redo stack ----------------------------------------------------------------

DEFAULT_MAX_HISTORY = 100


class UndoRedoStack:
    """Bounded undo/redo history. Executing a new command clears the redo branch."""

    def __init__(self, max_size: int = DEFAULT_MAX_HISTORY) -> None:
        self._undo_stack: deque[Command] = deque(maxlen=max_size)
        self._redo_stack: list[Command] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def undo_description(self) -> str | None:
        return self._undo_stack[-1].description if self._undo_stack else None

    @property
    def redo_description(self) -> str | None:
        return self._redo_stack[-1].description if self._redo_stack else None

    def clear(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()

    def execute(self, command: Command, project: Project) -> None:
        """Apply ``command`` and push it onto the undo stack.

        If ``apply()`` raises, the command is *not* recorded (so a failed
        action — e.g. deleting the last remaining frame — never corrupts
        the history), and the exception propagates to the caller.
        """
        command.apply(project)
        self._undo_stack.append(command)
        self._redo_stack.clear()

    def undo(self, project: Project) -> Command | None:
        if not self._undo_stack:
            return None
        command = self._undo_stack.pop()
        command.revert(project)
        self._redo_stack.append(command)
        return command

    def redo(self, project: Project) -> Command | None:
        if not self._redo_stack:
            return None
        command = self._redo_stack.pop()
        command.apply(project)
        self._undo_stack.append(command)
        return command


def snapshot_bone(bone: Bone) -> Bone:
    """Deep-copy a single ``Bone`` — the "before"/"after" snapshot unit for drag commands."""
    return copy.deepcopy(bone)


def snapshot_layer(layer: Layer) -> Layer:
    """Deep-copy a single ``Layer`` — the "before"/"after" snapshot unit for inspector edits."""
    return copy.deepcopy(layer)


def snapshot_camera(camera: Camera) -> Camera:
    """Deep-copy a ``Camera`` — the "before"/"after" snapshot unit for inspector edits/Reset Camera."""
    return copy.deepcopy(camera)


def snapshot_bones(bones: list[Bone]) -> list[Bone]:
    """Deep-copy a bone list — the "before"/"after" snapshot unit for ``EditRigStructureCommand``."""
    return copy.deepcopy(bones)
