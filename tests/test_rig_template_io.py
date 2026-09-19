from __future__ import annotations

import json

import pytest
from PIL import Image

from pivotcut.domain.assets import Asset
from pivotcut.domain.models import new_project
from pivotcut.domain.rig import Bone, Rig, RigTemplate, rig_template_from_rig, validate_rig_template
from pivotcut.services import asset_manager, rig_template_io


def make_asset(id: str, name: str = "part", width: int = 64, height: int = 64) -> Asset:
    return Asset(id=id, name=name, source_path=f"/tmp/{id}.png", relative_path=None, width=width, height=height)


def make_template() -> RigTemplate:
    root = Bone(id="root", name="Body", parent_id=None, asset_id="asset-body", x=100.0, y=100.0)
    child = Bone(id="arm", name="Arm", parent_id="root", asset_id="asset-arm", x=10.0, y=0.0)
    return RigTemplate(id="tmpl-1", name="Hero", root_bone_id="root", bones=[root, child], category="Heroes")


# -- export_rig_template ------------------------------------------------------------


def test_export_writes_format_version_and_template(tmp_path) -> None:
    template = make_template()
    assets = [make_asset("asset-body"), make_asset("asset-arm")]
    path = tmp_path / "hero.pivotcut-rig.json"

    rig_template_io.export_rig_template(template, assets, path)

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert '"format_version": 1' in text


def test_export_never_embeds_png_binary_data(tmp_path) -> None:
    template = make_template()
    assets = [make_asset("asset-body"), make_asset("asset-arm")]
    path = tmp_path / "hero.pivotcut-rig.json"

    rig_template_io.export_rig_template(template, assets, path)

    text = path.read_text(encoding="utf-8")
    assert "PNG" not in text
    assert "base64" not in text.lower()


def test_export_includes_asset_references_for_referenced_assets_only(tmp_path) -> None:
    template = make_template()
    assets = [make_asset("asset-body"), make_asset("asset-arm"), make_asset("asset-unused")]
    path = tmp_path / "hero.pivotcut-rig.json"

    rig_template_io.export_rig_template(template, assets, path)
    result = rig_template_io.import_rig_template(path)

    ref_ids = {ref.id for ref in result.asset_references}
    assert ref_ids == {"asset-body", "asset-arm"}


# -- import_rig_template -------------------------------------------------------------


def test_import_round_trips_template_structure(tmp_path) -> None:
    template = make_template()
    assets = [make_asset("asset-body"), make_asset("asset-arm")]
    path = tmp_path / "hero.pivotcut-rig.json"
    rig_template_io.export_rig_template(template, assets, path)

    result = rig_template_io.import_rig_template(path)

    assert result.template.id == template.id
    assert result.template.name == "Hero"
    assert result.template.category == "Heroes"
    assert [b.id for b in result.template.bones] == ["root", "arm"]


def test_import_rejects_non_json_file(tmp_path) -> None:
    path = tmp_path / "bad.pivotcut-rig.json"
    path.write_text("not json at all {{{", encoding="utf-8")

    with pytest.raises(rig_template_io.InvalidRigTemplateFileError):
        rig_template_io.import_rig_template(path)


def test_import_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(rig_template_io.InvalidRigTemplateFileError):
        rig_template_io.import_rig_template(tmp_path / "does-not-exist.pivotcut-rig.json")


def test_import_rejects_unsupported_format_version(tmp_path) -> None:
    path = tmp_path / "future.pivotcut-rig.json"
    path.write_text('{"format_version": 999, "template": {}}', encoding="utf-8")

    with pytest.raises(rig_template_io.UnsupportedRigTemplateFormatError):
        rig_template_io.import_rig_template(path)


def test_import_rejects_missing_template_object(tmp_path) -> None:
    path = tmp_path / "no-template.pivotcut-rig.json"
    path.write_text('{"format_version": 1}', encoding="utf-8")

    with pytest.raises(rig_template_io.InvalidRigTemplateFileError):
        rig_template_io.import_rig_template(path)


# -- missing_asset_ids / remap_template_asset_ids (asset mapping flow) ---------------


