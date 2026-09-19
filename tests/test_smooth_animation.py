"""Tests for Smooth Animation: the Bézier easing resolver, angle unwrapping,
``PlaybackEngine.get_interpolated_state``, and ``RigSerializer`` round-trips.
"""

from __future__ import annotations

import pytest

from pivotcut.domain import interpolation
from pivotcut.domain.ai_keyframe import resolve_keyframe_state
from pivotcut.domain.camera import Camera
from pivotcut.domain.layer import Layer
from pivotcut.domain.models import CameraState, Frame, InterpolationSettings, InterpolationType, KeyframeState
from pivotcut.domain.rig import Bone, Rig
from pivotcut.services.playback_engine import PlaybackEngine
from pivotcut.services.serialization import RigSerializer, SerializationError

LINEAR = InterpolationSettings(type=InterpolationType.LINEAR)
STEP = InterpolationSettings(type=InterpolationType.STEP)
# Point-symmetric about (0.5, 0.5): x1+x2=1 and y1+y2=1, so ease(0.5)==0.5
# exactly and ease(1-x) == 1-ease(x) for any x — this is also the actual
# CSS "ease-in-out" preset (cubic-bezier(0.42, 0, 0.58, 1)).
SYMMETRIC_EASE_IN_OUT = InterpolationSettings(type=InterpolationType.CUBIC_SPLINE, control_points=(0.42, 0.0, 0.58, 1.0))


# -- domain.interpolation: cubic Bézier resolver --------------------------------------


def test_bezier_resolver_midpoint_of_symmetric_curve_is_half() -> None:
    y = interpolation.ease_cubic_bezier(SYMMETRIC_EASE_IN_OUT.control_points, 0.5)
    assert y == pytest.approx(0.5, abs=1e-6)


def test_bezier_resolver_endpoints_are_exact() -> None:
    assert interpolation.ease_cubic_bezier(SYMMETRIC_EASE_IN_OUT.control_points, 0.0) == 0.0
    assert interpolation.ease_cubic_bezier(SYMMETRIC_EASE_IN_OUT.control_points, 1.0) == 1.0


def test_bezier_resolver_eases_in_then_out() -> None:
    """Ease-in-out: slower than linear during the first quarter (still
    accelerating), faster than linear during the last quarter (already
    decelerating back down) — the defining shape of this curve."""
    early = interpolation.ease_cubic_bezier(SYMMETRIC_EASE_IN_OUT.control_points, 0.25)
    late = interpolation.ease_cubic_bezier(SYMMETRIC_EASE_IN_OUT.control_points, 0.75)

    assert early < 0.25  # ease-in: lagging behind linear near the start
    assert late > 0.75  # ease-out: ahead of linear near the end
    assert late == pytest.approx(1.0 - early, abs=1e-6)  # point symmetry


def test_interpolate_value_applies_easing_type() -> None:
    assert interpolation.interpolate_value(0.0, 10.0, 0.5, LINEAR) == pytest.approx(5.0)
    assert interpolation.interpolate_value(0.0, 10.0, 0.99, STEP) == 0.0  # holds until fully there
    assert interpolation.interpolate_value(0.0, 10.0, 1.0, STEP) == 10.0


# -- domain.interpolation: angle unwrapping -------------------------------------------


def test_interpolate_angle_degrees_takes_the_shortest_path_across_zero() -> None:
    # 350deg -> 10deg is a 20deg forward move (350->360->10), never -340deg backward.
    halfway = interpolation.interpolate_angle_degrees(350.0, 10.0, 0.5, LINEAR)
    full = interpolation.interpolate_angle_degrees(350.0, 10.0, 1.0, LINEAR)

    assert halfway == pytest.approx(360.0)  # +10deg from 350, i.e. halfway through the +20deg move
    assert halfway % 360.0 == pytest.approx(0.0, abs=1e-9)
    assert full % 360.0 == pytest.approx(10.0)

    # The bug this guards against: naively lerp-ing the raw numbers would
    # rotate the *long* way around, backward through 180.
    naive_wrong_halfway = 350.0 + (10.0 - 350.0) * 0.5
    assert naive_wrong_halfway == pytest.approx(180.0)
    assert halfway != pytest.approx(naive_wrong_halfway)


