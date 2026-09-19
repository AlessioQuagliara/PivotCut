from __future__ import annotations

from pathlib import Path

import pytest

from pivotcut.domain.assets import Asset
from pivotcut.domain.camera import Camera
from pivotcut.domain.layer import Layer
from pivotcut.domain.models import Frame, Project, SceneSettings
from pivotcut.domain.playback import PlaybackValidationError
from pivotcut.domain.rig import Bone, Rig
from pivotcut.services.export_renderer import render_frame_to_qimage, render_video_frame_to_qimage, save_png_sequence

pytestmark = pytest.mark.usefixtures("qapp")


def make_bone(id: str, asset_id: str = "missing-asset", x: float = 100.0, y: float = 100.0) -> Bone:
    return Bone(id=id, name=id, parent_id=None, asset_id=asset_id, x=x, y=y)


def make_project(
    width: int = 320,
    height: int = 240,
    frame_count: int = 1,
    fps: int = 24,
    exposure: int = 3,
    background_color: str = "#2b2b2b",
    with_rig: bool = True,
    with_layer: bool = True,
) -> Project:
    frames = []
    for i in range(frame_count):
        rigs = (
            [Rig(id=f"rig-{i}", name="R", root_bone_id=f"b-{i}", bones=[make_bone(f"b-{i}")])]
            if with_rig
            else []
        )
        layers = (
            [Layer(id=f"layer-{i}", name="L", asset_id="missing-layer-asset", x=50.0, y=50.0)]
            if with_layer
            else []
        )
        frames.append(Frame(id=f"f-{i}", duration=1, label=f"Frame {i}", rigs=rigs, camera=Camera(), layers=layers))
    return Project(
        scene_settings=SceneSettings(width=width, height=height, background_color=background_color),
        fps=fps,
        exposure=exposure,
        frames=frames,
    )


# -- render_frame_to_qimage --------------------------------------------------------


def test_render_frame_to_qimage_matches_scene_dimensions() -> None:
    project = make_project(width=640, height=480)

    image = render_frame_to_qimage(project, 0)

    assert image.width() == 640
    assert image.height() == 480


def test_render_frame_to_qimage_uses_argb32_premultiplied_format() -> None:
    project = make_project()

    image = render_frame_to_qimage(project, 0)

    assert image.format().name == "Format_ARGB32_Premultiplied"


def test_render_frame_to_qimage_with_rig_and_layers_raises_no_exceptions() -> None:
    project = make_project(with_rig=True, with_layer=True)

    render_frame_to_qimage(project, 0)  # must not raise


def test_render_frame_to_qimage_missing_asset_renders_placeholder_without_crashing() -> None:
    # Every asset_id in make_project() is deliberately unresolvable (no
    # matching Asset registered), exercising the missing-asset placeholder
    # path used by both bones and layers.
    project = make_project(with_rig=True, with_layer=True)

    image = render_frame_to_qimage(project, 0)  # must not raise / crash

    assert not image.isNull()


def test_render_frame_to_qimage_with_resolvable_asset_renders_real_pixmap(tmp_path: Path) -> None:
    from PIL import Image

    png_path = tmp_path / "part.png"
    Image.new("RGBA", (32, 32), (255, 0, 0, 255)).save(png_path)
    asset = Asset(id="asset-1", name="part", source_path=str(png_path), relative_path=None, width=32, height=32)

    bone = Bone(id="b1", name="b1", parent_id=None, asset_id="asset-1", x=10.0, y=10.0)
    rig = Rig(id="rig1", name="R", root_bone_id="b1", bones=[bone])
    frame = Frame(id="f1", duration=1, label="F1", rigs=[rig])
    project = Project(scene_settings=SceneSettings(width=100, height=100), assets=[asset], frames=[frame])

    image = render_frame_to_qimage(project, 0)

    assert not image.isNull()
    # A pixel inside the placed 32x32 red square should be opaque red, not
    # background/placeholder color.
    pixel = image.pixelColor(15, 15)
    assert pixel.red() > 200
    assert pixel.alpha() > 200


def test_render_video_frame_to_qimage_ignores_progress_when_smooth_disabled() -> None:
    project = make_project(frame_count=2)
    project.smooth_animation_enabled = False

    baseline = render_frame_to_qimage(project, 0)
    mid_progress = render_video_frame_to_qimage(project, 0, 0.5)

    # Every project pre-dating Smooth Animation, and every project with it
    # left off, renders exactly as it always has — frame_progress is inert.
    assert mid_progress == baseline


def test_render_video_frame_to_qimage_eases_toward_next_pose_when_smooth_enabled(tmp_path: Path) -> None:
    from PIL import Image

    png_path = tmp_path / "part.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(png_path)
    asset = Asset(id="asset-1", name="part", source_path=str(png_path), relative_path=None, width=20, height=20)

    start_bone = Bone(id="b1", name="b1", parent_id=None, asset_id="asset-1", x=10.0, y=10.0)
    end_bone = Bone(id="b1", name="b1", parent_id=None, asset_id="asset-1", x=60.0, y=10.0)
    frame_0 = Frame(
        id="f0", duration=1, label="", rigs=[Rig(id="rig1", name="R", root_bone_id="b1", bones=[start_bone])]
    )
    frame_1 = Frame(
        id="f1", duration=1, label="", rigs=[Rig(id="rig1", name="R", root_bone_id="b1", bones=[end_bone])]
    )
    project = Project(
        scene_settings=SceneSettings(width=100, height=100),
        assets=[asset],
        frames=[frame_0, frame_1],
        exposure=4,
        smooth_animation_enabled=True,
    )

    at_progress_zero = render_video_frame_to_qimage(project, 0, 0.0)
    mid_transition = render_video_frame_to_qimage(project, 0, 0.5)
    exact_pose_0 = render_frame_to_qimage(project, 0)

    assert at_progress_zero == exact_pose_0  # progress 0.0 is exactly the pose itself
    assert mid_transition != exact_pose_0  # visibly mid-ease toward pose 1, not a hard cut


