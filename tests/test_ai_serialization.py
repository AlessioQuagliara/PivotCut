"""Tests for the AI Text-to-Animation pipeline: fake LLM JSON -> validated
``KeyframeState`` (``services.ai_animator``) -> an applied ``Frame``
(``domain.ai_keyframe``), and that applying it never corrupts the rig's
parent/child hierarchy or the camera.
"""

from __future__ import annotations

import pytest

from pivotcut.domain import commands
from pivotcut.domain.ai_keyframe import build_frame_from_keyframe
from pivotcut.domain.camera import Camera
from pivotcut.domain.layer import Layer
from pivotcut.domain.models import CameraState, Frame, KeyframeState, Project
from pivotcut.domain.rig import Bone, Rig
from pivotcut.services.ai_animator import AIAnimatorResponseError, keyframe_state_from_dict, keyframes_from_payload

# -- Fixtures ------------------------------------------------------------------------


def make_rig() -> Rig:
    """A 3-bone hierarchy: torso (root) -> arm -> hand."""
    torso = Bone(id="torso", name="Torso", parent_id=None, asset_id="asset-torso", x=100.0, y=100.0)
    arm = Bone(id="arm", name="Arm", parent_id="torso", asset_id="asset-arm", rotation=10.0)
    hand = Bone(id="hand", name="Hand", parent_id="arm", asset_id="asset-hand-open")
    return Rig(id="rig-1", name="Character", root_bone_id="torso", bones=[torso, arm, hand])


def make_frame() -> Frame:
    return Frame(
        id="frame-0",
        rigs=[make_rig()],
        layers=[Layer(id="layer-bg", name="Background", asset_id="asset-bg", x=0.0, y=0.0, z_depth=3.0)],
        camera=Camera(x=0.0, y=0.0, zoom=1.0),
    )


_FAKE_AI_RESPONSE = {
    "keyframes": [
        {
            "frame_index": 1,
            "bone_rotations": {"arm": 45.0},
            "bone_positions": {},
            "asset_swaps": {"hand": "asset-hand-fist"},
            "environment_transforms": {"layer-bg": {"x": -20.0}},
            "camera": {"x": 5.0, "y": 0.0, "zoom": 1.2},
        },
        {
            "frame_index": 0,
            "bone_rotations": {"arm": 20.0},
            "bone_positions": {"torso": {"x": 110.0, "y": 100.0}},
            "asset_swaps": {},
            "environment_transforms": {},
            "camera": None,
        },
    ]
}


# -- services.ai_animator: JSON -> KeyframeState --------------------------------------


def test_keyframes_from_payload_parses_every_field() -> None:
    keyframes = keyframes_from_payload(_FAKE_AI_RESPONSE)

    assert [kf.frame_index for kf in keyframes] == [0, 1]  # sorted by frame_index

    first, second = keyframes
    assert first.bone_rotations == {"arm": 20.0}
    assert first.bone_positions == {"torso": {"x": 110.0, "y": 100.0}}
    assert first.camera is None

    assert second.bone_rotations == {"arm": 45.0}
    assert second.asset_swaps == {"hand": "asset-hand-fist"}
    assert second.environment_transforms == {"layer-bg": {"x": -20.0}}
    assert second.camera == CameraState(x=5.0, y=0.0, zoom=1.2)


def test_keyframes_from_payload_accepts_bare_list() -> None:
    keyframes = keyframes_from_payload(_FAKE_AI_RESPONSE["keyframes"])
    assert len(keyframes) == 2


def test_keyframe_state_from_dict_applies_int_coercion_but_rejects_bool_frame_index() -> None:
    with pytest.raises(AIAnimatorResponseError):
        keyframe_state_from_dict({"frame_index": True})


@pytest.mark.parametrize(
    "broken_payload",
    [
        {},  # missing "keyframes"
        {"keyframes": "not-a-list"},
        {"keyframes": []},  # empty
        {"keyframes": [{"bone_rotations": {}}]},  # missing frame_index
        {"keyframes": [{"frame_index": 0, "bone_rotations": {"arm": "not-a-number"}}]},
        {"keyframes": [{"frame_index": 0, "bone_positions": {"torso": {"x": "nope", "y": 1}}}]},
        {"keyframes": [{"frame_index": 0, "camera": {"x": 0.0, "y": 0.0}}]},  # camera missing zoom
    ],
)
def test_keyframes_from_payload_rejects_malformed_payloads(broken_payload: object) -> None:
    with pytest.raises(AIAnimatorResponseError):
        keyframes_from_payload(broken_payload)