def test_missing_asset_ids_detects_gaps_in_target_project(tmp_path) -> None:
    template = make_template()
    assets = [make_asset("asset-body"), make_asset("asset-arm")]
    path = tmp_path / "hero.pivotcut-rig.json"
    rig_template_io.export_rig_template(template, assets, path)
    result = rig_template_io.import_rig_template(path)

    # Target project only has "asset-body" already registered.
    target_assets = [make_asset("asset-body")]

    missing = rig_template_io.missing_asset_ids(result.template, target_assets)

    assert missing == ["asset-arm"]


def test_missing_asset_ids_empty_when_all_assets_present(tmp_path) -> None:
    template = make_template()
    assets = [make_asset("asset-body"), make_asset("asset-arm")]
    path = tmp_path / "hero.pivotcut-rig.json"
    rig_template_io.export_rig_template(template, assets, path)
    result = rig_template_io.import_rig_template(path)

    missing = rig_template_io.missing_asset_ids(result.template, assets)

    assert missing == []


def test_remap_template_asset_ids_rewrites_only_mapped_ids(tmp_path) -> None:
    template = make_template()
    assets = [make_asset("asset-body"), make_asset("asset-arm")]
    path = tmp_path / "hero.pivotcut-rig.json"
    rig_template_io.export_rig_template(template, assets, path)
    result = rig_template_io.import_rig_template(path)

    remapped = rig_template_io.remap_template_asset_ids(result.template, {"asset-arm": "local-arm-replacement"})

    by_id = {b.id: b for b in remapped.bones}
    assert by_id["root"].asset_id == "asset-body"  # untouched: not in mapping
    assert by_id["arm"].asset_id == "local-arm-replacement"


def test_remap_template_asset_ids_returns_new_object_original_unchanged(tmp_path) -> None:
    template = make_template()
    assets = [make_asset("asset-body"), make_asset("asset-arm")]
    path = tmp_path / "hero.pivotcut-rig.json"
    rig_template_io.export_rig_template(template, assets, path)
    result = rig_template_io.import_rig_template(path)

    rig_template_io.remap_template_asset_ids(result.template, {"asset-arm": "local-arm"})

    original_by_id = {b.id: b for b in result.template.bones}
    assert original_by_id["arm"].asset_id == "asset-arm"


def test_full_export_import_missing_detect_remap_validate_pipeline(tmp_path) -> None:
    """End-to-end: export from a project, import into a fresh one missing an
    asset, resolve the gap via remap, and confirm the result validates."""
    original_rig = Rig(
        id="rig-1",
        name="Hero",
        root_bone_id="root",
        bones=[
            Bone(id="root", name="Body", parent_id=None, asset_id="asset-body", x=50.0, y=50.0),
            Bone(id="arm", name="Arm", parent_id="root", asset_id="asset-arm", x=10.0, y=0.0),
        ],
    )
    exporting_assets = [make_asset("asset-body"), make_asset("asset-arm")]
    template = rig_template_from_rig(original_rig, name="Hero")
    path = tmp_path / "hero.pivotcut-rig.json"
    rig_template_io.export_rig_template(template, exporting_assets, path)

    imported = rig_template_io.import_rig_template(path)
    target_project_assets = [make_asset("asset-body")]  # missing "asset-arm"

    missing = rig_template_io.missing_asset_ids(imported.template, target_project_assets)
    assert missing == ["asset-arm"]

    resolved_template = rig_template_io.remap_template_asset_ids(imported.template, {"asset-arm": "asset-body"})
    final_assets = target_project_assets  # both bones now reference "asset-body"

    validate_rig_template(resolved_template, final_assets)  # must not raise


# -- Milestone 6C: Character File format (file_type/asset_manifest/metadata) --------


def make_multipart_rig_with_pivot_attach() -> Rig:
    root = Bone(
        id="root", name="Body", parent_id=None, asset_id="asset-body", x=200.0, y=300.0, pivot_x=25.0, pivot_y=35.0
    )
    child = Bone(
        id="arm",
        name="Arm",
        parent_id="root",
        asset_id="asset-arm",
        pivot_x=8.0,
        pivot_y=12.0,
        attach_x=20.0,
        attach_y=5.0,
        z_index=3,
    )
    grandchild = Bone(
        id="hand",
        name="Hand",
        parent_id="arm",
        asset_id="asset-hand",
        pivot_x=4.0,
        pivot_y=4.0,
        attach_x=6.0,
        attach_y=30.0,
        z_index=7,
    )
    return Rig(id="rig-export", name="Hero", root_bone_id="root", bones=[root, child, grandchild])


