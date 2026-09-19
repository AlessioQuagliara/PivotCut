"""Milestone 6C: Character Library portable workflow.

Domain/service-level coverage (export/import round trip, asset manifest
resolution) lives in test_rig_template_io.py; this file covers the
MainWindow-level, undo/redo-integrated behavior — Add Character to Scene,
Remove Character From Library, and the template preview renderer.
"""

from __future__ import annotations

import pytest

from pivotcut.domain.rig import Bone, Rig, RigTemplate, instantiate_rig_template, rig_template_from_rig
from pivotcut.services.export_renderer import render_rig_template_preview


def make_multipart_rig() -> Rig:
    body = Bone(id="body", name="body", parent_id=None, asset_id="asset-1", x=960.0, y=540.0)
    arm = Bone(id="arm", name="arm", parent_id="body", asset_id="asset-1", pivot_x=5.0, pivot_y=5.0)
    return Rig(id="rig1", name="Character", root_bone_id="body", bones=[body, arm])


# -- instantiate_rig_template: fresh ids, coherent parent mapping (M6C checklist) ----


def test_new_instance_has_different_rig_and_bone_ids_from_template() -> None:
    rig = make_multipart_rig()
    template = rig_template_from_rig(rig, name="Hero")

    instance = instantiate_rig_template(template, 100.0, 100.0)

    assert instance.id != rig.id
    template_bone_ids = {b.id for b in template.bones}
    instance_bone_ids = {b.id for b in instance.bones}
    assert instance_bone_ids.isdisjoint(template_bone_ids)


def test_new_instance_parent_mapping_is_coherent() -> None:
    rig = make_multipart_rig()
    template = rig_template_from_rig(rig, name="Hero")

    instance = instantiate_rig_template(template, 100.0, 100.0)

    by_id = {b.id: b for b in instance.bones}
    arm = next(b for b in instance.bones if b.id != instance.root_bone_id)
    assert arm.parent_id == instance.root_bone_id
    assert arm.parent_id in by_id  # points at a real bone in the new instance, not a stale template id


# -- Preview: no overlay/selection artifacts -----------------------------------------


def test_template_preview_has_no_overlay_and_is_requested_size(qapp) -> None:
    rig = make_multipart_rig()
    template = rig_template_from_rig(rig, name="Hero")

    image = render_rig_template_preview(template, {}, None, size=160)

    assert image.width() == 160
    assert image.height() == 160
    # Corners must stay transparent (no background fill, no border, no
    # selection box) — only actual rig content, if any, may be opaque.
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(159, 159).alpha() == 0
    assert image.pixelColor(0, 159).alpha() == 0
    assert image.pixelColor(159, 0).alpha() == 0


def test_template_preview_empty_template_returns_transparent_canvas(qapp) -> None:
    template = RigTemplate(id="empty", name="Empty", root_bone_id="none", bones=[])

    image = render_rig_template_preview(template, {}, None, size=160)

    assert image.width() == 160 and image.height() == 160
    assert image.pixelColor(80, 80).alpha() == 0


# -- MainWindow-level: Add/Remove Character undo/redo, library-vs-instance isolation --


@pytest.mark.usefixtures("qapp")
class TestCharacterLibraryMainWindow:
    def test_add_character_to_scene_undo_redo(self) -> None:
        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        template = rig_template_from_rig(make_multipart_rig(), name="Hero")
        win._project.rig_templates = [template]

        win._add_character_template_to_scene(template)

        assert len(win._current_frame().rigs) == 1
        new_rig = win._current_frame().rigs[0]
        assert win._canvas.selected_bone_id() == new_rig.root_bone_id

        win._undo()
        assert win._current_frame().rigs == []

        win._redo()
        assert len(win._current_frame().rigs) == 1
        assert win._current_frame().rigs[0].id == new_rig.id

    def test_add_character_to_scene_positions_root_at_scene_center(self) -> None:
        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        template = rig_template_from_rig(make_multipart_rig(), name="Hero")
        win._project.rig_templates = [template]
        scene = win._project.scene_settings

        win._add_character_template_to_scene(template)

        root_bone = win._current_frame().rigs[0].bones[0]
        assert root_bone.x == scene.width / 2.0
        assert root_bone.y == scene.height / 2.0

    def test_remove_character_from_library_undo_redo(self, monkeypatch) -> None:
        from PySide6.QtWidgets import QMessageBox

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        template = rig_template_from_rig(make_multipart_rig(), name="Hero")
        win._project.rig_templates = [template]
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

        win._remove_character_template(template)
        assert win._project.rig_templates == []

        win._undo()
        assert len(win._project.rig_templates) == 1
        assert win._project.rig_templates[0].id == template.id

        win._redo()
        assert win._project.rig_templates == []

    def test_remove_character_from_library_does_not_remove_existing_instances(self, monkeypatch) -> None:
        from PySide6.QtWidgets import QMessageBox

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        template = rig_template_from_rig(make_multipart_rig(), name="Hero")
        win._project.rig_templates = [template]
        win._add_character_template_to_scene(template)
        assert len(win._current_frame().rigs) == 1
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

        win._remove_character_template(template)

        assert win._project.rig_templates == []
        assert len(win._current_frame().rigs) == 1  # the scene instance is untouched

    def test_remove_character_from_library_cancelled_changes_nothing(self, monkeypatch) -> None:
        from PySide6.QtWidgets import QMessageBox

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        template = rig_template_from_rig(make_multipart_rig(), name="Hero")
        win._project.rig_templates = [template]
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))

        win._remove_character_template(template)

        assert len(win._project.rig_templates) == 1
        assert not win._history.can_undo

    def test_save_character_to_file_does_not_mark_project_dirty(self, monkeypatch, tmp_path) -> None:
        from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

        from pivotcut.app.main_window import MainWindow

        win = MainWindow()
        win._current_frame().rigs = [make_multipart_rig()]
        win._canvas.select_bone("body")
        assert win._is_dirty is False

        path = tmp_path / "Hero.pivotcut-rig.json"
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Hero", True)))
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

        win._on_save_character_to_file()

        assert path.exists()
        assert win._is_dirty is False
        assert win._project.rig_templates == []
        assert not win._history.can_undo

    def test_import_character_file_marks_dirty_and_is_undoable(self, tmp_path) -> None:
        """Uses a real, fully-resolvable PNG (via the absolute source-path
        fallback) so the import completes without opening the Asset Mapping
        Dialog — a *real* QDialog.exec() under the offscreen Qt platform
        would block forever with nothing to dismiss it."""
        from PIL import Image

        from pivotcut.app.main_window import MainWindow
        from pivotcut.services import asset_manager, rig_template_io

        exporter = MainWindow()
        png_path = tmp_path / "body.png"
        Image.new("RGBA", (40, 60), (10, 20, 30, 255)).save(png_path)
        asset = asset_manager.import_png_asset(exporter._project, png_path, None)
        body = Bone(id="body", name="body", parent_id=None, asset_id=asset.id, x=100.0, y=100.0)
        exporter._current_frame().rigs = [Rig(id="rig1", name="Character", root_bone_id="body", bones=[body])]
        template = rig_template_from_rig(exporter._current_frame().rigs[0], name="Hero")
        path = tmp_path / "Hero.pivotcut-rig.json"
        rig_template_io.export_rig_template(template, exporter._project.assets, path)

        win = MainWindow()
        assert win._is_dirty is False

        win._import_character_file(path)

        assert len(win._project.rig_templates) == 1
        assert win._is_dirty is True
        assert win._history.can_undo

        win._undo()
        assert win._project.rig_templates == []