def test_interpolate_angle_degrees_short_path_already_forward_is_unaffected() -> None:
    # A plain 10deg -> 40deg move has nothing to unwrap; behaves like interpolate_value.
    assert interpolation.interpolate_angle_degrees(10.0, 40.0, 0.5, LINEAR) == pytest.approx(25.0)


# -- services.playback_engine: PlaybackEngine.get_interpolated_state -----------------


def _make_keyframe(frame_index: int, **overrides: object) -> KeyframeState:
    return KeyframeState(frame_index=frame_index, **overrides)  # type: ignore[arg-type]


def test_get_interpolated_state_interpolates_rotation_position_and_camera() -> None:
    engine = PlaybackEngine()
    start = _make_keyframe(
        0,
        bone_rotations={"arm": 0.0},
        bone_positions={"torso": {"x": 100.0, "y": 100.0}},
        camera=CameraState(x=0.0, y=0.0, zoom=1.0),
        interpolation_map={
            interpolation.bone_rotation_property_id("arm"): LINEAR,
            interpolation.bone_axis_property_id("torso", "x"): LINEAR,
            interpolation.camera_property_id("zoom"): LINEAR,
        },
    )
    end = _make_keyframe(
        1,
        bone_rotations={"arm": 90.0},
        bone_positions={"torso": {"x": 200.0, "y": 100.0}},
        camera=CameraState(x=0.0, y=0.0, zoom=2.0),
    )

    mid = engine.get_interpolated_state(start, end, 0.5)

    assert mid.bone_rotations["arm"] == pytest.approx(45.0)
    assert mid.bone_positions["torso"]["x"] == pytest.approx(150.0)
    assert mid.bone_positions["torso"]["y"] == pytest.approx(100.0)
    assert mid.camera == CameraState(x=0.0, y=0.0, zoom=1.5)
    assert mid.frame_index == start.frame_index


def test_get_interpolated_state_never_mutates_its_inputs() -> None:
    engine = PlaybackEngine()
    start = _make_keyframe(0, bone_rotations={"arm": 0.0}, camera=CameraState())
    end = _make_keyframe(1, bone_rotations={"arm": 180.0}, camera=CameraState(zoom=2.0))
    start_copy_rotations = dict(start.bone_rotations)
    end_copy_rotations = dict(end.bone_rotations)

    engine.get_interpolated_state(start, end, 0.3)

    assert start.bone_rotations == start_copy_rotations
    assert end.bone_rotations == end_copy_rotations
    assert start.camera == CameraState()
    assert end.camera == CameraState(zoom=2.0)


def test_get_interpolated_state_asset_swap_switches_at_threshold() -> None:
    engine = PlaybackEngine(asset_swap_threshold=0.5)
    start = _make_keyframe(0, asset_swaps={"eyes": "asset-eyes-open"})
    end = _make_keyframe(1, asset_swaps={"eyes": "asset-eyes-closed"})

    just_before = engine.get_interpolated_state(start, end, 0.49)
    at_threshold = engine.get_interpolated_state(start, end, 0.5)

    assert just_before.asset_swaps == {"eyes": "asset-eyes-open"}
    assert at_threshold.asset_swaps == {"eyes": "asset-eyes-closed"}


def test_get_interpolated_state_passes_through_properties_on_only_one_side() -> None:
    engine = PlaybackEngine()
    start = _make_keyframe(0, bone_rotations={"arm": 15.0})
    end = _make_keyframe(1, bone_rotations={})  # "arm" not mentioned on the end side

    mid = engine.get_interpolated_state(start, end, 0.9)

    assert mid.bone_rotations["arm"] == 15.0  # held, not dropped or guessed at


def test_get_interpolated_state_respects_step_interpolation_type() -> None:
    engine = PlaybackEngine()
    start = _make_keyframe(
        0,
        bone_rotations={"head": 0.0},
        interpolation_map={interpolation.bone_rotation_property_id("head"): STEP},
    )
    end = _make_keyframe(1, bone_rotations={"head": 90.0})

    assert engine.get_interpolated_state(start, end, 0.99).bone_rotations["head"] == 0.0
    assert engine.get_interpolated_state(start, end, 1.0).bone_rotations["head"] == 90.0


