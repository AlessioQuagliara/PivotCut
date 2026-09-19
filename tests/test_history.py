from __future__ import annotations

import pytest

from pivotcut.domain import commands, timeline
from pivotcut.domain.assets import Asset
from pivotcut.domain.camera import Camera
from pivotcut.domain.layer import Layer
from pivotcut.domain.models import Frame, Project, SceneSettings, new_project
from pivotcut.domain.rig import Bone, Rig, RigTemplate, find_bone_in_list, rig_template_from_rig

# -- Test fixtures ---------------------------------------------------------------


def make_project(frame_count: int = 1) -> Project:
    frames = [Frame(id=f"f{i}", duration=1, label=f"Frame {i}") for i in range(frame_count)]
    return Project(frames=frames, current_frame_index=0)


def make_bone(id: str, x: float = 0.0, y: float = 0.0, rotation: float = 0.0) -> Bone:
    return Bone(id=id, name=id, parent_id=None, asset_id="asset-1", x=x, y=y, rotation=rotation)


def make_layer(id: str, x: float = 0.0, y: float = 0.0) -> Layer:
    return Layer(id=id, name=id, asset_id="asset-1", x=x, y=y)


def make_asset(id: str = "asset-1") -> Asset:
    return Asset(id=id, name="part", source_path="/tmp/part.png", relative_path=None, width=64, height=64)


# -- UndoRedoStack basics ----------------------------------------------------------


def test_execute_applies_command_immediately() -> None:
    project = make_project()
    history = commands.UndoRedoStack()

    history.execute(commands.NewFrameCommand(), project)

    assert len(project.frames) == 2


def test_undo_reverts_and_redo_reapplies() -> None:
    project = make_project()
    history = commands.UndoRedoStack()
    history.execute(commands.NewFrameCommand(), project)

    history.undo(project)
    assert len(project.frames) == 1

    history.redo(project)
    assert len(project.frames) == 2


def test_can_undo_can_redo_reflect_stack_state() -> None:
    project = make_project()
    history = commands.UndoRedoStack()
    assert not history.can_undo
    assert not history.can_redo

    history.execute(commands.NewFrameCommand(), project)
    assert history.can_undo
    assert not history.can_redo

    history.undo(project)
    assert not history.can_undo
    assert history.can_redo


def test_new_command_after_undo_clears_redo_branch() -> None:
    project = make_project()
    history = commands.UndoRedoStack()
    history.execute(commands.NewFrameCommand(), project)
    history.undo(project)
    assert history.can_redo

    history.execute(commands.NewFrameCommand(), project)

    assert not history.can_redo


def test_clear_empties_both_stacks() -> None:
    project = make_project()
    history = commands.UndoRedoStack()
    history.execute(commands.NewFrameCommand(), project)
    history.undo(project)

    history.clear()

    assert not history.can_undo
    assert not history.can_redo


def test_undo_on_empty_stack_returns_none_and_does_not_raise() -> None:
    project = make_project()
    history = commands.UndoRedoStack()
    assert history.undo(project) is None


def test_redo_on_empty_stack_returns_none_and_does_not_raise() -> None:
    project = make_project()
    history = commands.UndoRedoStack()
    assert history.redo(project) is None


def test_max_history_size_evicts_oldest_command() -> None:
    project = new_project()
    history = commands.UndoRedoStack(max_size=5)

    for _ in range(8):
        history.execute(commands.NewFrameCommand(), project)

    # Only the most recent 5 commands are undoable; undoing all of them
    # must not raise even though 8 were executed.
    undone = 0
    while history.can_undo:
        history.undo(project)
        undone += 1
    assert undone == 5


def test_default_max_history_is_100() -> None:
    assert commands.DEFAULT_MAX_HISTORY == 100


