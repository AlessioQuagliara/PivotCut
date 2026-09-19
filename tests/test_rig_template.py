from __future__ import annotations

import pytest

from pivotcut.domain.assets import Asset
from pivotcut.domain.rig import (
    Bone,
    Rig,
    RigTemplate,
    RigValidationError,
    create_child_bone,
    duplicate_bone,
    find_bone_in_list,
    instantiate_rig_template,
    remove_bone_reparent_children,
    remove_bone_subtree,
    reparent_bone,
    rig_template_from_rig,
    set_attach_point,
    set_pivot_preserving_attach_point,
    set_root_bone,
    validate_rig_template,
    world_transforms,
)


def make_asset(id: str = "asset-1") -> Asset:
    return Asset(id=id, name="part", source_path="/tmp/part.png", relative_path=None, width=64, height=48)


def make_bone(id: str, parent_id: str | None, **kwargs) -> Bone:
    defaults = dict(name=id, asset_id="asset-1")
    defaults.update(kwargs)
    return Bone(id=id, parent_id=parent_id, **defaults)


# -- attach point / pivot math -------------------------------------------------------


def test_create_child_bone_defaults_attach_to_parent_pivot() -> None:
    asset = make_asset()
    parent = make_bone("root", None, x=500.0, y=500.0, pivot_x=30.0, pivot_y=40.0)
    child = create_child_bone(asset, parent, name="Arm")

    assert child.parent_id == "root"
    assert (child.attach_x, child.attach_y) == (30.0, 40.0)
    assert child.pivot_x == pytest.approx(asset.width / 2.0)
    assert child.pivot_y == pytest.approx(asset.height / 2.0)


def test_set_attach_point_keeps_pivot_fixed_and_updates_xy() -> None:
    bone = make_bone("b", "root", pivot_x=10.0, pivot_y=5.0)
    set_attach_point(bone, 200.0, 100.0)
    assert (bone.attach_x, bone.attach_y) == (200.0, 100.0)
    assert (bone.pivot_x, bone.pivot_y) == (10.0, 5.0)
    assert (bone.x, bone.y) == pytest.approx((190.0, 95.0))


def test_set_pivot_preserving_attach_point_keeps_attach_fixed() -> None:
    bone = make_bone("b", "root", attach_x=200.0, attach_y=100.0, pivot_x=10.0, pivot_y=5.0, x=190.0, y=95.0)
    set_pivot_preserving_attach_point(bone, 20.0, 8.0)
    assert (bone.pivot_x, bone.pivot_y) == (20.0, 8.0)
    assert (bone.attach_x, bone.attach_y) == (200.0, 100.0)  # unchanged
    assert (bone.x, bone.y) == pytest.approx((180.0, 92.0))


def test_child_pivot_coincides_with_parent_attach_point_in_world_space() -> None:
    """The core Milestone 6A invariant, stated explicitly in the spec."""
    root = make_bone("root", None, x=500.0, y=400.0, pivot_x=25.0, pivot_y=30.0, rotation=15.0)
    asset = make_asset()
    child = create_child_bone(asset, root, name="Arm")
    set_attach_point(child, 999.0, 12.0)  # anywhere; must still coincide

    rig = Rig(id="r1", name="R", root_bone_id="root", bones=[root, child])
    world = world_transforms(rig)

    parent_attach_world = world["root"].apply_point(child.attach_x, child.attach_y)
    child_pivot_world = world[child.id].apply_point(child.pivot_x, child.pivot_y)

    assert child_pivot_world == pytest.approx(parent_attach_world)


def test_pivot_attach_coincidence_holds_across_three_levels() -> None:
    asset = make_asset()
    root = make_bone("root", None, x=100.0, y=100.0, rotation=30.0, scale_x=1.5, pivot_x=10.0, pivot_y=10.0)
    mid = create_child_bone(asset, root, name="mid")
    mid.id = "mid"
    set_attach_point(mid, 40.0, 5.0)
    mid.rotation = -20.0
    mid.scale_y = 0.8

    leaf = create_child_bone(asset, mid, name="leaf")
    leaf.id = "leaf"
    set_attach_point(leaf, 15.0, 15.0)

    rig = Rig(id="r1", name="R", root_bone_id="root", bones=[root, mid, leaf])
    world = world_transforms(rig)

    assert world["mid"].apply_point(mid.pivot_x, mid.pivot_y) == pytest.approx(
        world["root"].apply_point(mid.attach_x, mid.attach_y)
    )
    assert world["leaf"].apply_point(leaf.pivot_x, leaf.pivot_y) == pytest.approx(
        world["mid"].apply_point(leaf.attach_x, leaf.attach_y)
    )


