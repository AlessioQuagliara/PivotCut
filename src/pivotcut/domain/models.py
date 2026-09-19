"""Pure domain data model for PivotCut.

No Qt imports allowed here: this module must remain testable and usable
without a running GUI. Qt-facing code lives under ``pivotcut.ui``.

Coordinate system (scene, rig bones, and future layers/camera all share it):

- Scene origin: top-left corner.
- X axis: positive to the right.
- Y axis: positive downward.
- Units: scene pixels.
- Rotation: degrees, positive = clockwise (screen/Qt convention, a
  consequence of the Y axis pointing down — see ``domain.rig.Matrix2D``).
- Rig bone values (``x``/``y``/``rotation``/``scale_x``/``scale_y``) are in
  local space relative to their parent bone, except a rig's root bone
  (``parent_id is None``), whose values are in world/scene space directly.
- A bone's local-to-parent transform composes as: translate to (x, y) in
  the parent's local space, then rotate around the pivot, then scale
  around that same pivot. See ``domain.rig.local_matrix`` for the exact
  formula.
- The pivot (``pivot_x``/``pivot_y``) is expressed in the PNG asset's own
  local pixel coordinates (0, 0 = image top-left).
- Layers (background/foreground image planes) use the exact same
  x/y/rotation/scale/pivot composition as a rig root bone — they have no
  parent hierarchy, so that composition *is* their world transform. A
  virtual camera (``x``/``y``/``zoom``, snapshot per frame) then offsets
  that world transform for 2.5D parallax: layers closer to ``z_depth = 0``
  (the character/rig plane) move with the camera like the rig does, while
  layers with larger ``z_depth`` move progressively less. See
  ``domain.camera`` for the exact formula and ``domain.layer`` for the
  ``Layer`` model.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum

from pivotcut.domain.assets import Asset
from pivotcut.domain.camera import Camera
from pivotcut.domain.layer import Layer
from pivotcut.domain.rig import Rig, RigTemplate


@dataclass
class SceneSettings:
    """Global canvas/scene configuration."""

    width: int = 1920
    height: int = 1080
    background_color: str = "#2b2b2b"


@dataclass
class Frame:
    """A single timeline entry: an independent snapshot of scene state.

    Milestone 1 kept this to id/duration/label. Milestone 2 added ``rigs``;
    Milestone 3 adds ``camera`` and ``layers`` the same way — each field is
    explicit, typed and serializable, and each frame owns its own complete
    snapshot (rig poses, layer placement, camera position) rather than
    referencing shared mutable state.
    """

    id: str
    duration: int = 1
    label: str = ""
    rigs: list[Rig] = field(default_factory=list)
    camera: Camera = field(default_factory=Camera)
    layers: list[Layer] = field(default_factory=list)
    #: How this frame's animatable properties ease *toward the next frame*
    #: in the timeline, keyed by the property id scheme documented on
    #: ``domain.interpolation`` — only consulted at all when
    #: ``Project.smooth_animation_enabled`` is True (see
    #: ``services.playback_engine``); empty by default for every frame,
    #: including every already-saved project, which is exactly what keeps
    #: Smooth Animation fully opt-in and existing projects pixel-identical.
    interpolation_map: dict[str, InterpolationSettings] = field(default_factory=dict)


@dataclass
class Project:
    """Top-level project state: the source of truth for the whole app.

    ``rig_templates`` (Milestone 6A) is the project's Rig Library: reusable
    character *structures* with no timeline pose of their own — see
    ``domain.rig.RigTemplate``. Distinct from ``Frame.rigs``, which holds
    posed *instances* of a rig, one snapshot per frame.
    """

    format_version: int = 1
    scene_settings: SceneSettings = field(default_factory=SceneSettings)
    fps: int = 24
    exposure: int = 3
    assets: list[Asset] = field(default_factory=list)
    rig_templates: list[RigTemplate] = field(default_factory=list)
    frames: list[Frame] = field(default_factory=list)
    current_frame_index: int = 0
    #: Master switch for Smooth Animation (spline/eased in-betweening — see
    #: ``domain.interpolation``/``services.playback_engine``). ``False`` by
    #: default: playback and export show/render each Pose exactly, held for
    #: its whole Exposure with a hard cut to the next — identical to every
    #: project created before this feature existed. ``True`` eases toward
    #: the next Pose across each Pose's Exposure window instead, using each
    #: ``Frame.interpolation_map`` (falling back to a smooth default per
    #: property left unspecified there).
    smooth_animation_enabled: bool = False


# -- Smooth Animation (see ``domain.interpolation``/``services.playback_engine``) ---


class InterpolationType(Enum):
    """How a value transitions from one keyframe to the next.

    - ``STEP``: no easing at all — classic Pivot Animator-style hard snap.
      The value holds at ``start`` for the whole transition and only
      becomes ``end`` once it's fully reached (see
      ``domain.interpolation.interpolate_value``).
    - ``LINEAR``: constant-speed interpolation.
    - ``CUBIC_SPLINE``: eased interpolation along a cubic Bézier timing
      curve (the same model as a CSS ``cubic-bezier()``/After Effects
      easy-ease curve) — see ``InterpolationSettings.control_points``.
    """

    STEP = "step"
    LINEAR = "linear"
    CUBIC_SPLINE = "cubic_spline"


@dataclass
class InterpolationSettings:
    """How one animatable property eases between two keyframes.

    ``control_points`` are the ``(x1, y1, x2, y2)`` handles of a cubic
    Bézier timing curve with implicit fixed endpoints ``P0 = (0, 0)`` and
    ``P3 = (1, 1)`` — exactly the 4 numbers a CSS ``cubic-bezier(x1, y1, x2,
    y2)``/After Effects "Bezier" easy-ease curve takes. Only meaningful
    when ``type`` is ``CUBIC_SPLINE``; ignored otherwise. The default,
    ``(0.25, 0.1, 0.25, 1.0)``, is the standard "ease" (gentle ease-in,
    stronger ease-out) preset.
    """

    type: InterpolationType = InterpolationType.CUBIC_SPLINE
    control_points: tuple[float, float, float, float] = (0.25, 0.1, 0.25, 1.0)


# -- AI Text-to-Animation (see ``domain.ai_keyframe``/``services.ai_animator``) ------
#
# These two dataclasses are the *target* scene state an AI animator asks
# for — plain data, never a live ``Camera``/``Bone``/``Layer`` reference —
# so ``services.ai_animator`` can build/parse them with no Qt and no
# dependency on a live ``Project`` being open. ``domain.ai_keyframe`` turns
# a ``KeyframeState`` into an actual new ``Frame`` by applying it on top of
# a base frame's rigs/layers/camera.


@dataclass
class CameraState:
    """A single frame's desired virtual camera pose, as produced by an AI
    animator. Field-for-field identical to ``domain.camera.Camera``, kept as
    its own type so AI request/response code never needs a live ``Camera``
    instance just to describe a *target* pose."""

    x: float = 0.0
    y: float = 0.0
    zoom: float = 1.0


@dataclass
class KeyframeState:
    """One frame's worth of AI-generated global scene state.

    Every mapping is keyed by the id of the thing it targets, and every
    mapping is a *partial* update: a bone/layer not mentioned simply keeps
    whatever it already has on the base frame the keyframe is applied onto
    (see ``domain.ai_keyframe.build_frame_from_keyframe``) — the AI never
    has to restate an entire rig/scene just to move one arm.

    - ``bone_rotations``: ``{bone_id: rotation_degrees}``. Works for any
      bone (root or child) — rotation is always meaningful in this rig's
      forward-kinematics model (see ``domain.rig.Bone``).
    - ``bone_positions``: ``{bone_id: {"x": ..., "y": ...}}``. On the root
      bone this is a world-space move; on a child bone it's local to its
      parent, exactly like a canvas drag (see ``domain.rig.Bone``'s
      docstring) — it does *not* touch ``attach_x``/``attach_y``.
    - ``asset_swaps``: ``{bone_id: asset_id}``. Each ``Bone`` already holds
      exactly one ``asset_id`` (its "slot"), so swapping a bone's PNG for
      an expression/prop change is just overwriting that field.
    - ``environment_transforms``: ``{layer_id: {field_name: value}}`` where
      ``field_name`` is one of ``Layer``'s own transform fields (``x``,
      ``y``, ``rotation``, ``scale_x``, ``scale_y``, ``z_depth``) — for
      environmental/parallax movement.
    - ``camera``: the frame's desired camera pose, or ``None`` to leave the
      base frame's camera untouched.
    - ``interpolation_map``: ``{property_id: InterpolationSettings}`` —
      how each individually animatable property should ease *from this
      keyframe to the next one* (see ``domain.interpolation``/
      ``services.playback_engine.get_interpolated_state``). A property id
      not present here falls back to
      ``domain.interpolation.DEFAULT_INTERPOLATION_SETTINGS``. Property ids
      follow a fixed ``"<kind>:<target>:<field>"`` scheme: ``"bone:<bone_id>:rotation"``,
      ``"bone:<bone_id>:x"``/``"bone:<bone_id>:y"``, ``"camera:x"``/
      ``"camera:y"``/``"camera:zoom"``, and ``"env:<layer_id>:<field>"``
      for one of ``domain.ai_keyframe.LAYER_TRANSFORM_FIELDS``.
    """

    frame_index: int
    bone_rotations: dict[str, float] = field(default_factory=dict)
    bone_positions: dict[str, dict[str, float]] = field(default_factory=dict)
    asset_swaps: dict[str, str] = field(default_factory=dict)
    environment_transforms: dict[str, dict[str, float]] = field(default_factory=dict)
    camera: CameraState | None = None
    interpolation_map: dict[str, InterpolationSettings] = field(default_factory=dict)


def new_frame_id() -> str:
    return str(uuid.uuid4())


def new_project() -> Project:
    """Create a brand new project with a single default frame."""
    first_frame = Frame(id=new_frame_id(), duration=1, label="Frame 1")
    return Project(frames=[first_frame], current_frame_index=0)
