"""JSON persistence for :class:`~pivotcut.domain.models.Project`.

The JSON schema is versioned (``format_version``) from day one. Milestone 2
added an asset registry (``Project.assets``) and per-frame rigs
(``Frame.rigs``); Milestone 3 adds per-frame ``camera``/``layers`` the same
way. All of these are optional/absent-safe on load so Milestone 1 and 2
files keep loading unchanged (``format_version`` stays 1: additive,
backward-compatible evolutions, not breaking ones):

- Milestone 1 files have no ``assets``, and frames have no ``rigs``,
  ``camera`` or ``layers`` -> ``assets=[]``, ``rigs=[]``, ``camera=Camera()``,
  ``layers=[]``.
- Milestone 2 files have ``assets``/``rigs`` but no ``camera``/``layers`` ->
  ``camera=Camera()``, ``layers=[]``.
- Milestone 6A adds ``Project.rig_templates`` (the Rig Library) and two new
  ``Bone`` fields, ``attach_x``/``attach_y``. Files from any earlier
  milestone have neither: ``rig_templates`` defaults to ``[]``, and each
  bone's ``attach_x``/``attach_y`` default to its *current* anchor
  (``x + pivot_x``/``y + pivot_y``) rather than 0.0, so an old rig opens in
  the new Character Rig Builder with a consistent, non-surprising attach
  point instead of a wrong one at the origin (see ``_bone_from_dict``).
- Smooth Animation adds ``Project.smooth_animation_enabled`` (default
  ``False``) and a per-``Frame`` ``interpolation_map``. Files from any
  earlier version have neither -> ``smooth_animation_enabled=False``,
  ``interpolation_map={}`` — every existing project keeps its exact
  original hard-cut playback/export until a user explicitly turns Smooth
  Animation on (see ``domain.interpolation``/``services.playback_engine``).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any

from pivotcut.domain.assets import Asset
from pivotcut.domain.camera import Camera, CameraValidationError, validate_camera
from pivotcut.domain.layer import Layer, LayerValidationError, validate_layers
from pivotcut.domain.models import Frame, InterpolationSettings, Project, SceneSettings
from pivotcut.domain.playback import PlaybackValidationError, validate_playback_settings
from pivotcut.domain.rig import (
    Bone,
    Rig,
    RigTemplate,
    RigValidationError,
    validate_rig,
    validate_rig_template,
)
from pivotcut.services.serialization import SerializationError, interpolation_settings_from_dict

SUPPORTED_FORMAT_VERSION = 1

PROJECT_FILE_SUFFIX = ".pivotcut.json"


class ProjectIOError(Exception):
    """Base class for all project load/save errors."""


class InvalidProjectFileError(ProjectIOError):
    """The file could not be read or is not valid JSON."""


class UnsupportedProjectFormatError(ProjectIOError):
    """The file's ``format_version`` is missing or not supported."""


class IncompleteProjectDataError(ProjectIOError):
    """The file is valid JSON but is missing required project fields."""


def _json_default(value: Any) -> Any:
    """``asdict(project)`` leaves any non-dataclass/list/tuple/dict field as
    a live Python object — the one case that matters here is
    ``Frame.interpolation_map``'s ``InterpolationSettings.type``, an
    ``InterpolationType`` member, which ``json.dumps`` can't serialize on
    its own. Encoded as its plain string ``.value`` (e.g. ``"cubic_spline"``),
    matching ``services.serialization.interpolation_settings_to_dict``.
    """
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def save_project(project: Project, path: Path) -> None:
    """Write ``project`` to ``path`` as pretty-printed JSON."""
    data = asdict(project)
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    except OSError as exc:
        raise InvalidProjectFileError(f"Cannot write project file: {path}") from exc


