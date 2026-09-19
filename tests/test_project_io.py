from __future__ import annotations

import json
from pathlib import Path

import pytest

from pivotcut.domain.assets import Asset
from pivotcut.domain.camera import Camera
from pivotcut.domain.layer import Layer
from pivotcut.domain.models import Frame, InterpolationSettings, InterpolationType, Project, SceneSettings
from pivotcut.domain.rig import Bone, Rig, RigTemplate, rig_template_from_rig
from pivotcut.domain.timeline import move_frame
from pivotcut.services.project_io import (
    IncompleteProjectDataError,
    InvalidProjectFileError,
    UnsupportedProjectFormatError,
    load_project,
    save_project,
)


def make_project() -> Project:
    return Project(
        format_version=1,
        scene_settings=SceneSettings(width=1920, height=1080, background_color="#2b2b2b"),
        fps=24,
        exposure=3,
        frames=[
            Frame(id="f1", duration=1, label="Frame 1"),
            Frame(id="f2", duration=2, label="Frame 2"),
        ],
        current_frame_index=1,
    )


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    project = make_project()
    path = tmp_path / "demo.pivotcut.json"

    save_project(project, path)
    loaded = load_project(path)

    assert loaded == project


def test_save_writes_readable_json_with_required_keys(tmp_path: Path) -> None:
    path = tmp_path / "demo.pivotcut.json"
    save_project(make_project(), path)

    data = json.loads(path.read_text(encoding="utf-8"))

    for key in (
        "format_version",
        "scene_settings",
        "fps",
        "exposure",
        "frames",
        "current_frame_index",
    ):
        assert key in data


def test_save_then_load_round_trip_preserves_smooth_animation_fields(tmp_path: Path) -> None:
    project = make_project()
    project.smooth_animation_enabled = True
    project.frames[0].interpolation_map = {
        "bone:torso:rotation": InterpolationSettings(
            type=InterpolationType.CUBIC_SPLINE, control_points=(0.17, 0.67, 0.83, 0.67)
        ),
        "camera:zoom": InterpolationSettings(type=InterpolationType.STEP),
    }
    path = tmp_path / "smooth.pivotcut.json"

    save_project(project, path)
    loaded = load_project(path)

    assert loaded == project
    assert loaded.smooth_animation_enabled is True
    assert loaded.frames[0].interpolation_map["camera:zoom"].type is InterpolationType.STEP


def test_legacy_project_file_without_smooth_animation_fields_loads_with_safe_defaults(tmp_path: Path) -> None:
    """A file saved before Smooth Animation existed has no
    'smooth_animation_enabled' and no per-frame 'interpolation_map' at
    all — loading it must default to fully backward-compatible, hard-cut
    behavior rather than raise or silently invent settings."""
    path = tmp_path / "legacy.pivotcut.json"
    legacy_data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#2b2b2b"},
        "fps": 24,
        "exposure": 3,
        "frames": [{"id": "f1", "duration": 1, "label": "Frame 1"}],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(legacy_data), encoding="utf-8")

    loaded = load_project(path)

    assert loaded.smooth_animation_enabled is False
    assert loaded.frames[0].interpolation_map == {}


def test_load_missing_file_raises_invalid_project_file_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.pivotcut.json"

    with pytest.raises(InvalidProjectFileError):
        load_project(missing)


def test_load_corrupted_json_raises_invalid_project_file_error(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.pivotcut.json"
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(InvalidProjectFileError):
        load_project(path)


def test_load_unsupported_format_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "future.pivotcut.json"
    data = json.loads(json.dumps({"format_version": 999}))
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(UnsupportedProjectFormatError):
        load_project(path)


def test_load_missing_format_version_raises_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "no-version.pivotcut.json"
    path.write_text(json.dumps({"scene_settings": {}}), encoding="utf-8")

    with pytest.raises(UnsupportedProjectFormatError):
        load_project(path)


def test_load_missing_frames_raises_incomplete_data_error(tmp_path: Path) -> None:
    path = tmp_path / "no-frames.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#000"},
        "fps": 24,
        "exposure": 3,
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(IncompleteProjectDataError):
        load_project(path)


def test_load_empty_frames_list_raises_incomplete_data_error(tmp_path: Path) -> None:
    path = tmp_path / "empty-frames.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#000"},
        "fps": 24,
        "exposure": 3,
        "frames": [],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(IncompleteProjectDataError):
        load_project(path)


def test_load_out_of_range_current_frame_index_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad-index.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#000"},
        "fps": 24,
        "exposure": 3,
        "frames": [{"id": "f1", "duration": 1, "label": "A"}],
        "current_frame_index": 5,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(IncompleteProjectDataError):
        load_project(path)


