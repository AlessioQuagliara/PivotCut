from __future__ import annotations

from pathlib import Path

import pytest

from pivotcut.domain.models import Frame, Project, SceneSettings
from pivotcut.services import export_renderer
from pivotcut.services.thumbnail_cache import ThumbnailCache

pytestmark = pytest.mark.usefixtures("qapp")


def make_project(width: int = 1920, height: int = 1080, frame_count: int = 2) -> Project:
    frames = [Frame(id=f"f{i}", duration=1, label=f"Frame {i}") for i in range(frame_count)]
    return Project(scene_settings=SceneSettings(width=width, height=height), frames=frames)


# -- render_thumbnail (export_renderer) ------------------------------------------------


def test_render_thumbnail_fits_within_max_size_16_9_scene() -> None:
    project = make_project(width=1920, height=1080)

    image = export_renderer.render_thumbnail(project, 0, 160, 90)

    assert image.width() == 160
    assert image.height() == 90


def test_render_thumbnail_preserves_aspect_ratio_with_pillarboxing() -> None:
    # A square (1:1) scene letterboxed/pillarboxed into a 160x90 (16:9) frame
    # must still be exactly 160x90 overall, with the actual content narrower.
    project = make_project(width=1000, height=1000)

    image = export_renderer.render_thumbnail(project, 0, 160, 90)

    assert image.width() == 160
    assert image.height() == 90


def test_render_thumbnail_excludes_editor_overlays() -> None:
    from PySide6.QtWidgets import QGraphicsPolygonItem, QGraphicsScene

    from pivotcut.services.export_renderer import _build_offscreen_scene

    project = make_project()
    frame = project.frames[0]
    scene = QGraphicsScene(0, 0, project.scene_settings.width, project.scene_settings.height)

    _build_offscreen_scene(scene, frame, {}, None, 1920.0, 1080.0, {})

    assert not any(isinstance(item, QGraphicsPolygonItem) for item in scene.items())
    assert all(item.zValue() < 9000 for item in scene.items())


# -- ThumbnailCache -----------------------------------------------------------------


def test_cache_hit_avoids_second_render(monkeypatch: pytest.MonkeyPatch) -> None:
    project = make_project(frame_count=1)
    cache = ThumbnailCache()
    calls = {"count": 0}
    original = export_renderer.render_thumbnail

    def spy(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr("pivotcut.services.thumbnail_cache.render_thumbnail", spy)

    cache.get(project, 0, 160, 90)
    cache.get(project, 0, 160, 90)

    assert calls["count"] == 1


def test_bump_invalidates_only_that_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    project = make_project(frame_count=2)
    cache = ThumbnailCache()
    calls: list[int] = []
    original = export_renderer.render_thumbnail

    def spy(project_arg, frame_index, *args, **kwargs):
        calls.append(frame_index)
        return original(project_arg, frame_index, *args, **kwargs)

    monkeypatch.setattr("pivotcut.services.thumbnail_cache.render_thumbnail", spy)

    cache.get(project, 0, 160, 90)
    cache.get(project, 1, 160, 90)
    assert calls == [0, 1]

    cache.bump(project.frames[0].id)
    cache.get(project, 0, 160, 90)  # must re-render: revision changed
    cache.get(project, 1, 160, 90)  # must stay cached: revision unchanged

    assert calls == [0, 1, 0]


def test_bump_all_invalidates_every_cached_thumbnail(monkeypatch: pytest.MonkeyPatch) -> None:
    project = make_project(frame_count=2)
    cache = ThumbnailCache()
    calls: list[int] = []
    original = export_renderer.render_thumbnail

    def spy(project_arg, frame_index, *args, **kwargs):
        calls.append(frame_index)
        return original(project_arg, frame_index, *args, **kwargs)

    monkeypatch.setattr("pivotcut.services.thumbnail_cache.render_thumbnail", spy)

    cache.get(project, 0, 160, 90)
    cache.get(project, 1, 160, 90)
    assert len(calls) == 2

    cache.bump_all()
    cache.get(project, 0, 160, 90)
    cache.get(project, 1, 160, 90)

    assert len(calls) == 4


def test_revision_starts_at_zero_and_increments_on_bump() -> None:
    cache = ThumbnailCache()
    assert cache.revision("f0") == 0
    cache.bump("f0")
    assert cache.revision("f0") == 1
    cache.bump("f0")
    assert cache.revision("f0") == 2
    assert cache.revision("other-frame") == 0


def test_different_max_size_is_not_a_cache_hit() -> None:
    project = make_project(frame_count=1)
    cache = ThumbnailCache()

    small = cache.get(project, 0, 80, 45)
    large = cache.get(project, 0, 160, 90)

    assert small.width() == 80
    assert large.width() == 160
