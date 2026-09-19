"""Pure, Qt-free camera and 2.5D parallax math.

Camera model: a virtual camera with world position (``x``, ``y``) and a
uniform ``zoom``. It never permanently mutates world position, rotation or
scale of any rig bone or layer — it only produces an additional *screen*
transform that rendering composes on top of each element's own world
transform (see :func:`to_screen_matrix`).

Parallax formula (documented per the project spec, with ``z_depth`` a
semantic depth >= 0, not the Qt ``z_index`` render-order value)::

    parallax_factor = 1.0 / (1.0 + z_depth)
    screen_position  = world_position - camera_position * parallax_factor

Examples:

- ``z_depth = 0`` -> ``parallax_factor = 1.0``: moves exactly like the
  character plane (this is what rig bones use, always).
- ``z_depth = 1`` -> ``parallax_factor = 0.5``: moves half as much as the
  camera pans — a "midground" layer.
- ``z_depth = 3`` -> ``parallax_factor = 0.25``: moves a quarter as much —
  a distant "background" layer.

``camera.zoom`` additionally scales the whole rendered scene (rig and
layers alike) uniformly around the output frame's center — this is the
*project* camera zoom, semantically distinct from the editor's own
navigation zoom (``CanvasView``'s trackpad/wheel zoom), which only affects
how the canvas is displayed and never touches this module or the domain
model.

No 3D perspective, shaders, camera rotation or negative ``z_depth`` in this
MVP.
"""

from __future__ import annotations

from dataclasses import dataclass

from pivotcut.domain.layer import Layer
from pivotcut.domain.rig import Matrix2D

MIN_ZOOM = 0.1
MAX_ZOOM = 5.0


class CameraValidationError(Exception):
    """Raised by :func:`validate_camera` on an invalid Camera."""


@dataclass
class Camera:
    """A virtual camera: world position + zoom, snapshot per frame."""

    x: float = 0.0
    y: float = 0.0
    zoom: float = 1.0


def validate_camera(camera: Camera) -> None:
    """Raise ``CameraValidationError`` unless ``zoom`` is in [MIN_ZOOM, MAX_ZOOM]."""
    if not (MIN_ZOOM <= camera.zoom <= MAX_ZOOM):
        raise CameraValidationError(
            f"camera.zoom {camera.zoom} outside allowed range [{MIN_ZOOM}, {MAX_ZOOM}]."
        )


def parallax_factor(z_depth: float) -> float:
    """1.0 at z_depth=0 (character plane), 0.5 at 1, 0.25 at 3, etc."""
    return 1.0 / (1.0 + z_depth)


def camera_offset(camera: Camera, z_depth: float) -> tuple[float, float]:
    """``-camera_position * parallax_factor(z_depth)`` as an (x, y) delta."""
    factor = parallax_factor(z_depth)
    return (-camera.x * factor, -camera.y * factor)


def screen_position(world_x: float, world_y: float, camera: Camera, z_depth: float) -> tuple[float, float]:
    """``world_position - camera_position * parallax_factor(z_depth)``."""
    offset_x, offset_y = camera_offset(camera, z_depth)
    return (world_x + offset_x, world_y + offset_y)


def zoom_matrix(camera: Camera, scene_width: float, scene_height: float) -> Matrix2D:
    """``camera.zoom`` scales the whole scene around the output frame's center."""
    return Matrix2D.scale_about(scene_width / 2.0, scene_height / 2.0, camera.zoom, camera.zoom)


def to_screen_matrix(
    world_matrix: Matrix2D,
    camera: Camera,
    z_depth: float,
    scene_width: float,
    scene_height: float,
) -> Matrix2D:
    """Compose a world-space matrix with the camera's parallax offset + zoom.

    Used for both rig bones (``z_depth=0``, via :func:`character_plane_matrix`)
    and layers (via :func:`layer_screen_matrix`).
    """
    offset_x, offset_y = camera_offset(camera, z_depth)
    return world_matrix.then(Matrix2D.translation(offset_x, offset_y)).then(
        zoom_matrix(camera, scene_width, scene_height)
    )


def character_plane_matrix(camera: Camera, scene_width: float, scene_height: float) -> Matrix2D:
    """The extra screen transform rig bones receive on top of their FK world transform.

    Bones live on the character plane: z_depth = 0, parallax_factor = 1.0.
    """
    return to_screen_matrix(Matrix2D.identity(), camera, 0.0, scene_width, scene_height)


def layer_local_matrix(layer: Layer) -> Matrix2D:
    """A layer's own world transform, before the camera/parallax offset.

    Identical composition to a rig root bone's local transform: translate
    to (x, y), rotate around the pivot, scale around the pivot.
    """
    anchor_x = layer.x + layer.pivot_x
    anchor_y = layer.y + layer.pivot_y
    return (
        Matrix2D.translation(layer.x, layer.y)
        .then(Matrix2D.rotation_about(anchor_x, anchor_y, layer.rotation))
        .then(Matrix2D.scale_about(anchor_x, anchor_y, layer.scale_x, layer.scale_y))
    )


def layer_screen_matrix(layer: Layer, camera: Camera, scene_width: float, scene_height: float) -> Matrix2D:
    """A layer's final screen transform: its own transform plus camera parallax/zoom."""
    return to_screen_matrix(layer_local_matrix(layer), camera, layer.z_depth, scene_width, scene_height)