# -- reparent / cycle detection ------------------------------------------------------


def test_reparent_bone_updates_parent_id() -> None:
    bones = [make_bone("a", None), make_bone("b", "a"), make_bone("c", "a")]
    reparent_bone(bones, "c", "b")
    assert find_bone_in_list(bones, "c").parent_id == "b"


def test_reparent_bone_rejects_self_parent() -> None:
    bones = [make_bone("a", None), make_bone("b", "a")]
    with pytest.raises(RigValidationError):
        reparent_bone(bones, "b", "b")


def test_reparent_bone_rejects_cycle_via_descendant() -> None:
    bones = [make_bone("a", None), make_bone("b", "a"), make_bone("c", "b")]
    with pytest.raises(RigValidationError):
        reparent_bone(bones, "a", "c")


def test_reparent_bone_rejects_missing_target() -> None:
    bones = [make_bone("a", None)]
    with pytest.raises(RigValidationError):
        reparent_bone(bones, "a", "does-not-exist")


# -- remove subtree / reparent children ----------------------------------------------


def test_remove_bone_subtree_removes_descendants() -> None:
    bones = [make_bone("a", None), make_bone("b", "a"), make_bone("c", "b"), make_bone("d", "a")]
    result = remove_bone_subtree(bones, "b", root_bone_id="a")
    assert {b.id for b in result} == {"a", "d"}


def test_remove_bone_subtree_rejects_removing_root() -> None:
    bones = [make_bone("a", None), make_bone("b", "a")]
    with pytest.raises(RigValidationError):
        remove_bone_subtree(bones, "a", root_bone_id="a")


def test_remove_bone_reparent_children_reattaches_to_grandparent() -> None:
    bones = [make_bone("a", None), make_bone("b", "a"), make_bone("c", "b"), make_bone("d", "b")]
    result = remove_bone_reparent_children(bones, "b", root_bone_id="a")
    ids_and_parents = {b.id: b.parent_id for b in result}
    assert "b" not in ids_and_parents
    assert ids_and_parents["c"] == "a"
    assert ids_and_parents["d"] == "a"


def test_remove_bone_reparent_children_rejects_removing_root() -> None:
    bones = [make_bone("a", None), make_bone("b", "a")]
    with pytest.raises(RigValidationError):
        remove_bone_reparent_children(bones, "a", root_bone_id="a")


# -- duplicate ------------------------------------------------------------------------


def test_duplicate_bone_creates_sibling_with_fresh_id_and_copy_suffix() -> None:
    bones = [make_bone("a", None), make_bone("b", "a", name="Arm")]
    new_bone = duplicate_bone(bones, "b")
    assert new_bone.id != "b"
    assert new_bone.parent_id == "a"
    assert new_bone.name == "Arm (copy)"


def test_duplicate_bone_does_not_include_children() -> None:
    bones = [make_bone("a", None), make_bone("b", "a"), make_bone("c", "b")]
    new_bone = duplicate_bone(bones, "b")
    # duplicate_bone only returns the new bone; caller decides whether to
    # append it — verify no bone in the original list points at the copy.
    assert all(b.parent_id != new_bone.id for b in bones)


# -- set_root_bone ----------------------------------------------------------------


def test_set_root_bone_reverses_path_to_new_root() -> None:
    bones = [make_bone("a", None), make_bone("b", "a"), make_bone("c", "b"), make_bone("d", "a")]
    set_root_bone(bones, "c")
    by_id = {b.id: b.parent_id for b in bones}
    assert by_id == {"a": "b", "b": "c", "c": None, "d": "a"}


# -- Rig <-> RigTemplate conversion --------------------------------------------------


def make_three_bone_rig() -> Rig:
    asset = make_asset()
    root = make_bone("root", None, x=960.0, y=540.0, pivot_x=32.0, pivot_y=24.0)
    mid = create_child_bone(asset, root, name="mid")
    mid.id = "mid"
    leaf = create_child_bone(asset, mid, name="leaf")
    leaf.id = "leaf"
    return Rig(id="rig-1", name="Hero", root_bone_id="root", bones=[root, mid, leaf])


