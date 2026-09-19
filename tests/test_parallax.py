from __future__ import annotations

import pytest

from pivotcut.domain.assets import Asset
from pivotcut.domain.camera import (
    MAX_ZOOM,
    MIN_ZOOM,
    Camera,
    CameraValidationError,
    character_plane_matrix,
    layer_local_matrix,
    layer_screen_matrix,
    parallax_factor,
    screen_position,
    validate_camera,
    zoom_matrix,
)
from pivotcut.domain.layer import (
    LayerValidationError,
    create_layer_from_asset,
    validate_layer,
    validate_layers,
)


def make_layer(
    id: str = "layer-1",
    x: float = 0.0,
    y: float = 0.0,
    rotation: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    pivot_x: float = 0.0,
    pivot_y: float = 0.0,
    z_depth: float = 0.0,
    opacity: float = 1.0,
    asset_id: str = "asset-1",
):
    from pivotcut.domain.layer import Layer

    return Layer(
        id=id,
        name=id,
        asset_id=asset_id,
        x=x,
        y=y,
        rotation=rotation,
        scale_x=scale_x,
        scale_y=scale_y,
        pivot_x=pivot_x,
        pivot_y=pivot_y,
        z_depth=z_depth,
        opacity=opacity,
    )


def make_asset(id: str = "asset-1") -> Asset:
    return Asset(id=id, name="bg", source_path="/tmp/bg.png", relative_path=None, width=400, height=200)


# -- parallax_factor: the three documented examples ----------------------------


def test_parallax_factor_zero_depth_is_full_factor() -> None:
    assert parallax_factor(0.0) == pytest.approx(1.0)


def test_parallax_factor_depth_one_is_half_factor() -> None:
    assert parallax_factor(1.0) == pytest.approx(0.5)


def test_parallax_factor_depth_three_is_quarter_factor() -> None:
    assert parallax_factor(3.0) == pytest.approx(0.25)


# -- screen_position with a non-zero camera ------------------------------------


def test_screen_position_with_nonzero_camera_and_depth() -> None:
    camera = Camera(x=100.0, y=50.0, zoom=1.0)

    result = screen_position(200.0, 300.0, camera, z_depth=1.0)

    # factor(1) = 0.5 -> screen = world - camera * 0.5
    assert result == pytest.approx((150.0, 275.0))


def test_screen_position_at_zero_depth_moves_with_full_camera_offset() -> None:
    camera = Camera(x=100.0, y=50.0, zoom=1.0)

    result = screen_position(200.0, 300.0, camera, z_depth=0.0)

    assert result == pytest.approx((100.0, 250.0))


def test_screen_position_zero_camera_is_identity() -> None:
    camera = Camera(x=0.0, y=0.0, zoom=1.0)

    result = screen_position(42.0, 7.0, camera, z_depth=2.0)

    assert result == pytest.approx((42.0, 7.0))


def test_deeper_layer_moves_less_than_character_plane_for_same_camera_pan() -> None:
    camera = Camera(x=400.0, y=200.0, zoom=1.0)
    world = (1000.0, 500.0)

    character_plane = screen_position(*world, camera, z_depth=0.0)
    distant_layer = screen_position(*world, camera, z_depth=3.0)

    character_delta = world[0] - character_plane[0]
    distant_delta = world[0] - distant_layer[0]

    assert distant_delta == pytest.approx(character_delta * 0.25)


# -- validate_camera --------------------------------------------------------------


def test_validate_camera_accepts_default() -> None:
    validate_camera(Camera())  # must not raise


@pytest.mark.parametrize("zoom", [MIN_ZOOM, 1.0, MAX_ZOOM])
def test_validate_camera_accepts_boundary_zoom(zoom: float) -> None:
    validate_camera(Camera(zoom=zoom))  # must not raise


@pytest.mark.parametrize("zoom", [0.0, -1.0, 0.05, MAX_ZOOM + 0.01, 10.0])
def test_validate_camera_rejects_out_of_range_zoom(zoom: float) -> None:
    with pytest.raises(CameraValidationError):
        validate_camera(Camera(zoom=zoom))


# -- validate_layer / validate_layers ----------------------------------------------


def test_validate_layer_accepts_a_valid_layer() -> None:
    asset = make_asset()
    layer = make_layer(asset_id=asset.id)
    validate_layer(layer, [asset])  # must not raise


def test_validate_layer_rejects_negative_z_depth() -> None:
    asset = make_asset()
    layer = make_layer(asset_id=asset.id, z_depth=-0.1)
    with pytest.raises(LayerValidationError):
        validate_layer(layer, [asset])


def test_validate_layer_rejects_missing_asset() -> None:
    known_asset = make_asset("known")
    layer = make_layer(asset_id="unknown-asset")
    with pytest.raises(LayerValidationError):
        validate_layer(layer, [known_asset])


@pytest.mark.parametrize("opacity", [-0.1, 1.1, 2.0])
def test_validate_layer_rejects_opacity_outside_unit_range(opacity: float) -> None:
    asset = make_asset()
    layer = make_layer(asset_id=asset.id, opacity=opacity)
    with pytest.raises(LayerValidationError):
        validate_layer(layer, [asset])


@pytest.mark.parametrize(("scale_x", "scale_y"), [(0.0, 1.0), (1.0, 0.0)])
def test_validate_layer_rejects_zero_scale(scale_x: float, scale_y: float) -> None:
    asset = make_asset()
    layer = make_layer(asset_id=asset.id, scale_x=scale_x, scale_y=scale_y)
    with pytest.raises(LayerValidationError):
        validate_layer(layer, [asset])