def test_one_command_produces_exactly_one_history_entry() -> None:
    """A whole drag/edit gesture is one command, not one per mouse-move/tick."""
    project = make_project()
    project.frames[0].rigs = [Rig(id="rig1", name="R", root_bone_id="b1", bones=[make_bone("b1")])]
    history = commands.UndoRedoStack()

    before = commands.snapshot_bone(project.frames[0].rigs[0].bones[0])
    project.frames[0].rigs[0].bones[0].x = 999.0
    after = commands.snapshot_bone(project.frames[0].rigs[0].bones[0])
    history.execute(
        commands.TransformBoneCommand("Move Bone", "f0", "rig1", "b1", before, after), project
    )

    assert len(history._undo_stack) == 1


# -- NewFrameCommand ----------------------------------------------------------------


def test_new_frame_command_undo_removes_created_frame() -> None:
    project = make_project()
    history = commands.UndoRedoStack()
    history.execute(commands.NewFrameCommand(), project)
    created_id = project.frames[1].id

    history.undo(project)

    assert [f.id for f in project.frames] == ["f0"]
    assert created_id != "f0"


def test_new_frame_command_redo_reinserts_same_frame_id() -> None:
    project = make_project()
    history = commands.UndoRedoStack()
    history.execute(commands.NewFrameCommand(), project)
    created_id = project.frames[1].id

    history.undo(project)
    history.redo(project)

    assert project.frames[1].id == created_id


def test_new_frame_command_undo_restores_previous_selection() -> None:
    project = make_project(frame_count=3)
    timeline.select_frame(project, 1)
    history = commands.UndoRedoStack()
    history.execute(commands.NewFrameCommand(), project)
    assert project.current_frame_index == 2

    history.undo(project)

    assert project.current_frame_index == 1


# -- DeleteFrameCommand ---------------------------------------------------------------


def test_delete_frame_command_undo_restores_frame_and_selection() -> None:
    project = make_project(frame_count=3)
    timeline.select_frame(project, 1)
    removed_id = project.frames[1].id
    history = commands.UndoRedoStack()

    history.execute(commands.DeleteFrameCommand(), project)
    assert [f.id for f in project.frames] == ["f0", "f2"]

    history.undo(project)

    assert [f.id for f in project.frames] == ["f0", removed_id, "f2"]
    assert project.current_frame_index == 1


def test_delete_frame_command_redo_removes_again() -> None:
    project = make_project(frame_count=2)
    history = commands.UndoRedoStack()
    history.execute(commands.DeleteFrameCommand(), project)
    history.undo(project)

    history.redo(project)

    assert len(project.frames) == 1


def test_delete_frame_command_last_frame_raises_and_is_not_recorded() -> None:
    project = make_project(frame_count=1)
    history = commands.UndoRedoStack()

    with pytest.raises(timeline.CannotDeleteLastFrameError):
        history.execute(commands.DeleteFrameCommand(), project)

    assert not history.can_undo
    assert len(project.frames) == 1


# -- MoveFrameCommand (Move Left/Right + drag-and-drop reorder) ------------------------


def test_move_frame_command_undo_restores_order() -> None:
    project = make_project(frame_count=3)
    history = commands.UndoRedoStack()

    history.execute(commands.MoveFrameCommand("Reorder Frame", 0, 2), project)
    assert [f.id for f in project.frames] == ["f1", "f2", "f0"]

    history.undo(project)

    assert [f.id for f in project.frames] == ["f0", "f1", "f2"]


def test_move_frame_command_redo_reapplies_move() -> None:
    project = make_project(frame_count=3)
    history = commands.UndoRedoStack()
    history.execute(commands.MoveFrameCommand("Reorder Frame", 0, 2), project)
    history.undo(project)

    history.redo(project)

    assert [f.id for f in project.frames] == ["f1", "f2", "f0"]


def test_move_frame_command_undo_restores_selection_of_non_dragged_frame() -> None:
    project = make_project(frame_count=3)
    timeline.select_frame(project, 2)  # select f2, then drag f0 elsewhere
    history = commands.UndoRedoStack()

    history.execute(commands.MoveFrameCommand("Reorder Frame", 0, 2), project)
    history.undo(project)

    assert project.current_frame_index == 2
    assert project.frames[project.current_frame_index].id == "f2"