def test_rig_template_from_rig_preserves_structure_and_ids() -> None:
    rig = make_three_bone_rig()
    template = rig_template_from_rig(rig, name="Hero Template", category="Heroes")

    assert template.name == "Hero Template"
    assert template.category == "Heroes"
    assert template.root_bone_id == rig.root_bone_id
    assert {b.id for b in template.bones} == {b.id for b in rig.bones}
    assert template.id != rig.id


def test_rig_template_from_rig_deep_copies_bones() -> None:
    rig = make_three_bone_rig()
    template = rig_template_from_rig(rig)
    template.bones[0].x = 999.0
    assert rig.bones[0].x != 999.0


def test_instantiate_rig_template_mints_fresh_ids_for_rig_and_every_bone() -> None:
    rig = make_three_bone_rig()
    template = rig_template_from_rig(rig)

    instance = instantiate_rig_template(template, 100.0, 200.0)

    assert instance.id != template.id
    assert len(instance.bones) == len(template.bones)
    template_ids = {b.id for b in template.bones}
    instance_ids = {b.id for b in instance.bones}
    assert template_ids.isdisjoint(instance_ids)


def test_instantiate_rig_template_remaps_parent_child_relationships() -> None:
    rig = make_three_bone_rig()
    template = rig_template_from_rig(rig)

    instance = instantiate_rig_template(template, 0.0, 0.0)

    new_root = find_bone_in_list(instance.bones, instance.root_bone_id)
    assert new_root.parent_id is None
    mid = next(b for b in instance.bones if b.name == "mid")
    leaf = next(b for b in instance.bones if b.name == "leaf")
    assert mid.parent_id == new_root.id
    assert leaf.parent_id == mid.id


def test_instantiate_rig_template_places_root_at_given_world_position() -> None:
    rig = make_three_bone_rig()
    template = rig_template_from_rig(rig)

    instance = instantiate_rig_template(template, 123.0, 456.0)

    new_root = find_bone_in_list(instance.bones, instance.root_bone_id)
    assert (new_root.x, new_root.y) == pytest.approx((123.0, 456.0))


def test_instantiate_rig_template_preserves_non_root_local_pose() -> None:
    rig = make_three_bone_rig()
    template = rig_template_from_rig(rig)
    original_mid = next(b for b in template.bones if b.name == "mid")

    instance = instantiate_rig_template(template, 500.0, 500.0)

    new_mid = next(b for b in instance.bones if b.name == "mid")
    assert (new_mid.x, new_mid.y) == pytest.approx((original_mid.x, original_mid.y))
    assert (new_mid.pivot_x, new_mid.pivot_y) == (original_mid.pivot_x, original_mid.pivot_y)
    assert (new_mid.attach_x, new_mid.attach_y) == (original_mid.attach_x, original_mid.attach_y)


def test_instantiate_rig_template_result_validates() -> None:
    rig = make_three_bone_rig()
    template = rig_template_from_rig(rig)
    instance = instantiate_rig_template(template, 0.0, 0.0)

    from pivotcut.domain.rig import validate_rig

    validate_rig(instance, [make_asset()])  # must not raise


# -- validate_rig_template ------------------------------------------------------------


def test_validate_rig_template_accepts_a_valid_template() -> None:
    asset = make_asset()
    template = RigTemplate(id="t1", name="T", root_bone_id="root", bones=[make_bone("root", None)])
    validate_rig_template(template, [asset])  # must not raise


def test_validate_rig_template_rejects_multiple_roots() -> None:
    asset = make_asset()
    template = RigTemplate(
        id="t1", name="T", root_bone_id="a", bones=[make_bone("a", None), make_bone("b", None)]
    )
    with pytest.raises(RigValidationError):
        validate_rig_template(template, [asset])


def test_validate_rig_template_rejects_cycle() -> None:
    asset = make_asset()
    template = RigTemplate(
        id="t1", name="T", root_bone_id="a", bones=[make_bone("a", "b"), make_bone("b", "a")]
    )
    with pytest.raises(RigValidationError):
        validate_rig_template(template, [asset])


def test_validate_rig_template_rejects_missing_asset_reference() -> None:
    known_asset = make_asset("known")
    template = RigTemplate(
        id="t1", name="T", root_bone_id="a", bones=[make_bone("a", None, asset_id="unknown")]
    )
    with pytest.raises(RigValidationError):
        validate_rig_template(template, [known_asset])
