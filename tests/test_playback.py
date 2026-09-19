from __future__ import annotations

import pytest

from pivotcut.domain.playback import (
    PlaybackValidationError,
    expanded_timeline_indices,
    expanded_timeline_progress,
    output_duration_seconds,
    output_frame_count,
    validate_playback_settings,
)

# -- expanded_timeline_indices --------------------------------------------------


def test_expanded_timeline_indices_repeats_each_pose_by_exposure() -> None:
    assert expanded_timeline_indices(3, 3) == [0, 0, 0, 1, 1, 1, 2, 2, 2]


def test_expanded_timeline_indices_exposure_one_is_identity() -> None:
    assert expanded_timeline_indices(4, 1) == [0, 1, 2, 3]


def test_expanded_timeline_indices_single_frame() -> None:
    assert expanded_timeline_indices(1, 5) == [0, 0, 0, 0, 0]


def test_expanded_timeline_indices_four_poses_example_from_spec() -> None:
    # 4 timeline poses, exposure=3 -> 12 output frames, matching the milestone's
    # own worked example (fps=24 -> 0.5s, checked separately below).
    result = expanded_timeline_indices(4, 3)
    assert len(result) == 12
    assert result == [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3]


@pytest.mark.parametrize("frame_count", [0, -1, -5])
def test_expanded_timeline_indices_rejects_non_positive_frame_count(frame_count: int) -> None:
    with pytest.raises(PlaybackValidationError):
        expanded_timeline_indices(frame_count, 3)


# -- expanded_timeline_progress (Smooth Animation) -------------------------------------


def test_expanded_timeline_progress_steps_through_each_poses_own_window() -> None:
    result = expanded_timeline_progress(2, 3)
    assert result == [(0, 0.0), (0, 1 / 3), (0, 2 / 3), (1, 0.0), (1, 1 / 3), (1, 2 / 3)]


def test_expanded_timeline_progress_matches_indices_pose_ordering() -> None:
    progress = expanded_timeline_progress(4, 3)
    indices = expanded_timeline_indices(4, 3)
    assert [pose_index for pose_index, _ in progress] == indices


def test_expanded_timeline_progress_exposure_one_is_always_zero() -> None:
    # No sub-frame to ease through when a pose is only ever shown once.
    assert expanded_timeline_progress(3, 1) == [(0, 0.0), (1, 0.0), (2, 0.0)]


def test_expanded_timeline_progress_never_reaches_1_within_a_poses_window() -> None:
    for _, progress in expanded_timeline_progress(5, 6):
        assert 0.0 <= progress < 1.0


@pytest.mark.parametrize("frame_count", [0, -1])
def test_expanded_timeline_progress_rejects_non_positive_frame_count(frame_count: int) -> None:
    with pytest.raises(PlaybackValidationError):
        expanded_timeline_progress(frame_count, 3)


def test_expanded_timeline_progress_rejects_non_positive_exposure() -> None:
    with pytest.raises(PlaybackValidationError):
        expanded_timeline_progress(3, 0)


@pytest.mark.parametrize("exposure", [0, -1, -5])
def test_expanded_timeline_indices_rejects_non_positive_exposure(exposure: int) -> None:
    with pytest.raises(PlaybackValidationError):
        expanded_timeline_indices(3, exposure)


# -- output_frame_count ----------------------------------------------------------


def test_output_frame_count_multiplies_frame_count_by_exposure() -> None:
    assert output_frame_count(4, 3) == 12


def test_output_frame_count_exposure_one() -> None:
    assert output_frame_count(10, 1) == 10


def test_output_frame_count_single_frame() -> None:
    assert output_frame_count(1, 7) == 7


def test_output_frame_count_rejects_non_positive_inputs() -> None:
    with pytest.raises(PlaybackValidationError):
        output_frame_count(0, 3)
    with pytest.raises(PlaybackValidationError):
        output_frame_count(3, 0)


# -- output_duration_seconds ------------------------------------------------------


def test_output_duration_seconds_matches_spec_worked_example() -> None:
    # 4 poses, fps=24, exposure=3 -> 12 frames -> 0.5s.
    assert output_duration_seconds(4, 3, 24) == pytest.approx(0.5)


def test_output_duration_seconds_exposure_one() -> None:
    assert output_duration_seconds(24, 1, 24) == pytest.approx(1.0)


def test_output_duration_seconds_single_frame() -> None:
    assert output_duration_seconds(1, 3, 24) == pytest.approx(3 / 24)


def test_output_duration_seconds_rejects_non_positive_fps() -> None:
    with pytest.raises(PlaybackValidationError):
        output_duration_seconds(4, 3, 0)
    with pytest.raises(PlaybackValidationError):
        output_duration_seconds(4, 3, -24)


def test_output_duration_seconds_rejects_non_positive_frame_count_or_exposure() -> None:
    with pytest.raises(PlaybackValidationError):
        output_duration_seconds(0, 3, 24)
    with pytest.raises(PlaybackValidationError):
        output_duration_seconds(4, 0, 24)


# -- validate_playback_settings ---------------------------------------------------


def test_validate_playback_settings_accepts_positive_values() -> None:
    validate_playback_settings(fps=24, exposure=3, scene_width=1920, scene_height=1080)  # must not raise


@pytest.mark.parametrize(
    ("fps", "exposure", "width", "height"),
    [
        (0, 3, 1920, 1080),
        (-24, 3, 1920, 1080),
        (24, 0, 1920, 1080),
        (24, -3, 1920, 1080),
        (24, 3, 0, 1080),
        (24, 3, -1920, 1080),
        (24, 3, 1920, 0),
        (24, 3, 1920, -1080),
    ],
)
def test_validate_playback_settings_rejects_non_positive_values(
    fps: int, exposure: int, width: int, height: int
) -> None:
    with pytest.raises(PlaybackValidationError):
        validate_playback_settings(fps=fps, exposure=exposure, scene_width=width, scene_height=height)