# -- CreateRigCommand / AddLayerCommand ------------------------------------------------


def test_create_rig_command_undo_removes_rig() -> None:
    project = make_project()
    rig = Rig(id="rig1", name="R", root_bone_id="b1", bones=[make_bone("b1")])
    history = commands.UndoRedoStack()

    history.execute(commands.CreateRigCommand("Create Rig", "f0", rig), project)
    assert len(project.frames[0].rigs) == 1

    history.undo(project)

    assert project.frames[0].rigs == []


def test_create_rig_command_redo_reinserts_same_rig() -> None:
    project = make_project()
    rig = Rig(id="rig1", name="R", root_bone_id="b1", bones=[make_bone("b1")])
    history = commands.UndoRedoStack()
    history.execute(commands.CreateRigCommand("Create Rig", "f0", rig), project)
    history.undo(project)

    history.redo(project)

    assert project.frames[0].rigs[0].id == "rig1"


def test_delete_rig_command_undo_redo() -> None:
    """Mirror of CreateRigCommand — used by "delete the selected root Part"."""
    project = make_project()
    rig = Rig(id="rig1", name="R", root_bone_id="b1", bones=[make_bone("b1", x=42.0)])
    project.frames[0].rigs = [rig]
    history = commands.UndoRedoStack()

    history.execute(commands.DeleteRigCommand("Delete Character", "f0", rig), project)
    assert project.frames[0].rigs == []

    history.undo(project)
    assert project.frames[0].rigs[0].id == "rig1"
    assert project.frames[0].rigs[0].bones[0].x == 42.0

    history.redo(project)
    assert project.frames[0].rigs == []


def test_add_layer_command_undo_redo() -> None:
    project = make_project()
    layer = make_layer("layer1")
    history = commands.UndoRedoStack()

    history.execute(commands.AddLayerCommand("Add Layer", "f0", layer), project)
    assert len(project.frames[0].layers) == 1

    history.undo(project)
    assert project.frames[0].layers == []

    history.redo(project)
    assert project.frames[0].layers[0].id == "layer1"


# -- EditRigStructureCommand / SaveRigTemplateCommand / DeleteRigTemplateCommand ------


def make_rig_bones() -> list[Bone]:
    return [
        make_bone("root", x=10.0, y=10.0),
        Bone(id="child", name="child", parent_id="root", asset_id="asset-1"),
    ]


def test_edit_rig_structure_command_undo_redo() -> None:
    project = make_project()
    rig = Rig(id="rig1", name="R", root_bone_id="root", bones=make_rig_bones())
    project.frames[0].rigs = [rig]
    history = commands.UndoRedoStack()

    before_bones = commands.snapshot_bones(rig.bones)
    before_root = rig.root_bone_id
    after_bones = commands.snapshot_bones(rig.bones)
    after_bones.append(Bone(id="grandchild", name="grandchild", parent_id="child", asset_id="asset-1"))

    command = commands.EditRigStructureCommand(
        "Edit Character Rig", "f0", "rig1", before_bones, before_root, after_bones, before_root
    )
    history.execute(command, project)

    assert [b.id for b in project.frames[0].rigs[0].bones] == ["root", "child", "grandchild"]

    history.undo(project)
    assert [b.id for b in project.frames[0].rigs[0].bones] == ["root", "child"]

    history.redo(project)
    assert [b.id for b in project.frames[0].rigs[0].bones] == ["root", "child", "grandchild"]


