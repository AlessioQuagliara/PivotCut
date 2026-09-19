"""Pure, Qt-free background/foreground layer model for 2.5D parallax scenes.

See ``domain.camera`` for the parallax math (``parallax_factor``,
``screen_position``, ``layer_screen_matrix``) that positions a layer
relative to the project camera, and ``domain.models`` for the shared
scene-wide coordinate system.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable

from pivotcut.domain.assets import Asset


class LayerValidationError(Exception):
    """Raised by :func:`validate_layer`/:func:`validate_layers` on invalid layer data."""


@dataclass
class Layer:
    """A single background/foreground image plane in world/scene space.

    ``x``/``y``/``rotation``/``scale_x``/``scale_y``/``pivot_x``/``pivot_y``
    compose exactly like a rig root bone (see ``domain.rig.local_matrix``):
    translate to (x, y), rotate around the pivot, scale around the pivot.
    Layers have no parent hierarchy, so this *is* their world transform,
    before the camera/parallax offset is applied on top of it.

    ``z_index`` controls Qt render order only — it has no effect on
    parallax. ``z_depth`` is a *semantic* parallax depth (must be >= 0): 0
    sits on the character plane (moves exactly like the rig), larger values
    move progressively less as the camera pans — see
    ``domain.camera.parallax_factor``.
    """

    id: str
    name: str
    asset_id: str
    x: float
    y: float
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    pivot_x: float = 0.0
    pivot_y: float = 0.0
    z_index: int = 0
    z_depth: float = 0.0
    visible: bool = True
    opacity: float = 1.0


#: layer_type -> (z_index, z_depth) defaults. layer_type only seeds these
#: initial values at creation time — it is not stored on Layer itself, so
#: the model never needs a migration if the set of "kinds" changes later.
_LAYER_TYPE_DEFAULTS: dict[str, tuple[int, float]] = {
    "background": (-100, 3.0),
    "midground": (-50, 1.0),
    "foreground": (100, 0.0),
}

LAYER_TYPES: tuple[str, ...] = tuple(_LAYER_TYPE_DEFAULTS)


def create_layer_from_asset(asset: Asset, layer_type: str, scene_width: float, scene_height: float) -> Layer:
    """Build a layer centered in the scene, named after the asset.

    ``layer_type`` (one of :data:`LAYER_TYPES`) only seeds ``z_index``/
    ``z_depth`` defaults; both remain freely editable afterwards.
    """
    if layer_type not in _LAYER_TYPE_DEFAULTS:
        raise ValueError(f"Unknown layer_type: {layer_type!r} (expected one of {LAYER_TYPES})")
    z_index, z_depth = _LAYER_TYPE_DEFAULTS[layer_type]
    return Layer(
        id=str(uuid.uuid4()),
        name=asset.name,
        asset_id=asset.id,
        x=scene_width / 2,
        y=scene_height / 2,
        pivot_x=asset.width / 2,
        pivot_y=asset.height / 2,
        z_index=z_index,
        z_depth=z_depth,
    )


def validate_layer(layer: Layer, assets: Iterable[Asset]) -> None:
    """Validate one layer's own constraints (not cross-layer id uniqueness).

    Checks: ``asset_id`` exists in ``assets``, ``z_depth >= 0``, ``opacity``
    in [0, 1], and non-zero ``scale_x``/``scale_y``.
    """
    asset_ids = {asset.id for asset in assets}
    if layer.asset_id not in asset_ids:
        raise LayerValidationError(f"Layer {layer.id!r} references missing asset_id {layer.asset_id!r}.")
    if layer.z_depth < 0.0:
        raise LayerValidationError(f"Layer {layer.id!r} has negative z_depth {layer.z_depth}.")
    if not (0.0 <= layer.opacity <= 1.0):
        raise LayerValidationError(f"Layer {layer.id!r} has opacity {layer.opacity} outside [0, 1].")
    if layer.scale_x == 0.0 or layer.scale_y == 0.0:
        raise LayerValidationError(f"Layer {layer.id!r} has a zero scale component.")


def validate_layers(layers: Iterable[Layer], assets: Iterable[Asset]) -> None:
    """Validate every layer in a frame plus id-uniqueness across the collection."""
    layers = list(layers)
    ids = [layer.id for layer in layers]
    if len(ids) != len(set(ids)):
        raise LayerValidationError("Duplicate layer ids in the same frame.")
    for layer in layers:
        validate_layer(layer, assets)