def test_load_malformed_scene_settings_raises_incomplete_data_error(tmp_path: Path) -> None:
    path = tmp_path / "bad-scene.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": "not-a-number"},
        "fps": 24,
        "exposure": 3,
        "frames": [{"id": "f1", "duration": 1, "label": "A"}],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(IncompleteProjectDataError):
        load_project(path)


# -- Milestone 2: asset registry + per-frame rigs ------------------------------


def make_project_with_asset_and_rig() -> Project:
    asset = Asset(
        id="asset-1",
        name="body",
        source_path="/Users/demo/PivotCut/assets/body.png",
        relative_path="assets/body.png",
        width=200,
        height=100,
    )
    root = Bone(id="root", name="body", parent_id=None, asset_id="asset-1", x=960.0, y=540.0)
    child = Bone(id="child", name="arm", parent_id="root", asset_id="asset-1", x=20.0, y=0.0, rotation=15.0)
    rig = Rig(id="rig-1", name="body", root_bone_id="root", bones=[root, child])
    frame = Frame(id="f1", duration=1, label="Frame 1", rigs=[rig])
    return Project(assets=[asset], frames=[frame], current_frame_index=0)


def test_round_trip_with_assets_and_rig(tmp_path: Path) -> None:
    project = make_project_with_asset_and_rig()
    path = tmp_path / "character.pivotcut.json"

    save_project(project, path)
    loaded = load_project(path)

    assert loaded == project


def test_round_trip_preserves_asset_with_no_relative_path(tmp_path: Path) -> None:
    project = make_project_with_asset_and_rig()
    project.assets[0].relative_path = None
    path = tmp_path / "unsaved-asset.pivotcut.json"

    save_project(project, path)
    loaded = load_project(path)

    assert loaded.assets[0].relative_path is None
    assert loaded.assets[0].source_path == project.assets[0].source_path


def test_load_m1_project_without_assets_or_rigs_defaults_to_empty(tmp_path: Path) -> None:
    """A Milestone 1 file has no 'assets' key and frames have no 'rigs' key."""
    path = tmp_path / "legacy-m1.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#000"},
        "fps": 24,
        "exposure": 3,
        "frames": [{"id": "f1", "duration": 1, "label": "A"}],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = load_project(path)

    assert loaded.assets == []
    assert loaded.frames[0].rigs == []


def test_load_rejects_rig_with_invalid_structure(tmp_path: Path) -> None:
    """A rig whose bone references a non-existent asset must fail to load."""
    path = tmp_path / "bad-rig.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#000"},
        "fps": 24,
        "exposure": 3,
        "assets": [],
        "frames": [
            {
                "id": "f1",
                "duration": 1,
                "label": "A",
                "rigs": [
                    {
                        "id": "rig-1",
                        "name": "body",
                        "root_bone_id": "root",
                        "bones": [
                            {
                                "id": "root",
                                "name": "body",
                                "parent_id": None,
                                "asset_id": "missing-asset",
                                "x": 0.0,
                                "y": 0.0,
                                "rotation": 0.0,
                                "scale_x": 1.0,
                                "scale_y": 1.0,
                                "pivot_x": 0.0,
                                "pivot_y": 0.0,
                                "z_index": 0,
                                "visible": True,
                                "opacity": 1.0,
                            }
                        ],
                    }
                ],
            }
        ],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(IncompleteProjectDataError):
        load_project(path)


# -- Milestone 3: camera + layers ----------------------------------------------


def make_project_with_camera_and_layers() -> Project:
    asset = Asset(
        id="asset-bg",
        name="sky",
        source_path="/Users/demo/PivotCut/assets/sky.png",
        relative_path="assets/sky.png",
        width=800,
        height=400,
    )
    camera = Camera(x=120.0, y=40.0, zoom=1.5)
    background = Layer(
        id="layer-bg",
        name="sky",
        asset_id="asset-bg",
        x=960.0,
        y=540.0,
        rotation=0.0,
        scale_x=1.0,
        scale_y=1.0,
        pivot_x=400.0,
        pivot_y=200.0,
        z_index=-100,
        z_depth=3.0,
        visible=True,
        opacity=1.0,
    )
    foreground = Layer(
        id="layer-fg",
        name="sky-fg",
        asset_id="asset-bg",
        x=500.0,
        y=300.0,
        rotation=10.0,
        scale_x=1.2,
        scale_y=1.2,
        pivot_x=400.0,
        pivot_y=200.0,
        z_index=100,
        z_depth=0.0,
        visible=False,
        opacity=0.5,
    )
    frame = Frame(id="f1", duration=1, label="Frame 1", camera=camera, layers=[background, foreground])
    return Project(assets=[asset], frames=[frame], current_frame_index=0)