def test_edit_rig_structure_command_does_not_touch_other_frames_rigs() -> None:
    """Structural edits apply to a single rig instance only — never propagated."""
    project = make_project(frame_count=2)
    rig_f0 = Rig(id="rig1", name="R", root_bone_id="root", bones=make_rig_bones())
    rig_f1 = Rig(id="rig1", name="R", root_bone_id="root", bones=make_rig_bones())
    project.frames[0].rigs = [rig_f0]
    project.frames[1].rigs = [rig_f1]
    history = commands.UndoRedoStack()

    before_bones = commands.snapshot_bones(rig_f0.bones)
    after_bones = commands.snapshot_bones(rig_f0.bones)
    after_bones[1].x = 999.0

    command = commands.EditRigStructureCommand(
        "Edit Character Rig", "f0", "rig1", before_bones, "root", after_bones, "root"
    )
    history.execute(command, project)

    assert project.frames[0].rigs[0].bones[1].x == 999.0
    assert project.frames[1].rigs[0].bones[1].x == 0.0


def test_save_rig_template_command_create_undo_redo() -> None:
    project = make_project()
    template = RigTemplate(id="tmpl1", name="Hero", root_bone_id="root", bones=make_rig_bones())
    history = commands.UndoRedoStack()

    history.execute(commands.SaveRigTemplateCommand("Save Template: Hero", template), project)
    assert [t.id for t in project.rig_templates] == ["tmpl1"]

    history.undo(project)
    assert project.rig_templates == []

    history.redo(project)
    assert [t.id for t in project.rig_templates] == ["tmpl1"]


def test_save_rig_template_command_update_existing_undo_restores_previous_content() -> None:
    project = make_project()
    original = RigTemplate(id="tmpl1", name="Hero v1", root_bone_id="root", bones=make_rig_bones())
    project.rig_templates = [original]
    updated = RigTemplate(id="tmpl1", name="Hero v2", root_bone_id="root", bones=make_rig_bones())
    history = commands.UndoRedoStack()

    history.execute(commands.SaveRigTemplateCommand("Save Template: Hero v2", updated), project)
    assert project.rig_templates[0].name == "Hero v2"

    history.undo(project)
    assert project.rig_templates[0].name == "Hero v1"

    history.redo(project)
    assert project.rig_templates[0].name == "Hero v2"


def test_delete_rig_template_command_undo_redo() -> None:
    project = make_project()
    template = RigTemplate(id="tmpl1", name="Hero", root_bone_id="root", bones=make_rig_bones())
    project.rig_templates = [template]
    history = commands.UndoRedoStack()

    history.execute(commands.DeleteRigTemplateCommand("Delete Template: Hero", "tmpl1"), project)
    assert project.rig_templates == []

    history.undo(project)
    assert [t.id for t in project.rig_templates] == ["tmpl1"]

    history.redo(project)
    assert project.rig_templates == []


def test_save_builder_style_edit_produces_exactly_one_history_entry() -> None:
    """A whole Rig Builder session (many local add/remove/reparent edits) must
    collapse into a single 'Save Builder' history entry, not one per edit."""
    project = make_project()
    rig = Rig(id="rig1", name="R", root_bone_id="root", bones=make_rig_bones())
    project.frames[0].rigs = [rig]
    history = commands.UndoRedoStack()

    before_bones = commands.snapshot_bones(rig.bones)
    before_root = rig.root_bone_id
    # Simulate several structural edits happening on the builder's local copy
    # (never touching history until the single final Save).
    working_bones = commands.snapshot_bones(rig.bones)
    working_bones.append(Bone(id="c2", name="c2", parent_id="root", asset_id="asset-1"))
    working_bones.append(Bone(id="c3", name="c3", parent_id="c2", asset_id="asset-1"))
    working_bones = [b for b in working_bones if b.id != "child"]

    command = commands.EditRigStructureCommand(
        "Edit Character Rig", "f0", "rig1", before_bones, before_root, working_bones, before_root
    )
    history.execute(command, project)

    assert len(history._undo_stack) == 1


# -- TransformBoneCommand (move + rotate) ----------------------------------------------


