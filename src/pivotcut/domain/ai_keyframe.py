"""Pure application of an AI-generated ``KeyframeState`` onto scene data.

No Qt imports here (same rule as every other ``domain`` module) and no
network/AI-provider imports either — this module only knows how to turn a
plain :class:`~pivotcut.domain.models.KeyframeState` into concrete edits on
a deep copy of an existing :class:`~pivotcut.domain.models.Frame`. It never
looks up assets by name, never talks to an LLM, and never mutates the
``Frame``/``Project`` it was handed — see :func:`build_frame_from_keyframe`.

A bone/layer id the keyframe doesn't mention is left exactly as it was on
the base frame (every mapping on ``KeyframeState`` is a *partial* update),
and an id the keyframe *does* mention but that doesn't exist on the base
frame is silently ignored rather than raising — an AI response referencing
a stale/unknown id must never crash animation playback, the same
never-crash-on-missing-reference philosophy already used for missing PNG
assets elsewhere in this app (see ``services.rig_template_io``).
"""

from __future__ import annotations

import copy
import uuid

from pivotcut.domain.camera import Camera
from pivotcut.domain.layer import Layer
from pivotcut.domain.models import CameraState, Frame, KeyframeState
from pivotcut.domain.rig import Bone


#: ``environment_transforms`` inner-dict keys understood on a Layer. Any
#: other key is ignored rather than raising — see module docstring. Public
#: (not ``_``-prefixed): ``services.ai_animator`` reuses this exact set to
#: build the matching JSON Schema, so the two can never drift apart.
LAYER_TRANSFORM_FIELDS = frozenset({"x", "y", "rotation", "scale_x", "scale_y", "z_depth"})


def _all_bones(frame: Frame) -> list[Bone]:
    return [bone for rig in frame.rigs for bone in rig.bones]


def apply_bone_rotations(frame: Frame, bone_rotations: dict[str, float]) -> None:
    """Set ``bone.rotation`` for every bone id present in ``bone_rotations``."""
    bones_by_id = {bone.id: bone for bone in _all_bones(frame)}
    for bone_id, rotation in bone_rotations.items():
        bone = bones_by_id.get(bone_id)
        if bone is not None:
            bone.rotation = rotation


def apply_bone_positions(frame: Frame, bone_positions: dict[str, dict[str, float]]) -> None:
    """Set ``bone.x``/``bone.y`` for every bone id present in ``bone_positions``.

    Deliberately does not touch ``attach_x``/``attach_y`` — this is a pose
    edit (like a canvas drag), not a structural re-attach; see
    ``domain.rig.Bone``'s docstring.
    """
    bones_by_id = {bone.id: bone for bone in _all_bones(frame)}
    for bone_id, position in bone_positions.items():
        bone = bones_by_id.get(bone_id)
        if bone is None:
            continue
        if "x" in position:
            bone.x = position["x"]
        if "y" in position:
            bone.y = position["y"]


def apply_asset_swaps(frame: Frame, asset_swaps: dict[str, str]) -> None:
    """Set ``bone.asset_id`` for every bone id present in ``asset_swaps``.

    Does not validate that the target asset id exists in the project — see
    :func:`build_frame_from_keyframe` for where that validation happens, so
    callers can choose whether an unresolved swap is fatal or silently
    dropped for this frame.
    """
    bones_by_id = {bone.id: bone for bone in _all_bones(frame)}
    for bone_id, asset_id in asset_swaps.items():
        bone = bones_by_id.get(bone_id)
        if bone is not None:
            bone.asset_id = asset_id


def apply_environment_transforms(frame: Frame, environment_transforms: dict[str, dict[str, float]]) -> None:
    """Apply a partial field update to every layer id present in ``environment_transforms``."""
    layers_by_id = {layer.id: layer for layer in frame.layers}
    for layer_id, transform in environment_transforms.items():
        layer = layers_by_id.get(layer_id)
        if layer is None:
            continue
        for name, value in transform.items():
            if name in LAYER_TRANSFORM_FIELDS:
                setattr(layer, name, value)


