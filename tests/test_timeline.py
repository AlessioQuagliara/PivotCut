from __future__ import annotations

import pytest

from pivotcut.domain.camera import Camera
from pivotcut.domain.layer import Layer
from pivotcut.domain.models import Frame, Project, new_project
from pivotcut.domain.rig import Bone, Rig
from pivotcut.domain.timeline import (
    CannotDeleteLastFrameError,
    delete_current_frame,
    move_current_frame_left,
    move_current_frame_right,
    move_frame,
    new_frame_after_current,
    select_frame,
    select_next_frame,
    select_previous_frame,
)


def make_project(labels: list[str]) -> Project:
    frames = [Frame(id=f"f{i}", duration=1, label=label) for i, label in enumerate(labels)]
    return Project(frames=frames, current_frame_index=0)


def test_new_project_has_single_frame() -> None:
    project = new_project()
    assert len(project.frames) == 1
    assert project.current_frame_index == 0


def test_new_frame_inserts_immediately_after_current() -> None:
    project = make_project(["A", "B", "C"])
    select_frame(project, 0)

    new_frame = new_frame_after_current(project)

    assert [f.label for f in project.frames] == ["A", "A (copy)", "B", "C"]
    assert project.frames[1] is new_frame
    assert project.current_frame_index == 1


def test_new_frame_gets_fresh_uuid_and_duplicates_duration() -> None:
    project = make_project(["A"])
    project.frames[0].duration = 5

    new_frame = new_frame_after_current(project)

    assert new_frame.id != project.frames[0].id
    assert new_frame.duration == 5


def test_new_frame_label_suffix_increments_instead_of_stacking() -> None:
    project = make_project(["Walk"])

    new_frame_after_current(project)  # -> "Walk (copy)"
    select_frame(project, 1)
    new_frame_after_current(project)  # -> "Walk (copy 2)"

    assert [f.label for f in project.frames] == ["Walk", "Walk (copy)", "Walk (copy 2)"]


def test_new_frame_label_suffix_skipped_when_source_label_empty() -> None:
    project = make_project([""])

    new_frame = new_frame_after_current(project)

    assert new_frame.label == ""


def test_delete_current_frame_removes_selected_frame() -> None:
    project = make_project(["A", "B", "C"])
    select_frame(project, 1)

    removed = delete_current_frame(project)

    assert removed.label == "B"
    assert [f.label for f in project.frames] == ["A", "C"]


def test_delete_current_frame_selects_previous_frame() -> None:
    project = make_project(["A", "B", "C"])
    select_frame(project, 2)

    delete_current_frame(project)

    assert project.current_frame_index == 1
    assert project.frames[project.current_frame_index].label == "B"


def test_delete_current_frame_falls_back_to_first_when_deleting_first() -> None:
    project = make_project(["A", "B", "C"])
    select_frame(project, 0)

    delete_current_frame(project)

    assert project.current_frame_index == 0
    assert project.frames[project.current_frame_index].label == "B"


def test_cannot_delete_last_remaining_frame() -> None:
    project = make_project(["Only"])

    with pytest.raises(CannotDeleteLastFrameError):
        delete_current_frame(project)

    assert len(project.frames) == 1


def test_move_current_frame_left_swaps_and_follows_selection() -> None:
    project = make_project(["A", "B", "C"])
    select_frame(project, 1)

    move_current_frame_left(project)

    assert [f.label for f in project.frames] == ["B", "A", "C"]
    assert project.current_frame_index == 0


def test_move_current_frame_left_noop_at_start() -> None:
    project = make_project(["A", "B"])
    select_frame(project, 0)

    move_current_frame_left(project)

    assert [f.label for f in project.frames] == ["A", "B"]
    assert project.current_frame_index == 0


def test_move_current_frame_right_swaps_and_follows_selection() -> None:
    project = make_project(["A", "B", "C"])
    select_frame(project, 1)

    move_current_frame_right(project)

    assert [f.label for f in project.frames] == ["A", "C", "B"]
    assert project.current_frame_index == 2


def test_move_current_frame_right_noop_at_end() -> None:
    project = make_project(["A", "B"])
    select_frame(project, 1)

    move_current_frame_right(project)

    assert [f.label for f in project.frames] == ["A", "B"]
    assert project.current_frame_index == 1


def test_select_frame_clamps_out_of_range_indices() -> None:
    project = make_project(["A", "B", "C"])

    select_frame(project, 99)
    assert project.current_frame_index == 2

    select_frame(project, -99)
    assert project.current_frame_index == 0


def test_select_previous_and_next_frame_clamp_at_edges() -> None:
    project = make_project(["A", "B", "C"])
    select_frame(project, 0)

    select_previous_frame(project)
    assert project.current_frame_index == 0

    select_frame(project, 2)
    select_next_frame(project)
    assert project.current_frame_index == 2


# -- New Frame must deep copy rigs, preserving rig/bone ids -------------------


def make_bone(id: str, x: float = 0.0, y: float = 0.0) -> Bone:
    return Bone(id=id, name=id, parent_id=None, asset_id="asset-1", x=x, y=y)


def make_project_with_rig() -> Project:
    bone = make_bone("bone-1", x=10.0, y=20.0)
    rig = Rig(id="rig-1", name="Character", root_bone_id="bone-1", bones=[bone])
    frame = Frame(id="f0", duration=1, label="A", rigs=[rig])
    return Project(frames=[frame], current_frame_index=0)


def test_new_frame_deep_copies_rigs() -> None:
    project = make_project_with_rig()

    new_frame = new_frame_after_current(project)

    assert new_frame.rigs is not project.frames[0].rigs
    assert new_frame.rigs[0] is not project.frames[0].rigs[0]
    assert new_frame.rigs[0].bones[0] is not project.frames[0].rigs[0].bones[0]