def test_validate_layers_rejects_duplicate_ids() -> None:
    asset = make_asset()
    layer_a = make_layer(id="dup", asset_id=asset.id)
    layer_b = make_layer(id="dup", asset_id=asset.id)
    with pytest.raises(LayerValidationError):
        validate_layers([layer_a, layer_b], [asset])


def test_validate_layers_accepts_distinct_ids_reusing_same_asset() -> None:
    asset = make_asset()
    layer_a = make_layer(id="a", asset_id=asset.id)
    layer_b = make_layer(id="b", asset_id=asset.id)
    validate_layers([layer_a, layer_b], [asset])  # must not raise


# -- layer_local_matrix / layer_screen_matrix ------------------------------------


def test_layer_local_matrix_rotation_happens_around_pivot() -> None:
    layer = make_layer(x=0.0, y=0.0, rotation=180.0, pivot_x=10.0, pivot_y=0.0)

    matrix = layer_local_matrix(layer)

    pivot_world = matrix.apply_point(10.0, 0.0)
    assert pivot_world == pytest.approx((10.0, 0.0))
    origin_world = matrix.apply_point(0.0, 0.0)
    assert origin_world == pytest.approx((20.0, 0.0), abs=1e-9)


def test_layer_screen_matrix_with_neutral_camera_matches_local_matrix() -> None:
    layer = make_layer(x=50.0, y=60.0, rotation=30.0, pivot_x=5.0, pivot_y=5.0)
    neutral_camera = Camera(x=0.0, y=0.0, zoom=1.0)

    local = layer_local_matrix(layer)
    screen = layer_screen_matrix(layer, neutral_camera, scene_width=1920.0, scene_height=1080.0)

    # zoom_matrix at zoom=1.0 anchored at scene center is the identity, and
    # the camera offset at (0, 0) is (0, 0), so screen == local exactly.
    assert screen.apply_point(0.0, 0.0) == pytest.approx(local.apply_point(0.0, 0.0))
    assert screen.apply_point(100.0, 0.0) == pytest.approx(local.apply_point(100.0, 0.0))


def test_layer_screen_matrix_applies_parallax_offset() -> None:
    layer = make_layer(x=1000.0, y=500.0, z_depth=1.0)  # factor 0.5
    camera = Camera(x=400.0, y=200.0, zoom=1.0)

    screen = layer_screen_matrix(layer, camera, scene_width=1920.0, scene_height=1080.0)

    assert screen.apply_point(0.0, 0.0) == pytest.approx((1000.0 - 200.0, 500.0 - 100.0))


# -- character_plane_matrix (what rig bones receive) -----------------------------


def test_character_plane_matrix_offsets_by_full_camera_position() -> None:
    camera = Camera(x=120.0, y=80.0, zoom=1.0)

    matrix = character_plane_matrix(camera, scene_width=1920.0, scene_height=1080.0)

    assert matrix.apply_point(0.0, 0.0) == pytest.approx((-120.0, -80.0))


def test_character_plane_matrix_is_identity_for_default_camera() -> None:
    camera = Camera()

    matrix = character_plane_matrix(camera, scene_width=1920.0, scene_height=1080.0)

    assert matrix.apply_point(500.0, 300.0) == pytest.approx((500.0, 300.0))


# -- zoom_matrix: scales around the output frame's center -----------------------


def test_zoom_matrix_keeps_scene_center_fixed() -> None:
    camera = Camera(zoom=2.0)

    matrix = zoom_matrix(camera, scene_width=1000.0, scene_height=1000.0)

    assert matrix.apply_point(500.0, 500.0) == pytest.approx((500.0, 500.0))


def test_zoom_matrix_scales_distance_from_center() -> None:
    camera = Camera(zoom=2.0)

    matrix = zoom_matrix(camera, scene_width=1000.0, scene_height=1000.0)

    assert matrix.apply_point(0.0, 0.0) == pytest.approx((-500.0, -500.0))


def test_zoom_matrix_identity_at_zoom_one() -> None:
    camera = Camera(zoom=1.0)

    matrix = zoom_matrix(camera, scene_width=1920.0, scene_height=1080.0)

    assert matrix.apply_point(37.0, 91.0) == pytest.approx((37.0, 91.0))


# -- create_layer_from_asset -------------------------------------------------------


@pytest.mark.parametrize(
    ("layer_type", "expected_z_index", "expected_z_depth"),
    [
        ("background", -100, 3.0),
        ("midground", -50, 1.0),
        ("foreground", 100, 0.0),
    ],
)
def test_create_layer_from_asset_applies_type_defaults(
    layer_type: str, expected_z_index: int, expected_z_depth: float
) -> None:
    asset = Asset(id="a1", name="Sky", source_path="/tmp/sky.png", relative_path=None, width=800, height=400)

    layer = create_layer_from_asset(asset, layer_type, scene_width=1920.0, scene_height=1080.0)

    assert layer.z_index == expected_z_index
    assert layer.z_depth == pytest.approx(expected_z_depth)
    assert layer.x == pytest.approx(960.0)
    assert layer.y == pytest.approx(540.0)
    assert layer.pivot_x == pytest.approx(400.0)
    assert layer.pivot_y == pytest.approx(200.0)
    assert layer.name == "Sky"
    assert layer.asset_id == "a1"


def test_create_layer_from_asset_rejects_unknown_type() -> None:
    asset = make_asset()
    with pytest.raises(ValueError):
        create_layer_from_asset(asset, "midair", scene_width=1920.0, scene_height=1080.0)