def apply_camera_state(frame: Frame, camera_state: CameraState) -> None:
    """Overwrite ``frame.camera`` with ``camera_state`` (all three fields)."""
    frame.camera = Camera(x=camera_state.x, y=camera_state.y, zoom=camera_state.zoom)


def apply_keyframe_to_frame(frame: Frame, keyframe: KeyframeState) -> None:
    """Apply every part of ``keyframe`` onto ``frame`` in place.

    Order doesn't matter between the four mapping fields — each only ever
    touches its own kind of id (bone vs. layer) — but rotations/positions
    are applied before asset swaps so a swap can never accidentally be
    shadowed by a later positional update meant for the pre-swap bone.
    """
    apply_bone_rotations(frame, keyframe.bone_rotations)
    apply_bone_positions(frame, keyframe.bone_positions)
    apply_asset_swaps(frame, keyframe.asset_swaps)
    apply_environment_transforms(frame, keyframe.environment_transforms)
    if keyframe.camera is not None:
        apply_camera_state(frame, keyframe.camera)


def build_frame_from_keyframe(base_frame: Frame, keyframe: KeyframeState, label: str = "") -> Frame:
    """Return a brand-new ``Frame`` (fresh id) built by deep-copying
    ``base_frame`` and applying ``keyframe`` on top of the copy.

    Never mutates ``base_frame``. This is the one function
    ``services.ai_animator``/the AI animation UI worker should use to turn
    each ``KeyframeState`` of an AI response into an actual timeline frame
    — every other function in this module is a lower-level building block
    it composes.
    """
    new_frame = copy.deepcopy(base_frame)
    new_frame.id = str(uuid.uuid4())
    new_frame.label = label
    apply_keyframe_to_frame(new_frame, keyframe)
    return new_frame


def resolve_keyframe_state(frame: Frame) -> KeyframeState:
    """Extract a *dense* ``KeyframeState`` snapshot of ``frame``: every
    bone's rotation/position/current asset, every layer's full transform,
    and the camera — not just the properties an author/AI happened to
    mention (contrast with the sparse, partial-update ``KeyframeState``s
    ``services.ai_animator`` produces).

    This is the starting point for interpolating between two *real*
    timeline frames — see
    ``services.playback_engine.PlaybackEngine.get_interpolated_frame``,
    which resolves both its ``start``/``end`` frames this way before
    calling ``get_interpolated_state``. Bone/layer ids only line up
    meaningfully between two frames that represent the same character/
    layer over time (true for frames created via "New Frame", which
    duplicates the previous frame's rigs/layers — see ``domain.timeline``);
    for anything else, mismatched ids simply pass through unchanged
    wherever they're interpolated (see ``services.playback_engine``), same
    never-crash guarantee as the rest of this module.

    ``frame_index`` on the result is always ``0`` — meaningless for a
    resolved snapshot, since it isn't the AI-authored keyframe ordinal;
    callers needing the real pose index already have ``frame`` itself.
    """
    bone_rotations: dict[str, float] = {}
    bone_positions: dict[str, dict[str, float]] = {}
    asset_swaps: dict[str, str] = {}
    for rig in frame.rigs:
        for bone in rig.bones:
            bone_rotations[bone.id] = bone.rotation
            bone_positions[bone.id] = {"x": bone.x, "y": bone.y}
            asset_swaps[bone.id] = bone.asset_id

    environment_transforms: dict[str, dict[str, float]] = {
        layer.id: {field_name: getattr(layer, field_name) for field_name in LAYER_TRANSFORM_FIELDS}
        for layer in frame.layers
    }

    return KeyframeState(
        frame_index=0,
        bone_rotations=bone_rotations,
        bone_positions=bone_positions,
        asset_swaps=asset_swaps,
        environment_transforms=environment_transforms,
        camera=CameraState(x=frame.camera.x, y=frame.camera.y, zoom=frame.camera.zoom),
        interpolation_map=frame.interpolation_map,
    )
