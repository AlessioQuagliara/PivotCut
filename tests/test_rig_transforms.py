from __future__ import annotations

import pytest

from pivotcut.domain.assets import Asset
from pivotcut.domain.rig import (
    Bone,
    Rig,
    RigValidationError,
    children_of,
    create_rig_from_asset,
    find_bone,
    validate_rig,
    world_transform_for_bone,
    world_transforms,
)


def make_bone(
    id: str,
    parent_id: str | None,
    x: float = 0.0,
    y: float = 0.0,
    rotation: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    pivot_x: float = 0.0,
    pivot_y: float = 0.0,
    asset_id: str = "asset-1",
    opacity: float = 1.0,
) -> Bone:
    return Bone(
        id=id,
        name=id,
        parent_id=parent_id,
        asset_id=asset_id,
        x=x,
        y=y,
        rotation=rotation,
        scale_x=scale_x,
        scale_y=scale_y,
        pivot_x=pivot_x,
        pivot_y=pivot_y,
        opacity=opacity,
    )


def make_asset(id: str = "asset-1") -> Asset:
    return Asset(id=id, name="part", source_path="/tmp/part.png", relative_path=None, width=64, height=64)


# -- world transform composition -----------------------------------------------


def test_root_transform_is_its_own_local_translation() -> None:
    root = make_bone("root", None, x=100.0, y=50.0)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[root])

    world = world_transform_for_bone(rig, "root")

    assert world.apply_point(0.0, 0.0) == pytest.approx((100.0, 50.0))


def test_child_inherits_parent_translation() -> None:
    root = make_bone("root", None, x=100.0, y=50.0)
    child = make_bone("child", "root", x=10.0, y=20.0)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[root, child])

    world = world_transform_for_bone(rig, "child")

    assert world.apply_point(0.0, 0.0) == pytest.approx((110.0, 70.0))


def test_child_inherits_parent_rotation() -> None:
    # Root rotates 90 degrees clockwise (Qt/screen convention) around (0, 0).
    root = make_bone("root", None, x=0.0, y=0.0, rotation=90.0)
    # Child sits 10 units to the right of the parent's local origin.
    child = make_bone("child", "root", x=10.0, y=0.0)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[root, child])

    world = world_transform_for_bone(rig, "child")

    # "Right" rotated 90 degrees clockwise in a Y-down system becomes "down".
    wx, wy = world.apply_point(0.0, 0.0)
    assert wx == pytest.approx(0.0, abs=1e-9)
    assert wy == pytest.approx(10.0)


def test_child_inherits_parent_scale() -> None:
    root = make_bone("root", None, x=0.0, y=0.0, scale_x=2.0, scale_y=2.0)
    child = make_bone("child", "root", x=10.0, y=0.0)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[root, child])

    world = world_transform_for_bone(rig, "child")

    assert world.apply_point(0.0, 0.0) == pytest.approx((20.0, 0.0))


def test_rotation_happens_around_the_pivot_not_the_origin() -> None:
    # A bone rotated 180 degrees around a pivot offset from its origin should
    # end up on the opposite side of that pivot, not mirrored around (0, 0).
    bone = make_bone("root", None, x=0.0, y=0.0, rotation=180.0, pivot_x=10.0, pivot_y=0.0)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[bone])

    world = world_transform_for_bone(rig, "root")

    # The pivot point itself must stay fixed under a pure rotation about it.
    pivot_world = world.apply_point(10.0, 0.0)
    assert pivot_world == pytest.approx((10.0, 0.0))
    # A point at the local origin (10 units left of the pivot) ends up
    # 10 units to the right of the pivot after a 180 degree turn.
    origin_world = world.apply_point(0.0, 0.0)
    assert origin_world == pytest.approx((20.0, 0.0), abs=1e-9)


def test_three_level_composition_accumulates_translation() -> None:
    root = make_bone("root", None, x=100.0, y=100.0)
    mid = make_bone("mid", "root", x=10.0, y=0.0)
    leaf = make_bone("leaf", "mid", x=0.0, y=5.0)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[root, mid, leaf])

    transforms = world_transforms(rig)

    assert transforms["root"].apply_point(0.0, 0.0) == pytest.approx((100.0, 100.0))
    assert transforms["mid"].apply_point(0.0, 0.0) == pytest.approx((110.0, 100.0))
    assert transforms["leaf"].apply_point(0.0, 0.0) == pytest.approx((110.0, 105.0))


def test_world_transforms_computes_every_bone_regardless_of_traversal_order() -> None:
    root = make_bone("root", None, x=1.0, y=2.0)
    child = make_bone("child", "root", x=3.0, y=4.0)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[child, root])  # child listed first

    transforms = world_transforms(rig)

    assert set(transforms.keys()) == {"root", "child"}
    assert transforms["child"].apply_point(0.0, 0.0) == pytest.approx((4.0, 6.0))


# -- find_bone / children_of ----------------------------------------------------


def test_find_bone_returns_none_when_missing() -> None:
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[make_bone("root", None)])
    assert find_bone(rig, "root") is not None
    assert find_bone(rig, "nope") is None