def test_transform_bone_command_move_undo_redo() -> None:
    project = make_project()
    bone = make_bone("b1", x=10.0, y=20.0)
    project.frames[0].rigs = [Rig(id="rig1", name="R", root_bone_id="b1", bones=[bone])]
    before = commands.snapshot_bone(bone)
    bone.x, bone.y = 100.0, 200.0
    after = commands.snapshot_bone(bone)
    history = commands.UndoRedoStack()

    history.execute(commands.TransformBoneCommand("Move Bone", "f0", "rig1", "b1", before, after), project)
    assert (bone.x, bone.y) == (100.0, 200.0)

    history.undo(project)
    assert (bone.x, bone.y) == (10.0, 20.0)

    history.redo(project)
    assert (bone.x, bone.y) == (100.0, 200.0)


def test_transform_bone_command_rotate_undo_redo() -> None:
    project = make_project()
    bone = make_bone("b1", rotation=0.0)
    project.frames[0].rigs = [Rig(id="rig1", name="R", root_bone_id="b1", bones=[bone])]
    before = commands.snapshot_bone(bone)
    bone.rotation = 45.0
    after = commands.snapshot_bone(bone)
    history = commands.UndoRedoStack()

    history.execute(commands.TransformBoneCommand("Rotate Bone", "f0", "rig1", "b1", before, after), project)
    history.undo(project)

    assert bone.rotation == 0.0


# -- EditLayerCommand -----------------------------------------------------------------


def test_edit_layer_command_undo_redo() -> None:
    project = make_project()
    layer = make_layer("layer1", x=0.0, y=0.0)
    project.frames[0].layers = [layer]
    before = commands.snapshot_layer(layer)
    layer.x, layer.opacity = 500.0, 0.3
    after = commands.snapshot_layer(layer)
    history = commands.UndoRedoStack()

    history.execute(commands.EditLayerCommand("Edit Layer", "f0", "layer1", before, after), project)
    assert (layer.x, layer.opacity) == (500.0, 0.3)

    history.undo(project)
    assert (layer.x, layer.opacity) == (0.0, 1.0)

    history.redo(project)
    assert (layer.x, layer.opacity) == (500.0, 0.3)


# -- EditCameraCommand ----------------------------------------------------------------


def test_edit_camera_command_undo_redo() -> None:
    project = make_project()
    before = commands.snapshot_camera(project.frames[0].camera)
    project.frames[0].camera.x = 120.0
    project.frames[0].camera.zoom = 2.0
    after = commands.snapshot_camera(project.frames[0].camera)
    history = commands.UndoRedoStack()

    history.execute(commands.EditCameraCommand("Edit Camera", "f0", before, after), project)
    history.undo(project)

    assert project.frames[0].camera.x == 0.0
    assert project.frames[0].camera.zoom == 1.0

    history.redo(project)
    assert project.frames[0].camera.x == 120.0
    assert project.frames[0].camera.zoom == 2.0


def test_reset_camera_is_a_regular_edit_camera_command() -> None:
    project = make_project()
    project.frames[0].camera = Camera(x=50.0, y=60.0, zoom=1.5)
    before = commands.snapshot_camera(project.frames[0].camera)
    project.frames[0].camera.x = 0.0
    project.frames[0].camera.y = 0.0
    project.frames[0].camera.zoom = 1.0
    after = commands.snapshot_camera(project.frames[0].camera)
    history = commands.UndoRedoStack()

    history.execute(commands.EditCameraCommand("Reset Camera", "f0", before, after), project)
    history.undo(project)

    assert (project.frames[0].camera.x, project.frames[0].camera.y, project.frames[0].camera.zoom) == (50.0, 60.0, 1.5)


# -- MainWindow dirty-state integration (requires Qt) ----------------------------------