def test_save_png_sequence_smooth_mode_keeps_the_same_frame_count(tmp_path: Path) -> None:
    project = make_project(frame_count=3)
    project.smooth_animation_enabled = True

    written = save_png_sequence(project, tmp_path / "out")

    assert len(written) == 9  # unchanged: 3 poses * exposure 3, same as discrete mode


def test_render_frame_to_qimage_out_of_range_frame_index_raises() -> None:
    project = make_project(frame_count=1)

    with pytest.raises(IndexError):
        render_frame_to_qimage(project, 5)


def test_render_frame_to_qimage_rejects_invalid_playback_settings() -> None:
    project = make_project()
    project.fps = 0

    with pytest.raises(PlaybackValidationError):
        render_frame_to_qimage(project, 0)


def test_render_frame_to_qimage_preserves_transparent_background_alpha() -> None:
    project = make_project(background_color="#00000000", with_rig=False, with_layer=False)

    image = render_frame_to_qimage(project, 0, force_opaque_background=False)

    corner_pixel = image.pixelColor(0, 0)
    assert corner_pixel.alpha() == 0


def test_render_frame_to_qimage_force_opaque_background_flattens_alpha() -> None:
    project = make_project(background_color="#00000000", with_rig=False, with_layer=False)

    image = render_frame_to_qimage(project, 0, force_opaque_background=True)

    corner_pixel = image.pixelColor(0, 0)
    assert corner_pixel.alpha() == 255


def test_render_frame_to_qimage_excludes_editor_overlays() -> None:
    """The offscreen scene must contain exactly the rig bones + layers, nothing else.

    No selection outline, no output-frame border, no grid: those are
    CanvasView-only editor concerns (see ui/canvas_view.py, ui/graphics_items.py)
    and must never leak into exported pixels.
    """
    from PySide6.QtWidgets import QGraphicsScene

    from pivotcut.services.export_renderer import _build_offscreen_scene

    project = make_project(with_rig=True, with_layer=True)
    frame = project.frames[0]
    scene = QGraphicsScene(0, 0, project.scene_settings.width, project.scene_settings.height)

    _build_offscreen_scene(scene, frame, {}, None, 320.0, 240.0, {})

    # One bone item + one layer item, both rendered as "missing asset"
    # placeholders since no real Asset is registered (each placeholder has
    # its own text-label child item, hence 2 top-level items) — no extra
    # top-level items such as a selection outline or output-frame border.
    top_level_items = [item for item in scene.items() if item.parentItem() is None]
    assert len(top_level_items) == 2
    # The editor's selection outline / output-frame border both use
    # zValue >= 9000 (see graphics_items._make_selection_outline and
    # canvas_view._OUTPUT_FRAME_Z) — none of that must appear here.
    assert all(item.zValue() < 9000 for item in scene.items())
    # No polygon item at all: that is exclusively the selection-outline shape.
    from PySide6.QtWidgets import QGraphicsPolygonItem

    assert not any(isinstance(item, QGraphicsPolygonItem) for item in scene.items())


# -- save_png_sequence -----------------------------------------------------------


def test_save_png_sequence_respects_exposure(tmp_path: Path) -> None:
    project = make_project(frame_count=3, exposure=3)

    written = save_png_sequence(project, tmp_path)

    assert len(written) == 9  # 3 poses * exposure 3


def test_save_png_sequence_uses_six_digit_sequential_names(tmp_path: Path) -> None:
    project = make_project(frame_count=2, exposure=2)

    written = save_png_sequence(project, tmp_path)

    assert [p.name for p in written] == [
        "frame_000001.png",
        "frame_000002.png",
        "frame_000003.png",
        "frame_000004.png",
    ]


def test_save_png_sequence_writes_real_nonempty_files(tmp_path: Path) -> None:
    project = make_project(frame_count=1, exposure=2)

    written = save_png_sequence(project, tmp_path)

    for path in written:
        assert path.is_file()
        assert path.stat().st_size > 0


def test_save_png_sequence_exposure_one(tmp_path: Path) -> None:
    project = make_project(frame_count=5, exposure=1)

    written = save_png_sequence(project, tmp_path)

    assert len(written) == 5


def test_save_png_sequence_invokes_progress_callback(tmp_path: Path) -> None:
    project = make_project(frame_count=2, exposure=3)
    calls: list[tuple[int, int]] = []

    save_png_sequence(project, tmp_path, progress_callback=lambda done, total: calls.append((done, total)))

    assert calls == [(1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 6)]


def test_save_png_sequence_cancellation_stops_cleanly_without_deleting_written_files(tmp_path: Path) -> None:
    project = make_project(frame_count=4, exposure=3)  # 12 total frames

    written = save_png_sequence(project, tmp_path, cancel_requested=lambda: len(list(tmp_path.glob("*.png"))) >= 4)

    assert 0 < len(written) < 12
    # Files already written must remain on disk (never auto-deleted on cancel).
    for path in written:
        assert path.is_file()


def test_save_png_sequence_creates_output_directory_if_missing(tmp_path: Path) -> None:
    project = make_project(frame_count=1, exposure=1)
    nested = tmp_path / "nested" / "output"

    written = save_png_sequence(project, nested)

    assert nested.is_dir()
    assert len(written) == 1