def test_new_frame_keeps_rig_and_bone_ids_identical() -> None:
    project = make_project_with_rig()

    new_frame = new_frame_after_current(project)

    assert new_frame.rigs[0].id == "rig-1"
    assert new_frame.rigs[0].bones[0].id == "bone-1"


def test_editing_bone_in_new_frame_does_not_affect_previous_frame() -> None:
    project = make_project_with_rig()
    original_frame = project.frames[0]

    new_frame_after_current(project)
    duplicated_frame = project.frames[1]
    duplicated_frame.rigs[0].bones[0].x = 999.0
    duplicated_frame.rigs[0].bones[0].rotation = 45.0

    assert original_frame.rigs[0].bones[0].x == 10.0
    assert original_frame.rigs[0].bones[0].rotation == 0.0


# -- New Frame must deep copy layers and camera, preserving layer ids ---------


def make_layer(id: str, x: float = 0.0, y: float = 0.0) -> Layer:
    return Layer(id=id, name=id, asset_id="asset-1", x=x, y=y)


def make_project_with_layer_and_camera() -> Project:
    layer = make_layer("layer-1", x=100.0, y=200.0)
    camera = Camera(x=10.0, y=20.0, zoom=1.5)
    frame = Frame(id="f0", duration=1, label="A", layers=[layer], camera=camera)
    return Project(frames=[frame], current_frame_index=0)


def test_new_frame_deep_copies_layers_and_camera() -> None:
    project = make_project_with_layer_and_camera()

    new_frame = new_frame_after_current(project)

    assert new_frame.layers is not project.frames[0].layers
    assert new_frame.layers[0] is not project.frames[0].layers[0]
    assert new_frame.camera is not project.frames[0].camera


def test_new_frame_keeps_layer_ids_identical() -> None:
    project = make_project_with_layer_and_camera()

    new_frame = new_frame_after_current(project)

    assert new_frame.layers[0].id == "layer-1"


def test_editing_layer_in_new_frame_does_not_affect_previous_frame() -> None:
    project = make_project_with_layer_and_camera()
    original_frame = project.frames[0]

    new_frame_after_current(project)
    duplicated_frame = project.frames[1]
    duplicated_frame.layers[0].x = 999.0
    duplicated_frame.layers[0].opacity = 0.2

    assert original_frame.layers[0].x == 100.0
    assert original_frame.layers[0].opacity == 1.0


def test_editing_camera_in_new_frame_does_not_affect_previous_frame() -> None:
    project = make_project_with_layer_and_camera()
    original_frame = project.frames[0]

    new_frame_after_current(project)
    duplicated_frame = project.frames[1]
    duplicated_frame.camera.x = 999.0
    duplicated_frame.camera.zoom = 3.0

    assert original_frame.camera.x == 10.0
    assert original_frame.camera.zoom == 1.5


# -- move_frame: generalized reorder backing Move Left/Right and drag-and-drop ------


def test_move_frame_left_by_one_matches_swap_semantics() -> None:
    project = make_project(["A", "B", "C"])

    move_frame(project, 1, 0)

    assert [f.label for f in project.frames] == ["B", "A", "C"]
    assert project.current_frame_index == 0


def test_move_frame_right_by_one_matches_swap_semantics() -> None:
    project = make_project(["A", "B", "C"])

    move_frame(project, 1, 2)

    assert [f.label for f in project.frames] == ["A", "C", "B"]
    assert project.current_frame_index == 2


def test_move_frame_to_first_position() -> None:
    project = make_project(["A", "B", "C", "D"])

    move_frame(project, 3, 0)

    assert [f.label for f in project.frames] == ["D", "A", "B", "C"]
    assert project.current_frame_index == 0


def test_move_frame_to_last_position() -> None:
    project = make_project(["A", "B", "C", "D"])

    move_frame(project, 0, 3)

    assert [f.label for f in project.frames] == ["B", "C", "D", "A"]
    assert project.current_frame_index == 3


def test_move_frame_to_same_position_is_a_true_no_op() -> None:
    project = make_project(["A", "B", "C"])
    project.current_frame_index = 2  # deliberately different from the moved frame

    move_frame(project, 1, 1)

    assert [f.label for f in project.frames] == ["A", "B", "C"]
    assert project.current_frame_index == 2  # untouched, not reset to 1


def test_move_frame_out_of_range_to_index_clamps() -> None:
    project = make_project(["A", "B", "C"])

    move_frame(project, 0, 99)

    assert [f.label for f in project.frames] == ["B", "C", "A"]
    assert project.current_frame_index == 2


def test_move_frame_negative_to_index_clamps_to_zero() -> None:
    project = make_project(["A", "B", "C"])

    move_frame(project, 2, -5)

    assert [f.label for f in project.frames] == ["C", "A", "B"]
    assert project.current_frame_index == 0


def test_move_frame_single_frame_is_a_no_op() -> None:
    project = make_project(["Only"])

    move_frame(project, 0, 0)

    assert [f.label for f in project.frames] == ["Only"]
    assert project.current_frame_index == 0


def test_move_frame_out_of_range_from_index_is_a_no_op() -> None:
    project = make_project(["A", "B"])

    move_frame(project, 99, 0)

    assert [f.label for f in project.frames] == ["A", "B"]


def test_move_frame_preserves_frame_identity_and_full_content() -> None:
    project = make_project_with_rig()
    frame_to_move = project.frames[0]
    original_bone_id = frame_to_move.rigs[0].bones[0].id
    project.frames.append(Frame(id="f-extra", duration=1, label="Extra"))

    move_frame(project, 0, 1)

    assert project.frames[1] is frame_to_move
    assert project.frames[1].rigs[0].bones[0].id == original_bone_id