def test_export_character_file_from_rig_instance_has_file_type_and_metadata(tmp_path) -> None:
    rig = make_multipart_rig_with_pivot_attach()
    assets = [make_asset("asset-body", "body"), make_asset("asset-arm", "arm"), make_asset("asset-hand", "hand")]
    template = rig_template_from_rig(rig, name="Hero")
    path = tmp_path / "Hero.pivotcut-rig.json"

    rig_template_io.export_rig_template(template, assets, path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["file_type"] == rig_template_io.FILE_TYPE
    assert data["format_version"] == rig_template_io.SUPPORTED_FORMAT_VERSION
    assert "asset_manifest" in data
    assert data["metadata"]["template_name"] == "Hero"
    assert data["metadata"]["created_with"] == "PivotCut"
    assert "created_at" in data["metadata"]


def test_export_character_file_preserves_hierarchy_pivot_attach_zindex(tmp_path) -> None:
    rig = make_multipart_rig_with_pivot_attach()
    assets = [make_asset("asset-body", "body"), make_asset("asset-arm", "arm"), make_asset("asset-hand", "hand")]
    template = rig_template_from_rig(rig, name="Hero")
    path = tmp_path / "Hero.pivotcut-rig.json"
    rig_template_io.export_rig_template(template, assets, path)

    imported = rig_template_io.import_rig_template(path)

    by_id = {b.id: b for b in imported.template.bones}
    assert by_id["arm"].parent_id == "root"
    assert by_id["hand"].parent_id == "arm"
    assert (by_id["arm"].pivot_x, by_id["arm"].pivot_y) == (8.0, 12.0)
    assert (by_id["arm"].attach_x, by_id["arm"].attach_y) == (20.0, 5.0)
    assert by_id["arm"].z_index == 3
    assert (by_id["hand"].pivot_x, by_id["hand"].pivot_y) == (4.0, 4.0)
    assert by_id["hand"].z_index == 7
    assert [ref.name for ref in imported.asset_references] == sorted(a.name for a in assets)


def test_import_rig_template_backward_compatible_with_pre_6c_asset_references_key(tmp_path) -> None:
    """Files written before Milestone 6C use 'asset_references' and have no
    file_type/asset_manifest/metadata at all — must still load correctly."""
    legacy_data = {
        "format_version": 1,
        "template": {
            "id": "tmpl-legacy",
            "name": "OldHero",
            "root_bone_id": "root",
            "bones": [
                {
                    "id": "root",
                    "name": "Body",
                    "parent_id": None,
                    "asset_id": "asset-body",
                    "x": 10.0,
                    "y": 20.0,
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
        },
        "asset_references": [
            {"id": "asset-body", "name": "body", "width": 64, "height": 64, "source_path_hint": "/tmp/body.png"}
        ],
    }
    path = tmp_path / "legacy.pivotcut-rig.json"
    path.write_text(json.dumps(legacy_data), encoding="utf-8")

    result = rig_template_io.import_rig_template(path)

    assert result.template.name == "OldHero"
    assert result.asset_references[0].id == "asset-body"
    assert result.asset_references[0].relative_path_hint == ""  # absent in legacy files, defaults cleanly
    assert result.metadata == {}


# -- Milestone 6C: auto_resolve_manifest (relative/absolute/fingerprint) ------------


def _make_png(path, size=(40, 40)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (10, 20, 30, 255)).save(path)


def test_auto_resolve_manifest_finds_relative_path(tmp_path) -> None:
    character_dir = tmp_path / "characters"
    character_dir.mkdir()
    png_dir = tmp_path / "characters" / "pngs"
    png_path = png_dir / "arm.png"
    _make_png(png_path)

    ref = rig_template_io.AssetReference(
        id="old-arm", name="arm", width=40, height=40, source_path_hint="", relative_path_hint="pngs/arm.png"
    )
    character_file_path = character_dir / "Hero.pivotcut-rig.json"
    project = new_project()

    mapping = rig_template_io.auto_resolve_manifest([ref], character_file_path, project, None)

    assert "old-arm" in mapping
    assert len(project.assets) == 1
    assert project.assets[0].name == "arm"


def test_auto_resolve_manifest_falls_back_to_source_path(tmp_path) -> None:
    png_path = tmp_path / "somewhere" / "leg.png"
    _make_png(png_path)

    ref = rig_template_io.AssetReference(
        id="old-leg", name="leg", width=40, height=40, source_path_hint=str(png_path), relative_path_hint="does/not/exist.png"
    )
    project = new_project()

    mapping = rig_template_io.auto_resolve_manifest([ref], tmp_path / "character.pivotcut-rig.json", project, None)

    assert "old-leg" in mapping
    assert project.assets[0].name == "leg"


def test_auto_resolve_manifest_automatch_by_filename_dedups_existing_asset(tmp_path) -> None:
    project = new_project()
    existing = Asset(id="existing-id", name="head", source_path="/gone/head.png", relative_path=None, width=50, height=50)
    project.assets.append(existing)

    # No relative/source path resolves (the exporting machine's file is gone),
    # but an asset with the same name+dimensions is already in this project.
    ref = rig_template_io.AssetReference(
        id="old-head", name="head", width=50, height=50, source_path_hint="/gone/head.png", relative_path_hint=""
    )

    mapping = rig_template_io.auto_resolve_manifest([ref], tmp_path / "character.pivotcut-rig.json", project, None)

    assert mapping == {"old-head": "existing-id"}
    assert len(project.assets) == 1  # deduped — no new asset created


def test_auto_resolve_manifest_missing_asset_does_not_crash_and_is_omitted(tmp_path) -> None:
    project = new_project()
    ref = rig_template_io.AssetReference(
        id="old-gone", name="gone", width=10, height=10, source_path_hint="/nowhere/gone.png", relative_path_hint=""
    )

    mapping = rig_template_io.auto_resolve_manifest([ref], tmp_path / "character.pivotcut-rig.json", project, None)

    assert mapping == {}
    assert project.assets == []


def test_auto_resolve_manifest_skips_already_registered_id(tmp_path) -> None:
    project = new_project()
    project.assets.append(Asset(id="same-id", name="torso", source_path="/x/torso.png", relative_path=None, width=1, height=1))
    ref = rig_template_io.AssetReference(id="same-id", name="torso", width=1, height=1)

    mapping = rig_template_io.auto_resolve_manifest([ref], tmp_path / "character.pivotcut-rig.json", project, None)

    assert mapping == {}  # nothing to remap: bone.asset_id already valid as-is
    assert len(project.assets) == 1


def test_find_asset_by_fingerprint_matches_name_and_dimensions() -> None:
    assets = [
        Asset(id="a1", name="arm", source_path="/x/arm.png", relative_path=None, width=30, height=60),
        Asset(id="a2", name="leg", source_path="/x/leg.png", relative_path=None, width=30, height=80),
    ]
    ref = rig_template_io.AssetReference(id="old", name="arm", width=30, height=60)

    match = rig_template_io.find_asset_by_fingerprint(assets, ref)

    assert match is not None and match.id == "a1"


def test_find_asset_by_fingerprint_no_match_returns_none() -> None:
    assets = [Asset(id="a1", name="arm", source_path="/x/arm.png", relative_path=None, width=30, height=60)]
    ref = rig_template_io.AssetReference(id="old", name="arm", width=999, height=999)

    assert rig_template_io.find_asset_by_fingerprint(assets, ref) is None


def test_import_png_asset_dedup_via_auto_resolve(tmp_path) -> None:
    """Re-importing the same on-disk PNG (by resolved path) must reuse the
    existing project asset rather than creating a duplicate entry."""
    png_path = tmp_path / "shared.png"
    _make_png(png_path)
    project = new_project()
    already = asset_manager.import_png_asset(project, png_path, None)
    assert len(project.assets) == 1

    ref = rig_template_io.AssetReference(id="old-shared", name="shared", width=40, height=40, source_path_hint=str(png_path))
    mapping = rig_template_io.auto_resolve_manifest([ref], tmp_path / "character.pivotcut-rig.json", project, None)

    assert mapping == {"old-shared": already.id}
    assert len(project.assets) == 1  # still just one — deduped by resolved path, not re-imported