# -- domain.ai_keyframe.resolve_keyframe_state + PlaybackEngine.get_interpolated_frame -
# (real-timeline integration: two actual Frames, not just two authored KeyframeStates)


def _make_real_rig(bone_id: str, rotation: float, x: float, y: float) -> Rig:
    bone = Bone(id=bone_id, name=bone_id, parent_id=None, asset_id="asset-1", x=x, y=y, rotation=rotation)
    return Rig(id="rig-1", name="Character", root_bone_id=bone_id, bones=[bone])


def test_resolve_keyframe_state_extracts_every_bone_layer_and_camera_value() -> None:
    frame = Frame(
        id="f0",
        rigs=[_make_real_rig("torso", rotation=15.0, x=100.0, y=200.0)],
        layers=[Layer(id="bg", name="bg", asset_id="asset-bg", x=0.0, y=0.0, z_depth=3.0)],
        camera=Camera(x=1.0, y=2.0, zoom=1.5),
    )

    state = resolve_keyframe_state(frame)

    assert state.bone_rotations == {"torso": 15.0}
    assert state.bone_positions == {"torso": {"x": 100.0, "y": 200.0}}
    assert state.asset_swaps == {"torso": "asset-1"}
    assert state.environment_transforms["bg"]["z_depth"] == 3.0
    assert state.camera == CameraState(x=1.0, y=2.0, zoom=1.5)


def test_get_interpolated_frame_eases_between_two_real_frames_preserving_hierarchy() -> None:
    start_frame = Frame(id="f0", rigs=[_make_real_rig("torso", rotation=0.0, x=100.0, y=100.0)])
    end_frame = Frame(id="f1", rigs=[_make_real_rig("torso", rotation=90.0, x=100.0, y=100.0)])

    engine = PlaybackEngine()
    mid_frame = engine.get_interpolated_frame(start_frame, end_frame, 0.5)

    assert mid_frame.id not in (start_frame.id, end_frame.id)  # a fresh, virtual frame
    mid_bone = mid_frame.rigs[0].bones[0]
    assert 0.0 < mid_bone.rotation < 90.0  # partway there, not a hard cut to either endpoint
    assert mid_frame.rigs[0].root_bone_id == "torso"

    # Neither real frame was touched.
    assert start_frame.rigs[0].bones[0].rotation == 0.0
    assert end_frame.rigs[0].bones[0].rotation == 90.0


def test_get_interpolated_frame_holds_steady_for_the_last_pose() -> None:
    """The last pose has no "next" pose — interpolating a frame with
    itself at any progress must return it unchanged (the export/playback
    integration's documented way of handling a timeline's tail)."""
    last_frame = Frame(id="f0", rigs=[_make_real_rig("torso", rotation=42.0, x=5.0, y=5.0)])

    result = PlaybackEngine().get_interpolated_frame(last_frame, last_frame, 0.7)

    assert result.rigs[0].bones[0].rotation == 42.0


# -- services.serialization: RigSerializer round-trip ----------------------------------


def test_rig_serializer_round_trip_preserves_control_points() -> None:
    original = KeyframeState(
        frame_index=3,
        bone_rotations={"arm": 45.0},
        bone_positions={"torso": {"x": 10.0, "y": 20.0}},
        asset_swaps={"eyes": "asset-eyes-closed"},
        environment_transforms={"bg_cloud": {"x": -5.0, "z_depth": 3.0}},
        camera=CameraState(x=1.0, y=2.0, zoom=1.5),
        interpolation_map={
            "bone:arm:rotation": InterpolationSettings(
                type=InterpolationType.CUBIC_SPLINE, control_points=(0.17, 0.67, 0.83, 0.67)
            ),
            "camera:zoom": STEP,
        },
    )

    round_tripped = RigSerializer.from_dict(RigSerializer.to_dict(original))

    assert round_tripped == original


