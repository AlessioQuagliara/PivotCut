"""AI Text-to-Animation service: turns a natural-language prompt into a
sequence of :class:`~pivotcut.domain.models.KeyframeState` via a cloud LLM's
structured outputs.

This module never touches Qt and never touches a live ``Project`` — it only
knows plain data (:class:`AnimationContext` in, ``list[KeyframeState]`` out)
plus how to talk to one specific AI provider's API. The "totally
non-blocking" requirement is handled entirely by
``ui.workers.AIAnimationWorker``, which runs a :class:`BaseAIAnimator` on a
background ``QThread``: every method here is a plain, synchronous, blocking
call, exactly like every other network/subprocess-facing ``services``
module in this app (see ``services.ffmpeg_export`` for the same pattern
with a subprocess instead of an HTTP request).

Provider SDKs (``anthropic``, ``openai``) are optional dependencies, lazily
imported inside each concrete animator's ``__init__``. Importing this
module — or using :class:`BaseAIAnimator`, the JSON Schema builder, or the
response parser — never requires either package to be installed; only
actually constructing ``AnthropicAIAnimator``/``OpenAIAnimator`` does. See
``pyproject.toml``'s ``[project.optional-dependencies] ai`` extra.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pivotcut.domain.ai_keyframe import LAYER_TRANSFORM_FIELDS
from pivotcut.domain.assets import Asset
from pivotcut.domain.models import CameraState, Frame, KeyframeState


class AIAnimatorError(Exception):
    """Base class for every error this module raises."""


class AIAnimatorUnavailableError(AIAnimatorError):
    """Raised when a provider's SDK package isn't installed."""


class AIAnimatorRequestError(AIAnimatorError):
    """Raised when the network request to the provider fails, is rejected, or lacks credentials."""


class AIAnimatorResponseError(AIAnimatorError):
    """Raised when the provider's response doesn't match the expected keyframe schema."""


# -- Request-side context: what the AI needs to know about this scene ----------------


@dataclass(frozen=True)
class BoneDescriptor:
    """The AI-facing summary of one rig bone — enough to reason about the
    hierarchy and pick valid ids, nothing Qt/pixel-buffer related."""

    id: str
    name: str
    parent_id: str | None
    asset_id: str


@dataclass(frozen=True)
class LayerDescriptor:
    """The AI-facing summary of one environment layer."""

    id: str
    name: str
    z_depth: float


@dataclass(frozen=True)
class AssetDescriptor:
    """The AI-facing summary of one importable PNG asset, available for
    ``asset_swaps`` (e.g. alternate facial expressions, held props)."""

    id: str
    name: str
    width: int
    height: int


@dataclass
class AnimationContext:
    """Everything one AI animation request needs, as plain data.

    ``prompt`` is the user's natural-language description of the desired
    action (e.g. "wave hello, then smile"). ``frame_count`` is how many
    keyframes to generate. ``bones``/``layers``/``assets`` describe the
    *current* frame's rig(s), environment layers, and every PNG asset
    available in the project — this is what lets
    :func:`build_keyframe_response_schema` restrict the AI to ids that
    actually exist, rather than hoping it invents valid ones.
    """

    prompt: str
    frame_count: int
    scene_width: int
    scene_height: int
    bones: list[BoneDescriptor] = field(default_factory=list)
    layers: list[LayerDescriptor] = field(default_factory=list)
    assets: list[AssetDescriptor] = field(default_factory=list)


def build_animation_context(
    frame: Frame,
    assets: list[Asset],
    prompt: str,
    frame_count: int,
    scene_width: int,
    scene_height: int,
) -> AnimationContext:
    """Build an :class:`AnimationContext` from a live ``Frame``/asset registry.

    A thin, pure convenience so callers (the AI animation UI worker) never
    have to hand-walk ``frame.rigs``/``frame.layers`` themselves.
    """
    bones = [
        BoneDescriptor(id=bone.id, name=bone.name, parent_id=bone.parent_id, asset_id=bone.asset_id)
        for rig in frame.rigs
        for bone in rig.bones
    ]
    layers = [LayerDescriptor(id=layer.id, name=layer.name, z_depth=layer.z_depth) for layer in frame.layers]
    asset_descriptors = [
        AssetDescriptor(id=asset.id, name=asset.name, width=asset.width, height=asset.height) for asset in assets
    ]
    return AnimationContext(
        prompt=prompt,
        frame_count=frame_count,
        scene_width=scene_width,
        scene_height=scene_height,
        bones=bones,
        layers=layers,
        assets=asset_descriptors,
    )


# -- JSON Schema (Structured Outputs) -------------------------------------------------

_NUMBER: dict[str, Any] = {"type": "number"}

_POSITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"x": _NUMBER, "y": _NUMBER},
    "additionalProperties": False,
}

_ENVIRONMENT_TRANSFORM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {name: _NUMBER for name in sorted(LAYER_TRANSFORM_FIELDS)},
    "additionalProperties": False,
}

_CAMERA_SCHEMA: dict[str, Any] = {
    "type": ["object", "null"],
    "properties": {"x": _NUMBER, "y": _NUMBER, "zoom": _NUMBER},
    "required": ["x", "y", "zoom"],
    "additionalProperties": False,
}


def _id_keyed_map_schema(value_schema: dict[str, Any], known_ids: list[str]) -> dict[str, Any]:
    """An object schema whose keys are restricted to ``known_ids`` when any
    are given — rigorous: the AI can only reference ids that actually exist
    in this scene — or any string key otherwise (e.g. an empty rig)."""
    if known_ids:
        return {"type": "object", "properties": {i: value_schema for i in known_ids}, "additionalProperties": False}
    return {"type": "object", "additionalProperties": value_schema}


def build_keyframe_response_schema(context: AnimationContext) -> dict[str, Any]:
    """A JSON Schema describing exactly the shape :func:`keyframes_from_payload`
    accepts: ``{"keyframes": [<one KeyframeState per entry>, ...]}``.

    Bone/layer/asset ids are baked in from ``context`` so the schema itself
    forbids the AI from inventing an id that doesn't exist in this project.
    Pass this to whichever provider's structured-output/tool-schema
    parameter — see :class:`AnthropicAIAnimator`/:class:`OpenAIAnimator`.

    Every field of one keyframe is marked ``required`` so it's always
    *present* in the response (an empty ``{}``/``null`` is a valid, cheap
    value for "nothing changed here") — this is deliberately different from
    requiring every bone/layer id to be restated on every keyframe, which
    would defeat ``KeyframeState``'s partial-update design (see its
    docstring in ``domain.models``).
    """
    bone_ids = [bone.id for bone in context.bones]
    layer_ids = [layer.id for layer in context.layers]
    asset_id_schema = (
        {"type": "string", "enum": [asset.id for asset in context.assets]}
        if context.assets
        else {"type": "string"}
    )

    keyframe_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "frame_index": {"type": "integer", "minimum": 0},
            "bone_rotations": _id_keyed_map_schema(_NUMBER, bone_ids),
            "bone_positions": _id_keyed_map_schema(_POSITION_SCHEMA, bone_ids),
            "asset_swaps": _id_keyed_map_schema(asset_id_schema, bone_ids),
            "environment_transforms": _id_keyed_map_schema(_ENVIRONMENT_TRANSFORM_SCHEMA, layer_ids),
            "camera": _CAMERA_SCHEMA,
        },
        "required": [
            "frame_index",
            "bone_rotations",
            "bone_positions",
            "asset_swaps",
            "environment_transforms",
            "camera",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "keyframes": {
                "type": "array",
                "items": keyframe_schema,
                "minItems": context.frame_count,
                "maxItems": context.frame_count,
            }
        },
        "required": ["keyframes"],
        "additionalProperties": False,
    }


def build_system_prompt(context: AnimationContext) -> str:
    """The system prompt sent to the LLM: explains this rig's bone
    hierarchy, the available environment layers and expression/prop PNGs,
    and the exact keyframe format it must respond with."""
    bone_lines = (
        "\n".join(
            f"  - id={bone.id!r} name={bone.name!r} parent={bone.parent_id!r} current_asset={bone.asset_id!r}"
            for bone in context.bones
        )
        or "  (this frame has no character rig)"
    )
    layer_lines = (
        "\n".join(f"  - id={layer.id!r} name={layer.name!r} z_depth={layer.z_depth}" for layer in context.layers)
        or "  (no environment layers)"
    )
    asset_lines = (
        "\n".join(
            f"  - id={asset.id!r} name={asset.name!r} ({asset.width}x{asset.height})" for asset in context.assets
        )
        or "  (no PNG assets available)"
    )

    return (
        "You are the animation director for PivotCut, a 2D cut-out animation "
        "tool. You are given a description of an action and must return a "
        "sequence of keyframes that stages it.\n\n"
        "RIG (forward kinematics; each bone is a rigid PNG part parented to "
        "another bone; rotating a bone rotates everything attached below "
        "it):\n"
        f"{bone_lines}\n\n"
        "ENVIRONMENT LAYERS (background/midground/foreground planes; lower "
        "z_depth moves more with the camera, higher z_depth moves less):\n"
        f"{layer_lines}\n\n"
        "AVAILABLE PNG ASSETS (use asset_swaps to change a bone's PNG for "
        "things like closed eyes, an open mouth, or a held prop):\n"
        f"{asset_lines}\n\n"
        f"Generate exactly {context.frame_count} keyframes (frame_index 0 to "
        f"{context.frame_count - 1}, one entry per pose in that order) that "
        f"stage this action: {context.prompt!r}\n\n"
        "Rules:\n"
        "- Only reference bone/layer/asset ids listed above — never invent one.\n"
        "- Every mapping is a partial update: omit any bone/layer you don't "
        "want to change on a given keyframe rather than restating its current value.\n"
        "- rotation is in degrees, clockwise positive.\n"
        "- bone_positions on a non-root bone is local to its parent, exactly "
        "like the root bone's is local to the scene — small nudges only; "
        "prefer bone_rotations for most posing.\n"
        "- camera.zoom is a multiplier (1.0 = no zoom).\n"
        "- Respond with keyframe data only, matching the provided schema — no prose."
    )


# -- Response parsing/validation: untrusted JSON -> trusted domain objects -----------


def _require_type(value: Any, expected: type | tuple[type, ...], path: str) -> Any:
    if not isinstance(value, expected):
        raise AIAnimatorResponseError(f"{path}: expected {expected}, got {type(value).__name__}")
    return value


def _camera_state_from_dict(data: Any, path: str) -> CameraState:
    _require_type(data, dict, path)
    try:
        return CameraState(x=float(data["x"]), y=float(data["y"]), zoom=float(data["zoom"]))
    except KeyError as exc:
        raise AIAnimatorResponseError(f"{path}: missing required field {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise AIAnimatorResponseError(f"{path}: {exc}") from exc


def _float_map_from_dict(data: Any, path: str) -> dict[str, float]:
    _require_type(data, dict, path)
    result: dict[str, float] = {}
    for key, value in data.items():
        _require_type(key, str, f"{path} key")
        try:
            result[key] = float(value)
        except (TypeError, ValueError) as exc:
            raise AIAnimatorResponseError(f"{path}[{key!r}]: expected a number, got {value!r}") from exc
    return result


def _nested_float_map_from_dict(data: Any, path: str) -> dict[str, dict[str, float]]:
    _require_type(data, dict, path)
    return {key: _float_map_from_dict(value, f"{path}[{key!r}]") for key in data for value in (data[key],)}


def _string_map_from_dict(data: Any, path: str) -> dict[str, str]:
    _require_type(data, dict, path)
    result: dict[str, str] = {}
    for key, value in data.items():
        _require_type(key, str, f"{path} key")
        result[key] = _require_type(value, str, f"{path}[{key!r}]")
    return result


def keyframe_state_from_dict(data: Any) -> KeyframeState:
    """Convert one raw keyframe dict (as returned by an LLM) into a
    validated :class:`~pivotcut.domain.models.KeyframeState`.

    Raises :class:`AIAnimatorResponseError` on any structural/type
    mismatch — this is the boundary where an untrusted network response
    turns into a trusted domain object; nothing past this point ever needs
    to re-check these types (see ``domain.ai_keyframe``).
    """
    _require_type(data, dict, "keyframe")
    try:
        frame_index = data["frame_index"]
    except KeyError as exc:
        raise AIAnimatorResponseError("keyframe: missing required field 'frame_index'") from exc
    if not isinstance(frame_index, int) or isinstance(frame_index, bool):
        raise AIAnimatorResponseError(f"keyframe.frame_index: expected an integer, got {frame_index!r}")

    camera_data = data.get("camera")
    camera = _camera_state_from_dict(camera_data, "keyframe.camera") if camera_data is not None else None

    return KeyframeState(
        frame_index=frame_index,
        bone_rotations=_float_map_from_dict(data.get("bone_rotations") or {}, "keyframe.bone_rotations"),
        bone_positions=_nested_float_map_from_dict(data.get("bone_positions") or {}, "keyframe.bone_positions"),
        asset_swaps=_string_map_from_dict(data.get("asset_swaps") or {}, "keyframe.asset_swaps"),
        environment_transforms=_nested_float_map_from_dict(
            data.get("environment_transforms") or {}, "keyframe.environment_transforms"
        ),
        camera=camera,
    )


def keyframes_from_payload(payload: Any) -> list[KeyframeState]:
    """Convert a full raw AI response payload into ``list[KeyframeState]``,
    ordered by ``frame_index``.

    Accepts either the ``{"keyframes": [...]}`` object described by
    :func:`build_keyframe_response_schema`, or a bare list of keyframe
    dicts (some providers/response modes hand back the array directly).
    """
    if isinstance(payload, dict):
        try:
            raw_keyframes = payload["keyframes"]
        except KeyError as exc:
            raise AIAnimatorResponseError("response: missing required field 'keyframes'") from exc
    else:
        raw_keyframes = payload
    _require_type(raw_keyframes, list, "keyframes")
    if not raw_keyframes:
        raise AIAnimatorResponseError("keyframes: response contained no keyframes")

    keyframes = [keyframe_state_from_dict(item) for item in raw_keyframes]
    keyframes.sort(key=lambda kf: kf.frame_index)
    return keyframes


# -- Providers --------------------------------------------------------------------


class BaseAIAnimator(ABC):
    """One cloud LLM provider capable of turning an :class:`AnimationContext`
    into a validated ``list[KeyframeState]``.

    Every method here is synchronous/blocking by design — see the module
    docstring for why: non-blocking behavior is ``ui.workers.AIAnimationWorker``'s
    job, not this class's.
    """

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise AIAnimatorRequestError("An API key is required.")
        self._api_key = api_key

    def generate_keyframes(self, context: AnimationContext) -> list[KeyframeState]:
        """Request a keyframe sequence from the provider and return it validated.

        Subclasses implement :meth:`_request_raw_keyframes`; this method
        owns the shared parse/validate step so every provider goes through
        the exact same :func:`keyframes_from_payload`.
        """
        raw_payload = self._request_raw_keyframes(context)
        return keyframes_from_payload(raw_payload)

    @abstractmethod
    def _request_raw_keyframes(self, context: AnimationContext) -> Any:
        """Call the provider's API and return the raw, still-unvalidated
        JSON payload (a parsed Python dict/list, not a JSON string)."""


class AnthropicAIAnimator(BaseAIAnimator):
    """Uses Claude's tool-use (a forced structured output) to generate keyframes."""

    DEFAULT_MODEL = "claude-sonnet-5"
    _TOOL_NAME = "submit_keyframes"

    def __init__(self, api_key: str, model: str | None = None) -> None:
        super().__init__(api_key)
        try:
            import anthropic
        except ImportError as exc:
            raise AIAnimatorUnavailableError(
                "The 'anthropic' package is not installed. Install it with: "
                "pip install anthropic (or pip install .[ai])."
            ) from exc
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL

    def _request_raw_keyframes(self, context: AnimationContext) -> Any:
        schema = build_keyframe_response_schema(context)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=8192,
                system=build_system_prompt(context),
                messages=[{"role": "user", "content": context.prompt}],
                tools=[
                    {
                        "name": self._TOOL_NAME,
                        "description": "Submit the generated keyframe sequence.",
                        "input_schema": schema,
                    }
                ],
                tool_choice={"type": "tool", "name": self._TOOL_NAME},
            )
        except Exception as exc:  # noqa: BLE001 - any SDK/network failure becomes one clear error type
            raise AIAnimatorRequestError(f"Anthropic request failed: {exc}") from exc

        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == self._TOOL_NAME:
                return block.input
        raise AIAnimatorResponseError("Anthropic response did not include the expected tool_use block.")


