"""Pure, Qt-free operations on a :class:`~pivotcut.domain.models.Project` timeline.

The timeline is the single source of truth for frame insertion, deletion,
reordering and selection. The UI layer must call into these functions
rather than mutate ``project.frames``/``project.current_frame_index`` directly,
so that behaviour stays identical between the GUI and the test suite.
"""

from __future__ import annotations

import copy
import re

from pivotcut.domain.models import Frame, Project, new_frame_id

_COPY_SUFFIX_RE = re.compile(r"^(?P<base>.*) \(copy(?: (?P<num>\d+))?\)$")


class TimelineError(Exception):
    """Base class for invalid timeline operations."""


class CannotDeleteLastFrameError(TimelineError):
    """Raised when trying to delete the only remaining frame."""


def _duplicate_label(label: str) -> str:
    """Derive a readable label for a duplicated frame.

    Empty labels stay empty: appending "(copy)" to nothing would just add
    noise to the timeline UI without conveying information. Non-empty
    labels get an incrementing "(copy)" / "(copy 2)" suffix so repeated
    duplication doesn't pile up "(copy) (copy) (copy)".
    """
    if not label:
        return ""
    match = _COPY_SUFFIX_RE.match(label)
    if match:
        base = match.group("base")
        num = int(match.group("num")) if match.group("num") else 1
        return f"{base} (copy {num + 1})"
    return f"{label} (copy)"


def new_frame_after_current(project: Project) -> Frame:
    """Duplicate the current frame and insert it right after it.

    The new frame gets a fresh UUID, duplicates duration/label from the
    source frame (label gets a readable copy suffix), and becomes selected.
    Its rigs/layers/camera are a deep copy of the source frame's: editing a
    pose, a layer or the camera in the new frame must never affect the
    source frame, but rig/bone/layer ids stay identical across frames since
    they represent the same character/layer through different poses over
    time.
    """
    source = project.frames[project.current_frame_index]
    new_frame = Frame(
        id=new_frame_id(),
        duration=source.duration,
        label=_duplicate_label(source.label),
        rigs=copy.deepcopy(source.rigs),
        camera=copy.deepcopy(source.camera),
        layers=copy.deepcopy(source.layers),
    )
    insert_at = project.current_frame_index + 1
    project.frames.insert(insert_at, new_frame)
    project.current_frame_index = insert_at
    return new_frame


def delete_current_frame(project: Project) -> Frame:
    """Delete the currently selected frame.

    Raises ``CannotDeleteLastFrameError`` if it is the only frame left.
    Selection moves to the previous frame when possible, otherwise stays
    on the new first frame.
    """
    if len(project.frames) <= 1:
        raise CannotDeleteLastFrameError("Cannot delete the last remaining frame.")

    removed = project.frames.pop(project.current_frame_index)
    project.current_frame_index = max(0, project.current_frame_index - 1)
    return removed


def move_frame(project: Project, from_index: int, to_index: int) -> None:
    """Move ``project.frames[from_index]`` so it ends up at ``to_index``.

    ``to_index`` is expressed in the *resulting* list (0-indexed, clamped to
    the valid range) — i.e. "the frame should end up at position
    ``to_index``", not "insert before/after some other frame". Selection
    follows the moved frame. A no-op (frame stays, selection untouched) if
    ``from_index`` is out of range or already equals the clamped
    ``to_index`` — this is also what makes dropping a dragged frame back on
    its own slot a true no-op.

    This single generalized function backs both the adjacent
    ``move_current_frame_left``/``move_current_frame_right`` moves below and
    drag-and-drop timeline reordering (see ``ui/timeline_widget.py``),
    which can move a frame by an arbitrary number of positions in one step.
    """
    frames = project.frames
    if not (0 <= from_index < len(frames)):
        return
    to_index = max(0, min(to_index, len(frames) - 1))
    if to_index == from_index:
        return
    frame = frames.pop(from_index)
    frames.insert(to_index, frame)
    project.current_frame_index = to_index


def move_current_frame_left(project: Project) -> None:
    """Move the current frame one position left, if any."""
    index = project.current_frame_index
    move_frame(project, index, index - 1)


def move_current_frame_right(project: Project) -> None:
    """Move the current frame one position right, if any."""
    index = project.current_frame_index
    move_frame(project, index, index + 1)


def select_frame(project: Project, index: int) -> None:
    """Select a frame by index, clamped to the valid range."""
    project.current_frame_index = max(0, min(index, len(project.frames) - 1))


def select_previous_frame(project: Project) -> None:
    select_frame(project, project.current_frame_index - 1)


def select_next_frame(project: Project) -> None:
    select_frame(project, project.current_frame_index + 1)