@pytest.mark.usefixtures("qapp")
class TestDirtyStateAndMainWindow:
    def test_new_project_starts_clean(self) -> None:
        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        assert win._is_dirty is False
        assert "*" not in win.windowTitle()

    def test_executing_a_command_marks_dirty(self) -> None:
        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._timeline.new_frame()

        assert win._is_dirty is True
        assert win.windowTitle().endswith("*")

    def test_save_clears_dirty_flag(self, tmp_path) -> None:
        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._timeline.new_frame()
        assert win._is_dirty is True

        win._save_to(tmp_path / "proj.pivotcut.json")

        assert win._is_dirty is False
        assert "*" not in win.windowTitle()

    def test_open_project_resets_dirty_and_history(self, tmp_path) -> None:
        from pivotcut.app.main_window import MainWindow
        from pivotcut.services import project_io

        win = MainWindow()
        win._timeline.new_frame()
        path = tmp_path / "proj.pivotcut.json"
        win._save_to(path)
        win._timeline.new_frame()  # dirty again, with undo history present
        assert win._is_dirty is True
        assert win._history.can_undo is True

        win._project = project_io.load_project(path)
        win._current_path = path
        win._rebind_project()

        assert win._is_dirty is False
        assert win._history.can_undo is False

    def test_undo_redo_disabled_while_exporting(self) -> None:
        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._timeline.new_frame()
        frames_before = len(win._project.frames)

        win._is_exporting = True
        win._timeline.new_frame()  # must be silently ignored

        assert len(win._project.frames) == frames_before
        win._is_exporting = False

    # -- Character Rig Builder integration (Milestone 6A) -----------------------

    def test_cancel_rig_builder_does_not_change_project(self, monkeypatch) -> None:
        from PySide6.QtWidgets import QDialog

        import pivotcut.app.main_window as main_window_module
        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        rig = Rig(id="rig1", name="R", root_bone_id="root", bones=[make_bone("root")])
        win._current_frame().rigs = [rig]
        bones_before = commands.snapshot_bones(rig.bones)
        history_len_before = len(win._history._undo_stack)

        class _CancelledDialog:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def exec(self) -> int:
                return QDialog.DialogCode.Rejected

        monkeypatch.setattr(main_window_module, "RigBuilderDialog", _CancelledDialog)

        win._on_edit_selected_rig()

        assert win._current_frame().rigs[0].bones == bones_before
        assert win._is_dirty is False
        assert len(win._history._undo_stack) == history_len_before

    def test_save_rig_builder_invalidates_current_frame_thumbnail(self, monkeypatch) -> None:
        from PySide6.QtWidgets import QDialog

        import pivotcut.app.main_window as main_window_module
        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        frame = win._current_frame()
        rig = Rig(id="rig1", name="R", root_bone_id="root", bones=[make_bone("root")])
        frame.rigs = [rig]
        revision_before = win._thumbnail_cache.revision(frame.id)

        new_bones = commands.snapshot_bones(rig.bones)
        new_bones.append(Bone(id="extra", name="extra", parent_id="root", asset_id="asset-1"))

        class _SavedDialog:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def exec(self) -> int:
                return QDialog.DialogCode.Accepted

            def result_bones(self) -> list[Bone]:
                return new_bones

            def result_root_bone_id(self) -> str:
                return "root"

        monkeypatch.setattr(main_window_module, "RigBuilderDialog", _SavedDialog)

        win._on_edit_selected_rig()

        assert win._thumbnail_cache.revision(frame.id) == revision_before + 1
        assert [b.id for b in win._current_frame().rigs[0].bones] == ["root", "extra"]
        assert win._is_dirty is True

    def test_editing_a_frames_pose_does_not_mutate_saved_template(self) -> None:
        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        frame = win._current_frame()
        bone = make_bone("root", x=0.0, y=0.0)
        rig = Rig(id="rig1", name="R", root_bone_id="root", bones=[bone])
        frame.rigs = [rig]

        template = rig_template_from_rig(rig, name="Hero")
        win._execute_command(commands.SaveRigTemplateCommand("Save Template: Hero", template))
        template_x_before = win._project.rig_templates[0].bones[0].x

        before = commands.snapshot_bone(bone)
        bone.x = 500.0
        after = commands.snapshot_bone(bone)
        win._execute_command(commands.TransformBoneCommand("Move Bone", frame.id, "rig1", "root", before, after))

        assert win._current_frame().rigs[0].bones[0].x == 500.0
        assert win._project.rig_templates[0].bones[0].x == template_x_before