def test_rig_serializer_legacy_dict_without_interpolation_map_falls_back_safely() -> None:
    legacy_dict = {
        "frame_index": 0,
        "bone_rotations": {"arm": 30.0},
        "camera": {"x": 0.0, "y": 0.0, "zoom": 1.0},
        # no "interpolation_map", "bone_positions", "asset_swaps", "environment_transforms"
    }

    state = RigSerializer.from_dict(legacy_dict)

    assert state.interpolation_map == {}
    assert state.bone_positions == {}
    assert state.asset_swaps == {}
    assert state.environment_transforms == {}


def test_rig_serializer_interpolation_entry_missing_type_falls_back_to_linear() -> None:
    data = {"frame_index": 0, "interpolation_map": {"bone:arm:rotation": {"control_points": [0.1, 0.2, 0.3, 0.4]}}}

    state = RigSerializer.from_dict(data)

    assert state.interpolation_map["bone:arm:rotation"].type is InterpolationType.LINEAR
    assert state.interpolation_map["bone:arm:rotation"].control_points == (0.1, 0.2, 0.3, 0.4)


@pytest.mark.parametrize(
    "broken_data",
    [
        {},  # missing frame_index
        {"frame_index": "not-an-int"},
        {"frame_index": 0, "camera": {"x": 0.0}},  # camera missing y/zoom
        {"frame_index": 0, "interpolation_map": {"x": {"type": "not-a-real-type"}}},
        {"frame_index": 0, "interpolation_map": {"x": {"control_points": [1, 2, 3]}}},  # only 3 values
    ],
)
def test_rig_serializer_rejects_malformed_data(broken_data: object) -> None:
    with pytest.raises(SerializationError):
        RigSerializer.from_dict(broken_data)


# -- services.playback_controller: Smooth Animation sub-progress (needs a QApplication) -


def _make_playback_project(exposure: int, smooth: bool):
    from pivotcut.domain.models import Project

    return Project(frames=[Frame(id="f0"), Frame(id="f1")], exposure=exposure, smooth_animation_enabled=smooth)


@pytest.mark.usefixtures("qapp")
def test_playback_controller_sub_progress_advances_each_tick_when_smooth_enabled() -> None:
    from pivotcut.services.playback_controller import PlaybackController

    project = _make_playback_project(exposure=4, smooth=True)
    controller = PlaybackController()
    controller.set_project(project)
    sub_frame_progress_seen: list[float] = []
    frame_changed_pose_seen: list[int] = []
    controller.sub_frame_changed.connect(lambda: sub_frame_progress_seen.append(controller.current_sub_progress))
    controller.frame_changed.connect(lambda: frame_changed_pose_seen.append(project.current_frame_index))

    controller.start()
    # Manually driving _on_tick (rather than waiting on the real QTimer/an
    # event loop) keeps this deterministic — see PlaybackController's own
    # docstring for exactly what one tick does.
    for _ in range(4):
        controller._on_tick()

    assert sub_frame_progress_seen == [0.25, 0.5, 0.75]  # 3 mid-exposure ticks...
    assert frame_changed_pose_seen == [1]  # ...then the 4th tick crosses into pose 1
    assert controller.current_sub_progress == 0.0  # reset once the pose boundary is crossed


@pytest.mark.usefixtures("qapp")
def test_playback_controller_never_emits_sub_frame_changed_when_smooth_disabled() -> None:
    from pivotcut.services.playback_controller import PlaybackController

    project = _make_playback_project(exposure=4, smooth=False)
    controller = PlaybackController()
    controller.set_project(project)
    sub_frame_events: list[object] = []
    controller.sub_frame_changed.connect(lambda: sub_frame_events.append(object()))

    controller.start()
    for _ in range(4):
        controller._on_tick()

    assert sub_frame_events == []  # exactly the pre-existing, unaffected behavior


@pytest.mark.usefixtures("qapp")
def test_playback_controller_stop_resets_sub_progress() -> None:
    from pivotcut.services.playback_controller import PlaybackController

    project = _make_playback_project(exposure=4, smooth=True)
    controller = PlaybackController()
    controller.set_project(project)
    controller.start()
    controller._on_tick()
    assert controller.current_sub_progress > 0.0

    controller.stop()

    assert controller.current_sub_progress == 0.0
