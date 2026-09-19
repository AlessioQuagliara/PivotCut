"""JSON (de)serialization for :class:`~pivotcut.domain.models.KeyframeState`,
including its Smooth Animation ``interpolation_map``.

This is a distinct concern from ``services/project_io.py`` (the real
``Project``/``.pivotcut.json`` file format, unaffected by this module) and
from ``services/ai_animator.py`` (which validates *untrusted* JSON fresh off
an LLM response, raising ``AIAnimatorResponseError``). ``RigSerializer``
here is the app's own canonical, round-trip-safe format for persisting a
``KeyframeState`` it already trusts — e.g. saving/exporting a generated
animation sequence, or embedding one in project-adjacent data — and raises
``SerializationError`` instead.

Legacy-safe by design: a dict saved before Smooth Animation existed has no
``interpolation_map``/``control_points`` at all, and :meth:`RigSerializer.from_dict`
loads it with every property defaulting to :data:`LEGACY_FALLBACK_INTERPOLATION_TYPE`
(``LINEAR``) rather than the feature's own ``CUBIC_SPLINE`` default — old data
keeps the plain, predictable motion it was authored with instead of silently
gaining new easing.
"""

from __future__ import annotations

from typing import Any

from pivotcut.domain.models import CameraState, InterpolationSettings, InterpolationType, KeyframeState

#: See module docstring: the safe fallback for interpolation data that
#: predates this feature, deliberately not `InterpolationSettings`'s own
#: (newer, eased) `CUBIC_SPLINE` default.
LEGACY_FALLBACK_INTERPOLATION_TYPE = InterpolationType.LINEAR


class SerializationError(Exception):
    """Raised on malformed ``KeyframeState`` JSON data."""


def interpolation_settings_to_dict(settings: InterpolationSettings) -> dict[str, Any]:
    """Public: also reused by ``services.project_io`` for ``Frame.interpolation_map``,
    so the real project file format and this module's own format never drift apart."""
    return {"type": settings.type.value, "control_points": list(settings.control_points)}


def interpolation_settings_from_dict(data: Any) -> InterpolationSettings:
    if not isinstance(data, dict):
        raise SerializationError(f"interpolation settings: expected an object, got {type(data).__name__}")

    raw_type = data.get("type")
    if raw_type is None:
        interpolation_type = LEGACY_FALLBACK_INTERPOLATION_TYPE
    else:
        try:
            interpolation_type = InterpolationType(raw_type)
        except ValueError as exc:
            raise SerializationError(f"interpolation settings: unknown type {raw_type!r}") from exc

    raw_points = data.get("control_points")
    if raw_points is None:
        control_points = InterpolationSettings().control_points
    else:
        try:
            control_points = tuple(float(value) for value in raw_points)
        except (TypeError, ValueError) as exc:
            raise SerializationError(f"interpolation settings: invalid control_points {raw_points!r}") from exc
        if len(control_points) != 4:
            raise SerializationError(
                f"interpolation settings: control_points must have exactly 4 values, got {len(control_points)}"
            )

    return InterpolationSettings(type=interpolation_type, control_points=control_points)


def _camera_state_to_dict(camera: CameraState) -> dict[str, float]:
    return {"x": camera.x, "y": camera.y, "zoom": camera.zoom}


def _camera_state_from_dict(data: Any) -> CameraState:
    if not isinstance(data, dict):
        raise SerializationError(f"camera: expected an object, got {type(data).__name__}")
    try:
        return CameraState(x=float(data["x"]), y=float(data["y"]), zoom=float(data["zoom"]))
    except KeyError as exc:
        raise SerializationError(f"camera: missing required field {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise SerializationError(f"camera: {exc}") from exc


class RigSerializer:
    """(De)serializes a ``KeyframeState`` — interpolation_map included — to/from
    this app's JSON dict format. Stateless; every method is a plain function."""

    @staticmethod
    def to_dict(state: KeyframeState) -> dict[str, Any]:
        """A plain, ``json.dumps``-ready dict."""
        return {
            "frame_index": state.frame_index,
            "bone_rotations": dict(state.bone_rotations),
            "bone_positions": {bone_id: dict(position) for bone_id, position in state.bone_positions.items()},
            "asset_swaps": dict(state.asset_swaps),
            "environment_transforms": {
                layer_id: dict(fields) for layer_id, fields in state.environment_transforms.items()
            },
            "camera": _camera_state_to_dict(state.camera) if state.camera is not None else None,
            "interpolation_map": {
                property_id: interpolation_settings_to_dict(settings)
                for property_id, settings in state.interpolation_map.items()
            },
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> KeyframeState:
        """The inverse of :meth:`to_dict`. Every field is absent-safe (a
        pre-Smooth-Animation project, or any field simply omitted) — see
        the module docstring for exactly what each one defaults to.
        """
        if not isinstance(data, dict):
            raise SerializationError(f"keyframe: expected an object, got {type(data).__name__}")

        try:
            frame_index = int(data["frame_index"])
        except KeyError as exc:
            raise SerializationError("keyframe: missing required field 'frame_index'") from exc
        except (TypeError, ValueError) as exc:
            raise SerializationError(f"keyframe.frame_index: {exc}") from exc

        camera_data = data.get("camera")
        camera = _camera_state_from_dict(camera_data) if camera_data is not None else None

        raw_interpolation_map = data.get("interpolation_map") or {}
        if not isinstance(raw_interpolation_map, dict):
            raise SerializationError("keyframe.interpolation_map: expected an object")
        interpolation_map = {
            property_id: interpolation_settings_from_dict(settings_data)
            for property_id, settings_data in raw_interpolation_map.items()
        }

        return KeyframeState(
            frame_index=frame_index,
            bone_rotations=dict(data.get("bone_rotations") or {}),
            bone_positions={bone_id: dict(position) for bone_id, position in (data.get("bone_positions") or {}).items()},
            asset_swaps=dict(data.get("asset_swaps") or {}),
            environment_transforms={
                layer_id: dict(fields) for layer_id, fields in (data.get("environment_transforms") or {}).items()
            },
            camera=camera,
            interpolation_map=interpolation_map,
        )