def test_children_of_returns_direct_children_only() -> None:
    root = make_bone("root", None)
    child_a = make_bone("a", "root")
    child_b = make_bone("b", "root")
    grandchild = make_bone("c", "a")
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[root, child_a, child_b, grandchild])

    children = {bone.id for bone in children_of(rig, "root")}

    assert children == {"a", "b"}


# -- validate_rig -----------------------------------------------------------------


def test_validate_rig_accepts_a_valid_rig() -> None:
    asset = make_asset()
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[make_bone("root", None, asset_id=asset.id)])
    validate_rig(rig, [asset])  # must not raise


def test_validate_rig_detects_cycle_among_non_root_bones() -> None:
    asset = make_asset()
    root = make_bone("root", None, asset_id=asset.id)
    a = make_bone("a", "b", asset_id=asset.id)
    b = make_bone("b", "a", asset_id=asset.id)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[root, a, b])

    with pytest.raises(RigValidationError):
        validate_rig(rig, [asset])


def test_world_transforms_also_detects_cycles() -> None:
    asset = make_asset()
    root = make_bone("root", None, asset_id=asset.id)
    a = make_bone("a", "b", asset_id=asset.id)
    b = make_bone("b", "a", asset_id=asset.id)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[root, a, b])

    with pytest.raises(RigValidationError):
        world_transforms(rig)


def test_validate_rig_detects_missing_parent() -> None:
    asset = make_asset()
    root = make_bone("root", None, asset_id=asset.id)
    orphan = make_bone("orphan", "does-not-exist", asset_id=asset.id)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[root, orphan])

    with pytest.raises(RigValidationError):
        validate_rig(rig, [asset])


def test_validate_rig_detects_multiple_roots() -> None:
    asset = make_asset()
    root_a = make_bone("root_a", None, asset_id=asset.id)
    root_b = make_bone("root_b", None, asset_id=asset.id)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root_a", bones=[root_a, root_b])

    with pytest.raises(RigValidationError):
        validate_rig(rig, [asset])


def test_validate_rig_detects_root_bone_id_mismatch() -> None:
    asset = make_asset()
    actual_root = make_bone("root", None, asset_id=asset.id)
    child = make_bone("child", "root", asset_id=asset.id)
    rig = Rig(id="rig1", name="Rig", root_bone_id="child", bones=[actual_root, child])

    with pytest.raises(RigValidationError):
        validate_rig(rig, [asset])


def test_validate_rig_detects_root_bone_id_not_found() -> None:
    asset = make_asset()
    root = make_bone("root", None, asset_id=asset.id)
    rig = Rig(id="rig1", name="Rig", root_bone_id="does-not-exist", bones=[root])

    with pytest.raises(RigValidationError):
        validate_rig(rig, [asset])


def test_validate_rig_detects_missing_asset_reference() -> None:
    known_asset = make_asset("known")
    root = make_bone("root", None, asset_id="unknown-asset")
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[root])

    with pytest.raises(RigValidationError):
        validate_rig(rig, [known_asset])


@pytest.mark.parametrize("opacity", [-0.1, 1.1, 2.0])
def test_validate_rig_rejects_opacity_outside_unit_range(opacity: float) -> None:
    asset = make_asset()
    root = make_bone("root", None, asset_id=asset.id, opacity=opacity)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[root])

    with pytest.raises(RigValidationError):
        validate_rig(rig, [asset])


@pytest.mark.parametrize(("scale_x", "scale_y"), [(0.0, 1.0), (1.0, 0.0), (0.0, 0.0)])
def test_validate_rig_rejects_zero_scale(scale_x: float, scale_y: float) -> None:
    asset = make_asset()
    root = make_bone("root", None, asset_id=asset.id, scale_x=scale_x, scale_y=scale_y)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[root])

    with pytest.raises(RigValidationError):
        validate_rig(rig, [asset])


def test_validate_rig_detects_duplicate_bone_ids() -> None:
    asset = make_asset()
    root = make_bone("root", None, asset_id=asset.id)
    duplicate = make_bone("root", None, asset_id=asset.id)
    rig = Rig(id="rig1", name="Rig", root_bone_id="root", bones=[root, duplicate])

    with pytest.raises(RigValidationError):
        validate_rig(rig, [asset])


# -- create_rig_from_asset --------------------------------------------------------


def test_create_rig_from_asset_centers_root_bone_in_scene() -> None:
    asset = Asset(id="a1", name="Body", source_path="/tmp/body.png", relative_path=None, width=200, height=100)

    rig = create_rig_from_asset(asset, scene_width=1920.0, scene_height=1080.0)

    assert len(rig.bones) == 1
    root = rig.bones[0]
    assert root.id == rig.root_bone_id
    assert root.parent_id is None
    assert root.asset_id == "a1"
    assert root.x == pytest.approx(960.0)
    assert root.y == pytest.approx(540.0)
    assert root.pivot_x == pytest.approx(100.0)
    assert root.pivot_y == pytest.approx(50.0)
    assert rig.name == "Body"
    assert root.name == "Body"