# -- domain.ai_keyframe: KeyframeState -> applied Frame --------------------------------


def test_build_frame_from_keyframe_applies_changes_without_corrupting_hierarchy() -> None:
    base_frame = make_frame()
    keyframe = KeyframeState(
        frame_index=0,
        bone_rotations={"arm": 45.0},
        bone_positions={"torso": {"x": 150.0, "y": 100.0}},
        asset_swaps={"hand": "asset-hand-fist"},
        environment_transforms={"layer-bg": {"x": -20.0, "z_depth": 2.0}},
        camera=CameraState(x=5.0, y=1.0, zoom=1.5),
    )

    new_frame = build_frame_from_keyframe(base_frame, keyframe, label="AI: wave")

    # A fresh frame, never the same object/id as the base.
    assert new_frame is not base_frame
    assert new_frame.id != base_frame.id
    assert new_frame.label == "AI: wave"

    new_rig = new_frame.rigs[0]
    bones_by_id = {bone.id: bone for bone in new_rig.bones}

    # Rotation applied to the targeted bone only.
    assert bones_by_id["arm"].rotation == 45.0
    assert bones_by_id["hand"].rotation == 0.0  # untouched

    # Position applied to the root bone.
    assert bones_by_id["torso"].x == 150.0
    assert bones_by_id["torso"].y == 100.0

    # Asset swap applied, and only to the targeted bone.
    assert bones_by_id["hand"].asset_id == "asset-hand-fist"
    assert bones_by_id["arm"].asset_id == "asset-arm"  # untouched

    # The parent/child hierarchy itself is completely untouched.
    assert new_rig.root_bone_id == "torso"
    assert bones_by_id["torso"].parent_id is None
    assert bones_by_id["arm"].parent_id == "torso"
    assert bones_by_id["hand"].parent_id == "arm"
    assert {bone.id for bone in new_rig.bones} == {"torso", "arm", "hand"}

    # Environment layer: only the given fields changed.
    new_layer = new_frame.layers[0]
    assert new_layer.x == -20.0
    assert new_layer.z_depth == 2.0
    assert new_layer.y == 0.0  # untouched

    # Camera fully replaced.
    assert new_frame.camera == Camera(x=5.0, y=1.0, zoom=1.5)

    # The original frame passed in is never mutated.
    original_rig = base_frame.rigs[0]
    original_bones_by_id = {bone.id: bone for bone in original_rig.bones}
    assert original_bones_by_id["arm"].rotation == 10.0
    assert original_bones_by_id["hand"].asset_id == "asset-hand-open"
    assert base_frame.camera == Camera(x=0.0, y=0.0, zoom=1.0)


def test_build_frame_from_keyframe_ignores_unknown_ids() -> None:
    """An AI response referencing a stale/nonexistent id must never crash."""
    base_frame = make_frame()
    keyframe = KeyframeState(
        frame_index=0,
        bone_rotations={"does-not-exist": 90.0},
        asset_swaps={"also-missing": "asset-x"},
        environment_transforms={"missing-layer": {"x": 1.0}},
    )

    new_frame = build_frame_from_keyframe(base_frame, keyframe)

    assert [bone.rotation for bone in new_frame.rigs[0].bones] == [0.0, 10.0, 0.0]


# -- End-to-end: fake AI payload -> Frames -> undoable InsertFramesCommand -----------


def test_full_pipeline_is_undoable_and_preserves_rig_structure() -> None:
    base_frame = make_frame()
    project = Project(frames=[base_frame], current_frame_index=0)

    keyframes = keyframes_from_payload(_FAKE_AI_RESPONSE)
    new_frames = [build_frame_from_keyframe(base_frame, kf, label=f"AI {kf.frame_index}") for kf in keyframes]

    history = commands.UndoRedoStack()
    history.execute(commands.InsertFramesCommand("AI Animate: wave", 1, new_frames), project)

    assert len(project.frames) == 3
    assert project.current_frame_index == 1
    assert [frame.rigs[0].root_bone_id for frame in project.frames] == ["torso", "torso", "torso"]

    history.undo(project)
    assert len(project.frames) == 1
    assert project.frames[0] is base_frame
    assert project.current_frame_index == 0

    history.redo(project)
    assert len(project.frames) == 3
    assert [frame.id for frame in project.frames[1:]] == [f.id for f in new_frames]
