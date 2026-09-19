"""Pure, Qt-free playback/export timing math.

A **timeline pose** (one entry of ``Project.frames``) is not the same thing
as a **video frame** (one PNG in an exported sequence, or one frame of an
encoded MP4). Every timeline pose is held for ``Project.exposure``
consecutive video frames at ``Project.fps`` frames per second. By default
(``Project.smooth_animation_enabled = False``) there is no tweening/
interpolation between poses: a video frame always shows exactly one
timeline pose, unchanged, for its whole exposure window — see
:func:`expanded_timeline_indices`. Smooth Animation (when enabled) instead
eases each pose's exposure window toward the next pose — see
:func:`expanded_timeline_progress` and ``services.playback_engine``.

Example: 4 timeline poses, fps=24, exposure=3 -> 12 video frames -> a
0.5 second clip. Pose ``i`` (0-indexed) occupies 1-indexed video frames
``i * exposure + 1`` through ``(i + 1) * exposure``.
"""

from __future__ import annotations


class PlaybackValidationError(Exception):
    """Raised on invalid frame_count/exposure/fps/scene-dimension inputs."""


def expanded_timeline_indices(frame_count: int, exposure: int) -> list[int]:
    """Expand ``frame_count`` timeline poses into one entry per video frame.

    Each pose index is repeated ``exposure`` times, in order. E.g.
    ``frame_count=3, exposure=3`` -> ``[0, 0, 0, 1, 1, 1, 2, 2, 2]``.
    """
    if frame_count <= 0:
        raise PlaybackValidationError(f"frame_count must be > 0, got {frame_count}.")
    if exposure <= 0:
        raise PlaybackValidationError(f"exposure must be > 0, got {exposure}.")
    return [pose_index for pose_index in range(frame_count) for _ in range(exposure)]


def expanded_timeline_progress(frame_count: int, exposure: int) -> list[tuple[int, float]]:
    """Like :func:`expanded_timeline_indices`, paired with progress through
    each pose's own exposure window.

    Returns one ``(pose_index, progress)`` pair per video frame, in order,
    with ``progress`` stepping ``0/exposure, 1/exposure, ..., (exposure-1)/exposure``
    across each pose's window — e.g. ``frame_count=2, exposure=3`` ->
    ``[(0, 0.0), (0, 1/3), (0, 2/3), (1, 0.0), (1, 1/3), (1, 2/3)]``.

    ``progress`` never reaches exactly ``1.0`` for a pose's own window: that
    value is, by construction, identical to the *next* pose's own
    ``progress=0.0`` (see ``services.playback_engine.PlaybackEngine.get_interpolated_frame``,
    which returns ``start_frame`` unchanged at ``progress=0.0``) — so it
    would be a redundant duplicate frame, not a new one, if included here.
    Only meaningful when ``Project.smooth_animation_enabled``; callers
    rendering the classic discrete timeline use
    :func:`expanded_timeline_indices` instead and ignore progress entirely.
    """
    if frame_count <= 0:
        raise PlaybackValidationError(f"frame_count must be > 0, got {frame_count}.")
    if exposure <= 0:
        raise PlaybackValidationError(f"exposure must be > 0, got {exposure}.")
    return [(pose_index, step / exposure) for pose_index in range(frame_count) for step in range(exposure)]


def output_frame_count(frame_count: int, exposure: int) -> int:
    """Total number of video frames: ``frame_count * exposure``."""
    return len(expanded_timeline_indices(frame_count, exposure))


def output_duration_seconds(frame_count: int, exposure: int, fps: int) -> float:
    """Estimated duration in seconds of the exported video."""
    if fps <= 0:
        raise PlaybackValidationError(f"fps must be > 0, got {fps}.")
    return output_frame_count(frame_count, exposure) / fps


def validate_playback_settings(fps: int, exposure: int, scene_width: int, scene_height: int) -> None:
    """Raise :class:`PlaybackValidationError` unless all four values are positive integers.

    Used both when loading a project (``services/project_io.py``) and again
    right before export, since a project loaded once can be mutated by the
    UI afterwards (e.g. malformed data slipping in some other way).
    """
    if fps <= 0:
        raise PlaybackValidationError(f"fps must be > 0, got {fps}.")
    if exposure <= 0:
        raise PlaybackValidationError(f"exposure must be > 0, got {exposure}.")
    if scene_width <= 0:
        raise PlaybackValidationError(f"scene width must be > 0, got {scene_width}.")
    if scene_height <= 0:
        raise PlaybackValidationError(f"scene height must be > 0, got {scene_height}.")