class OpenAIAnimator(BaseAIAnimator):
    """Uses OpenAI's Structured Outputs (``response_format: json_schema``)."""

    DEFAULT_MODEL = "gpt-4.1"

    def __init__(self, api_key: str, model: str | None = None) -> None:
        super().__init__(api_key)
        try:
            import openai
        except ImportError as exc:
            raise AIAnimatorUnavailableError(
                "The 'openai' package is not installed. Install it with: "
                "pip install openai (or pip install .[ai])."
            ) from exc
        self._client = openai.OpenAI(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL

    def _request_raw_keyframes(self, context: AnimationContext) -> Any:
        schema = build_keyframe_response_schema(context)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": build_system_prompt(context)},
                    {"role": "user", "content": context.prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "keyframe_sequence",
                        # Deliberately non-strict: our per-bone/layer maps
                        # are sparse/partial by design (see KeyframeState's
                        # docstring), which OpenAI's fully strict mode can't
                        # express without forcing every id to be restated on
                        # every keyframe. The schema is still sent and still
                        # guides generation — it just isn't hard-enforced.
                        "strict": False,
                        "schema": schema,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001 - any SDK/network failure becomes one clear error type
            raise AIAnimatorRequestError(f"OpenAI request failed: {exc}") from exc

        content = response.choices[0].message.content
        if not content:
            raise AIAnimatorResponseError("OpenAI response had no content.")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise AIAnimatorResponseError(f"OpenAI response was not valid JSON: {exc}") from exc