def test_round_trip_with_camera_and_layers(tmp_path: Path) -> None:
    project = make_project_with_camera_and_layers()
    path = tmp_path / "scene.pivotcut.json"

    save_project(project, path)
    loaded = load_project(path)

    assert loaded == project


def test_load_m1_project_defaults_camera_and_layers(tmp_path: Path) -> None:
    """A Milestone 1 file has no 'assets', and frames have no 'rigs'/'camera'/'layers'."""
    path = tmp_path / "legacy-m1.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#000"},
        "fps": 24,
        "exposure": 3,
        "frames": [{"id": "f1", "duration": 1, "label": "A"}],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = load_project(path)

    assert loaded.frames[0].camera == Camera()
    assert loaded.frames[0].layers == []


def test_load_m2_project_without_camera_or_layers_defaults(tmp_path: Path) -> None:
    """A Milestone 2 file has 'assets'/'rigs' but no 'camera'/'layers' key at all."""
    path = tmp_path / "legacy-m2.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#000"},
        "fps": 24,
        "exposure": 3,
        "assets": [],
        "frames": [{"id": "f1", "duration": 1, "label": "A", "rigs": []}],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = load_project(path)

    assert loaded.frames[0].camera == Camera()
    assert loaded.frames[0].layers == []


def test_load_rejects_invalid_camera_zoom(tmp_path: Path) -> None:
    path = tmp_path / "bad-camera.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#000"},
        "fps": 24,
        "exposure": 3,
        "assets": [],
        "frames": [
            {
                "id": "f1",
                "duration": 1,
                "label": "A",
                "camera": {"x": 0.0, "y": 0.0, "zoom": 0.0},
            }
        ],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(IncompleteProjectDataError):
        load_project(path)


def test_load_rejects_layer_with_negative_z_depth(tmp_path: Path) -> None:
    path = tmp_path / "bad-layer.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#000"},
        "fps": 24,
        "exposure": 3,
        "assets": [
            {
                "id": "asset-1",
                "name": "sky",
                "source_path": "/tmp/sky.png",
                "relative_path": None,
                "width": 100,
                "height": 100,
            }
        ],
        "frames": [
            {
                "id": "f1",
                "duration": 1,
                "label": "A",
                "layers": [
                    {
                        "id": "layer-1",
                        "name": "sky",
                        "asset_id": "asset-1",
                        "x": 0.0,
                        "y": 0.0,
                        "rotation": 0.0,
                        "scale_x": 1.0,
                        "scale_y": 1.0,
                        "pivot_x": 0.0,
                        "pivot_y": 0.0,
                        "z_index": 0,
                        "z_depth": -1.0,
                        "visible": True,
                        "opacity": 1.0,
                    }
                ],
            }
        ],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(IncompleteProjectDataError):
        load_project(path)


def test_load_rejects_duplicate_layer_ids(tmp_path: Path) -> None:
    path = tmp_path / "dup-layer.pivotcut.json"

    def make_layer_dict(layer_id: str) -> dict:
        return {
            "id": layer_id,
            "name": "sky",
            "asset_id": "asset-1",
            "x": 0.0,
            "y": 0.0,
            "rotation": 0.0,
            "scale_x": 1.0,
            "scale_y": 1.0,
            "pivot_x": 0.0,
            "pivot_y": 0.0,
            "z_index": 0,
            "z_depth": 0.0,
            "visible": True,
            "opacity": 1.0,
        }

    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#000"},
        "fps": 24,
        "exposure": 3,
        "assets": [
            {
                "id": "asset-1",
                "name": "sky",
                "source_path": "/tmp/sky.png",
                "relative_path": None,
                "width": 100,
                "height": 100,
            }
        ],
        "frames": [
            {
                "id": "f1",
                "duration": 1,
                "label": "A",
                "layers": [make_layer_dict("same-id"), make_layer_dict("same-id")],
            }
        ],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(IncompleteProjectDataError):
        load_project(path)


# -- Milestone 5A: reordered frames round-trip correctly -----------------------


def test_reordered_timeline_round_trips_through_save_and_load(tmp_path: Path) -> None:
    project = Project(
        frames=[
            Frame(id="f1", duration=1, label="A"),
            Frame(id="f2", duration=1, label="B"),
            Frame(id="f3", duration=1, label="C"),
        ],
        current_frame_index=0,
    )

    move_frame(project, 0, 2)  # -> B, C, A
    assert [f.id for f in project.frames] == ["f2", "f3", "f1"]

    path = tmp_path / "reordered.pivotcut.json"
    save_project(project, path)
    loaded = load_project(path)

    assert [f.id for f in loaded.frames] == ["f2", "f3", "f1"]
    assert loaded.current_frame_index == project.current_frame_index
    assert loaded == project