def load_project(path: Path) -> Project:
    """Read and validate a project from ``path``.

    Raises:
        InvalidProjectFileError: file missing/unreadable or not valid JSON.
        UnsupportedProjectFormatError: ``format_version`` missing or unknown.
        IncompleteProjectDataError: required fields missing or malformed.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InvalidProjectFileError(f"Cannot read project file: {path}") from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise InvalidProjectFileError(f"Project file is not valid JSON: {path}") from exc

    if not isinstance(data, dict):
        raise IncompleteProjectDataError("Project file must contain a JSON object.")

    format_version = data.get("format_version")
    if format_version != SUPPORTED_FORMAT_VERSION:
        raise UnsupportedProjectFormatError(
            f"Unsupported project format_version: {format_version!r} "
            f"(expected {SUPPORTED_FORMAT_VERSION})"
        )

    return _project_from_dict(data)


def _require(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise IncompleteProjectDataError(f"Missing required field: {key!r}")
    return data[key]


def _project_from_dict(data: dict[str, Any]) -> Project:
    scene_data = _require(data, "scene_settings")
    if not isinstance(scene_data, dict):
        raise IncompleteProjectDataError("'scene_settings' must be an object.")

    try:
        scene_settings = SceneSettings(
            width=int(scene_data["width"]),
            height=int(scene_data["height"]),
            background_color=str(scene_data["background_color"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IncompleteProjectDataError("Invalid or incomplete 'scene_settings'.") from exc

    assets_data = data.get("assets", [])
    if not isinstance(assets_data, list):
        raise IncompleteProjectDataError("'assets' must be a list when present.")
    assets = [_asset_from_dict(entry) for entry in assets_data]

    rig_templates_data = data.get("rig_templates", [])
    if not isinstance(rig_templates_data, list):
        raise IncompleteProjectDataError("'rig_templates' must be a list when present.")
    rig_templates = [_rig_template_from_dict(entry) for entry in rig_templates_data]
    for template in rig_templates:
        try:
            validate_rig_template(template, assets)
        except RigValidationError as exc:
            raise IncompleteProjectDataError(f"Invalid rig template {template.id!r}: {exc}") from exc

    frames_data = _require(data, "frames")
    if not isinstance(frames_data, list) or not frames_data:
        raise IncompleteProjectDataError("'frames' must be a non-empty list.")
    frames = [_frame_from_dict(entry) for entry in frames_data]

    try:
        fps = int(_require(data, "fps"))
        exposure = int(_require(data, "exposure"))
        current_frame_index = int(_require(data, "current_frame_index"))
    except (TypeError, ValueError) as exc:
        raise IncompleteProjectDataError("Invalid numeric field in project data.") from exc

    if not (0 <= current_frame_index < len(frames)):
        raise IncompleteProjectDataError(
            f"'current_frame_index' {current_frame_index} out of range for "
            f"{len(frames)} frame(s)."
        )

    try:
        validate_playback_settings(fps, exposure, scene_settings.width, scene_settings.height)
    except PlaybackValidationError as exc:
        raise IncompleteProjectDataError(f"Invalid playback/scene settings: {exc}") from exc

    for frame in frames:
        for rig in frame.rigs:
            try:
                validate_rig(rig, assets)
            except RigValidationError as exc:
                raise IncompleteProjectDataError(f"Invalid rig data in frame {frame.id!r}: {exc}") from exc
        try:
            validate_camera(frame.camera)
        except CameraValidationError as exc:
            raise IncompleteProjectDataError(f"Invalid camera in frame {frame.id!r}: {exc}") from exc
        try:
            validate_layers(frame.layers, assets)
        except LayerValidationError as exc:
            raise IncompleteProjectDataError(f"Invalid layer data in frame {frame.id!r}: {exc}") from exc

    return Project(
        format_version=SUPPORTED_FORMAT_VERSION,
        scene_settings=scene_settings,
        fps=fps,
        exposure=exposure,
        assets=assets,
        rig_templates=rig_templates,
        frames=frames,
        current_frame_index=current_frame_index,
        smooth_animation_enabled=bool(data.get("smooth_animation_enabled", False)),
    )


def _asset_from_dict(data: Any) -> Asset:
    if not isinstance(data, dict):
        raise IncompleteProjectDataError("Each 'assets' entry must be an object.")
    try:
        relative_path = data.get("relative_path")
        return Asset(
            id=str(data["id"]),
            name=str(data["name"]),
            source_path=str(data["source_path"]),
            relative_path=str(relative_path) if relative_path is not None else None,
            width=int(data["width"]),
            height=int(data["height"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IncompleteProjectDataError("Invalid or incomplete entry in 'assets'.") from exc


def _bone_from_dict(data: Any) -> Bone:
    if not isinstance(data, dict):
        raise IncompleteProjectDataError("Each bone entry must be an object.")
    try:
        parent_id = data.get("parent_id")
        x = float(data["x"])
        y = float(data["y"])
        pivot_x = float(data["pivot_x"])
        pivot_y = float(data["pivot_y"])
        # attach_x/attach_y are Milestone 6A additions, absent from every
        # Milestone 2-5B file. Default them to the bone's *current* anchor
        # (x + pivot_x, y + pivot_y) rather than 0.0: for a legacy bone this
        # is the position its pivot is already sitting at in parent space,
        # so opening an old rig in the new Rig Builder shows a consistent,
        # non-surprising attach point instead of a wrong one at the origin.
        attach_x = float(data["attach_x"]) if "attach_x" in data else x + pivot_x
        attach_y = float(data["attach_y"]) if "attach_y" in data else y + pivot_y
        return Bone(
            id=str(data["id"]),
            name=str(data["name"]),
            parent_id=str(parent_id) if parent_id is not None else None,
            asset_id=str(data["asset_id"]),
            x=x,
            y=y,
            rotation=float(data["rotation"]),
            scale_x=float(data["scale_x"]),
            scale_y=float(data["scale_y"]),
            pivot_x=pivot_x,
            pivot_y=pivot_y,
            attach_x=attach_x,
            attach_y=attach_y,
            z_index=int(data["z_index"]),
            visible=bool(data["visible"]),
            opacity=float(data["opacity"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IncompleteProjectDataError("Invalid or incomplete entry in a rig's 'bones'.") from exc


def _rig_from_dict(data: Any) -> Rig:
    if not isinstance(data, dict):
        raise IncompleteProjectDataError("Each 'rigs' entry must be an object.")
    bones_data = data.get("bones")
    if not isinstance(bones_data, list):
        raise IncompleteProjectDataError("Rig 'bones' must be a list.")
    bones = [_bone_from_dict(entry) for entry in bones_data]
    try:
        return Rig(
            id=str(data["id"]),
            name=str(data["name"]),
            root_bone_id=str(data["root_bone_id"]),
            bones=bones,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IncompleteProjectDataError("Invalid or incomplete rig entry.") from exc


def _rig_template_from_dict(data: Any) -> RigTemplate:
    if not isinstance(data, dict):
        raise IncompleteProjectDataError("Each 'rig_templates' entry must be an object.")
    bones_data = data.get("bones")
    if not isinstance(bones_data, list):
        raise IncompleteProjectDataError("RigTemplate 'bones' must be a list.")
    bones = [_bone_from_dict(entry) for entry in bones_data]
    try:
        canvas_width = data.get("canvas_width")
        canvas_height = data.get("canvas_height")
        return RigTemplate(
            id=str(data["id"]),
            name=str(data["name"]),
            root_bone_id=str(data["root_bone_id"]),
            bones=bones,
            canvas_width=float(canvas_width) if canvas_width is not None else None,
            canvas_height=float(canvas_height) if canvas_height is not None else None,
            category=str(data.get("category", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IncompleteProjectDataError("Invalid or incomplete rig_templates entry.") from exc


def _camera_from_dict(data: Any) -> Camera:
    if not isinstance(data, dict):
        raise IncompleteProjectDataError("Frame 'camera' must be an object.")
    try:
        return Camera(x=float(data["x"]), y=float(data["y"]), zoom=float(data["zoom"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise IncompleteProjectDataError("Invalid or incomplete 'camera'.") from exc


def _layer_from_dict(data: Any) -> Layer:
    if not isinstance(data, dict):
        raise IncompleteProjectDataError("Each 'layers' entry must be an object.")
    try:
        return Layer(
            id=str(data["id"]),
            name=str(data["name"]),
            asset_id=str(data["asset_id"]),
            x=float(data["x"]),
            y=float(data["y"]),
            rotation=float(data["rotation"]),
            scale_x=float(data["scale_x"]),
            scale_y=float(data["scale_y"]),
            pivot_x=float(data["pivot_x"]),
            pivot_y=float(data["pivot_y"]),
            z_index=int(data["z_index"]),
            z_depth=float(data["z_depth"]),
            visible=bool(data["visible"]),
            opacity=float(data["opacity"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IncompleteProjectDataError("Invalid or incomplete entry in 'layers'.") from exc


def _frame_from_dict(data: Any) -> Frame:
    if not isinstance(data, dict):
        raise IncompleteProjectDataError("Each 'frames' entry must be an object.")
    try:
        frame_id = str(data["id"])
        duration = int(data["duration"])
        label = str(data["label"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IncompleteProjectDataError("Invalid or incomplete entry in 'frames'.") from exc

    rigs_data = data.get("rigs", [])
    if not isinstance(rigs_data, list):
        raise IncompleteProjectDataError("Frame 'rigs' must be a list when present.")
    rigs = [_rig_from_dict(entry) for entry in rigs_data]

    camera_data = data.get("camera")
    camera = _camera_from_dict(camera_data) if camera_data is not None else Camera()

    layers_data = data.get("layers", [])
    if not isinstance(layers_data, list):
        raise IncompleteProjectDataError("Frame 'layers' must be a list when present.")
    layers = [_layer_from_dict(entry) for entry in layers_data]

    interpolation_map = _interpolation_map_from_dict(data.get("interpolation_map"))

    return Frame(
        id=frame_id,
        duration=duration,
        label=label,
        rigs=rigs,
        camera=camera,
        layers=layers,
        interpolation_map=interpolation_map,
    )


def _interpolation_map_from_dict(data: Any) -> dict[str, InterpolationSettings]:
    """Absent (pre-Smooth-Animation files) -> ``{}``. See module docstring."""
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise IncompleteProjectDataError("Frame 'interpolation_map' must be an object when present.")
    try:
        return {
            str(property_id): interpolation_settings_from_dict(settings_data)
            for property_id, settings_data in data.items()
        }
    except SerializationError as exc:
        raise IncompleteProjectDataError(f"Invalid entry in 'interpolation_map': {exc}") from exc