def make_multipart_rig() -> Rig:
    body = make_bone("body", x=960.0, y=540.0)
    arm = Bone(id="arm", name="Arm", parent_id="body", asset_id="asset-1")
    hand = Bone(id="hand", name="Hand", parent_id="arm", asset_id="asset-1")
    return Rig(id="rig1", name="Character", root_bone_id="body", bones=[body, arm, hand])


@pytest.mark.usefixtures("qapp")
class TestPartManipulationHotfix:
    """Hotfix UX-1: Quick Add selection, canvas handles, and the Characters
    menu's quick part operations (Delete/Duplicate/Reorder/Reparent/Set Root),
    all driven through the real MainWindow so the wiring itself is covered,
    not just the underlying pure domain.rig functions (already covered by
    test_rig_template.py)."""

    def test_quick_add_selects_the_new_part_immediately(self) -> None:
        """The actual bug behind "Rig creates a static PNG": nothing was
        selected after creation, so no selection box/handles ever appeared."""
        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        asset = make_asset("asset-1")
        win._project.assets.append(asset)
        win._asset_panel.refresh_assets()
        win._asset_panel._list.setCurrentRow(0)

        win._on_quick_create_rig()

        rig = win._current_frame().rigs[0]
        assert win._canvas.selected_bone_id() == rig.root_bone_id

    def test_delete_selected_part_with_children_deletes_subtree(self, monkeypatch) -> None:
        from PySide6.QtWidgets import QMessageBox

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._current_frame().rigs = [make_multipart_rig()]
        win._canvas.select_bone("arm")
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

        win._on_delete_selected_part()

        bone_ids = {b.id for b in win._current_frame().rigs[0].bones}
        assert bone_ids == {"body"}
        assert win._history.can_undo

        win._undo()
        assert {b.id for b in win._current_frame().rigs[0].bones} == {"body", "arm", "hand"}

    def test_delete_selected_part_reparents_children_to_grandparent(self, monkeypatch) -> None:
        from PySide6.QtWidgets import QMessageBox

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._current_frame().rigs = [make_multipart_rig()]
        win._canvas.select_bone("arm")
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))

        win._on_delete_selected_part()

        bones = win._current_frame().rigs[0].bones
        bone_ids = {b.id for b in bones}
        assert bone_ids == {"body", "hand"}
        hand = next(b for b in bones if b.id == "hand")
        assert hand.parent_id == "body"

    def test_delete_selected_root_part_deletes_whole_rig_with_confirmation(self, monkeypatch) -> None:
        from PySide6.QtWidgets import QMessageBox

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._current_frame().rigs = [make_multipart_rig()]
        win._canvas.select_bone("body")
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

        win._on_delete_selected_part()

        assert win._current_frame().rigs == []
        assert win._canvas.selected_bone_id() is None

        win._undo()
        assert len(win._current_frame().rigs) == 1
        assert len(win._current_frame().rigs[0].bones) == 3

    def test_delete_selected_root_part_cancelled_changes_nothing(self, monkeypatch) -> None:
        from PySide6.QtWidgets import QMessageBox

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._current_frame().rigs = [make_multipart_rig()]
        win._canvas.select_bone("body")
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))

        win._on_delete_selected_part()

        assert len(win._current_frame().rigs) == 1
        assert not win._history.can_undo

    def test_bring_forward_and_send_backward_adjust_z_index(self) -> None:
        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._current_frame().rigs = [make_multipart_rig()]
        win._canvas.select_bone("arm")
        arm = win._current_frame().rigs[0].bones[1]
        z_before = arm.z_index

        win._on_bring_forward()
        assert arm.z_index == z_before + 1

        win._on_send_backward()
        win._on_send_backward()
        assert arm.z_index == z_before - 1

        win._undo()
        win._undo()
        win._undo()
        assert arm.z_index == z_before

    def test_set_selected_part_as_root_validates_and_reroots(self) -> None:
        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._current_frame().rigs = [make_multipart_rig()]
        win._canvas.select_bone("arm")

        win._on_set_selected_part_as_root()

        rig = win._current_frame().rigs[0]
        assert rig.root_bone_id == "arm"
        body = next(b for b in rig.bones if b.id == "body")
        assert body.parent_id == "arm"

        win._undo()
        assert win._current_frame().rigs[0].root_bone_id == "body"

    def test_duplicate_selected_part_adds_bone_and_selects_it(self) -> None:
        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._current_frame().rigs = [make_multipart_rig()]
        win._canvas.select_bone("arm")

        win._on_duplicate_selected_part()

        rig = win._current_frame().rigs[0]
        assert len(rig.bones) == 4
        new_bone_id = win._canvas.selected_bone_id()
        assert new_bone_id not in ("body", "arm", "hand")
        new_bone = find_bone_in_list(rig.bones, new_bone_id)
        assert new_bone.parent_id == "body"  # same parent as the original "arm"

    def test_duplicate_selected_root_part_is_rejected(self, monkeypatch) -> None:
        from PySide6.QtWidgets import QMessageBox

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._current_frame().rigs = [make_multipart_rig()]
        win._canvas.select_bone("body")
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

        win._on_duplicate_selected_part()

        assert len(win._current_frame().rigs[0].bones) == 3
        assert not win._history.can_undo

    def test_reparent_selected_part_via_menu_action(self, monkeypatch) -> None:
        from PySide6.QtWidgets import QInputDialog

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._current_frame().rigs = [make_multipart_rig()]
        win._canvas.select_bone("hand")
        monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("body", True)))

        win._on_reparent_selected_part()

        hand = find_bone_in_list(win._current_frame().rigs[0].bones, "hand")
        assert hand.parent_id == "body"

        win._undo()
        hand = find_bone_in_list(win._current_frame().rigs[0].bones, "hand")
        assert hand.parent_id == "arm"

    def test_reparent_that_would_create_a_cycle_is_rejected(self, monkeypatch) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._current_frame().rigs = [make_multipart_rig()]
        win._canvas.select_bone("body")
        win._inspector.set_frame(win._current_frame())
        win._inspector.set_selected_bone("body", "rig1")
        warnings: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2]) or QMessageBox.StandardButton.Ok)
        )

        # body -> arm would create a cycle (arm is body's own descendant).
        win._reparent_part(win._current_frame().id, "rig1", "body", "arm")

        assert warnings, "cycle attempt must be rejected with a warning, not silently applied"
        body = find_bone_in_list(win._current_frame().rigs[0].bones, "body")
        assert body.parent_id is None
        assert not win._history.can_undo

    def test_backspace_deletes_selected_part_not_current_frame(self, monkeypatch) -> None:
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtWidgets import QMessageBox

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._current_frame().rigs = [make_multipart_rig()]
        win._canvas.select_bone("hand")
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        frames_before = len(win._project.frames)

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier)
        handled = win.eventFilter(win, event)

        assert handled is True
        assert len(win._project.frames) == frames_before
        assert find_bone_in_list(win._current_frame().rigs[0].bones, "hand") is None

    def test_backspace_with_no_part_selected_still_deletes_frame(self) -> None:
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._timeline.new_frame()
        win._canvas.clear_selection()
        frames_before = len(win._project.frames)

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier)
        win.eventFilter(win, event)

        assert len(win._project.frames) == frames_before - 1

    def test_escape_mid_drag_reverts_without_recording_a_command(self) -> None:
        from PySide6.QtCore import QPointF

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._current_frame().rigs = [make_multipart_rig()]
        body = win._current_frame().rigs[0].bones[0]
        win._canvas.select_bone("body")

        win._canvas._begin_handle_drag("scale", QPointF(body.x + 20.0, body.y))
        win._canvas._update_bone_drag(QPointF(body.x + 60.0, body.y))
        assert body.scale_x != 1.0

        win._canvas._cancel_bone_drag()

        assert body.scale_x == 1.0
        assert not win._history.can_undo
        assert win._canvas._drag_bone_id is None