# -- Milestone 6A: rig_templates + attach_x/attach_y persistence ---------------


def make_project_with_rig_template() -> Project:
    asset = Asset(id="asset-1", name="body", source_path="/tmp/body.png", relative_path=None, width=64, height=64)
    root = Bone(id="root", name="body", parent_id=None, asset_id="asset-1", x=960.0, y=540.0)
    rig = Rig(id="rig-1", name="body", root_bone_id="root", bones=[root])
    template = rig_template_from_rig(rig, name="Hero", canvas_width=1920, canvas_height=1080, category="Heroes")
    frame = Frame(id="f1", duration=1, label="Frame 1")
    return Project(assets=[asset], rig_templates=[template], frames=[frame])


def test_round_trip_with_rig_templates(tmp_path: Path) -> None:
    project = make_project_with_rig_template()
    path = tmp_path / "with-template.pivotcut.json"

    save_project(project, path)
    loaded = load_project(path)

    assert loaded == project
    assert loaded.rig_templates[0].name == "Hero"
    assert loaded.rig_templates[0].category == "Heroes"
    assert loaded.rig_templates[0].canvas_width == 1920.0


def test_load_legacy_project_without_rig_templates_defaults_to_empty(tmp_path: Path) -> None:
    """A Milestone <6A file has no 'rig_templates' key at all."""
    path = tmp_path / "legacy-no-templates.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#000"},
        "fps": 24,
        "exposure": 3,
        "frames": [{"id": "f1", "duration": 1, "label": "A"}],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = load_project(path)

    assert loaded.rig_templates == []


def test_load_rejects_rig_template_with_invalid_structure(tmp_path: Path) -> None:
    path = tmp_path / "bad-template.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#000"},
        "fps": 24,
        "exposure": 3,
        "assets": [],
        "rig_templates": [
            {
                "id": "t1",
                "name": "Bad",
                "root_bone_id": "root",
                "bones": [
                    {
                        "id": "root",
                        "name": "root",
                        "parent_id": None,
                        "asset_id": "missing-asset",
                        "x": 0.0,
                        "y": 0.0,
                        "rotation": 0.0,
                        "scale_x": 1.0,
                        "scale_y": 1.0,
                        "pivot_x": 0.0,
                        "pivot_y": 0.0,
                        "z_index": 0,
                        "visible": True,
                        "opacity": 1.0,
                    }
                ],
            }
        ],
        "frames": [{"id": "f1", "duration": 1, "label": "A"}],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(IncompleteProjectDataError):
        load_project(path)


def test_load_legacy_bone_without_attach_fields_derives_from_current_anchor(tmp_path: Path) -> None:
    """A bone saved before Milestone 6A has no attach_x/attach_y at all."""
    path = tmp_path / "legacy-bone.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#000"},
        "fps": 24,
        "exposure": 3,
        "assets": [
            {
                "id": "asset-1",
                "name": "body",
                "source_path": "/tmp/body.png",
                "relative_path": None,
                "width": 100,
                "height": 100,
            }
        ],
        "frames": [
            {
                "id": "f1",
                "duration": 1,
                "label": "A",
                "rigs": [
                    {
                        "id": "rig-1",
                        "name": "body",
                        "root_bone_id": "root",
                        "bones": [
                            {
                                "id": "root",
                                "name": "root",
                                "parent_id": None,
                                "asset_id": "asset-1",
                                "x": 10.0,
                                "y": 20.0,
                                "rotation": 0.0,
                                "scale_x": 1.0,
                                "scale_y": 1.0,
                                "pivot_x": 5.0,
                                "pivot_y": 6.0,
                                "z_index": 0,
                                "visible": True,
                                "opacity": 1.0,
                            }
                        ],
                    }
                ],
            }
        ],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = load_project(path)

    bone = loaded.frames[0].rigs[0].bones[0]
    assert (bone.attach_x, bone.attach_y) == (15.0, 26.0)  # x + pivot_x, y + pivot_y


def test_load_rejects_frame_with_malformed_interpolation_map(tmp_path: Path) -> None:
    path = tmp_path / "bad-interpolation.pivotcut.json"
    data = {
        "format_version": 1,
        "scene_settings": {"width": 1920, "height": 1080, "background_color": "#2b2b2b"},
        "fps": 24,
        "exposure": 3,
        "frames": [
            {
                "id": "f1",
                "duration": 1,
                "label": "",
                "interpolation_map": {"camera:zoom": {"type": "not-a-real-interpolation-type"}},
            }
        ],
        "current_frame_index": 0,
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(IncompleteProjectDataError):
        load_project(path)
