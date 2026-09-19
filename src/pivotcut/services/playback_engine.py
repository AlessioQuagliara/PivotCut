"""Sub-frame ("Smooth Animation") interpolation between two keyframes.

Pure and Qt-free, like every other computational ``services`` module in
this app (see ``services.ai_animator``'s module docstring for the same
pattern) — ``PlaybackEngine`` never touches ``QGraphicsScene``/``QImage``;
it only turns two :class:`~pivotcut.domain.models.KeyframeState` into a
third, virtual one at some progress between them. The existing discrete
timeline (one ``Frame`` per ``Project.frames`` entry, held for
``exposure`` video frames — see ``domain.playback``) is untouched by this
module: it stays the source of truth for *which* keyframes exist. Turning
``get_interpolated_state``'s output into something on screen (a rendered
``Frame``, via ``domain.ai_keyframe.build_frame_from_keyframe``) is the
caller's job — this module only computes the numbers.
"""

from __future__ import annotations

from typing import Callable

from pivotcut.domain import ai_keyframe, interpolation
from pivotcut.domain.models import CameraState, Frame, InterpolationSettings, KeyframeState

#: Progress at which a discrete (non-interpolable) value — an
#: ``asset_swaps`` entry — switches from the start keyframe's value to the
#: end keyframe's, absent any more specific per-property configuration.
DEFAULT_ASSET_SWAP_THRESHOLD = 0.5


def _settings_for(settings_map: dict[str, InterpolationSettings], property_id: str) -> InterpolationSettings:
    return settings_map.get(property_id, interpolation.DEFAULT_INTERPOLATION_SETTINGS)


def _interpolate_flat_map(
    start_map: dict[str, float],
    end_map: dict[str, float],
    t: float,
    settings_map: dict[str, InterpolationSettings],
    property_id_of: Callable[[str], str],
    angular: bool = False,
) -> dict[str, float]:
    """Interpolate a ``{id: float}`` map, keyed by the union of both sides.

    An id present on only one side has nothing to interpolate *towards*,
    so it simply passes through unchanged rather than being dropped or
    guessed at.
    """
    interpolate = interpolation.interpolate_angle_degrees if angular else interpolation.interpolate_value
    result: dict[str, float] = {}
    for key in start_map.keys() | end_map.keys():
        if key in start_map and key in end_map:
            settings = _settings_for(settings_map, property_id_of(key))
            result[key] = interpolate(start_map[key], end_map[key], t, settings)
        else:
            result[key] = start_map.get(key, end_map.get(key))
    return result


def _interpolate_nested_map(
    start_map: dict[str, dict[str, float]],
    end_map: dict[str, dict[str, float]],
    t: float,
    settings_map: dict[str, InterpolationSettings],
    property_id_of: Callable[[str, str], str],
) -> dict[str, dict[str, float]]:
    """Same idea as :func:`_interpolate_flat_map`, one level deeper —
    shared by ``bone_positions`` (``bone_id -> {"x"/"y": value}``) and
    ``environment_transforms`` (``layer_id -> {field: value}``).
    """
    result: dict[str, dict[str, float]] = {}
    for entity_id in start_map.keys() | end_map.keys():
        start_fields = start_map.get(entity_id, {})
        end_fields = end_map.get(entity_id, {})
        fields: dict[str, float] = {}
        for field_name in start_fields.keys() | end_fields.keys():
            if field_name in start_fields and field_name in end_fields:
                settings = _settings_for(settings_map, property_id_of(entity_id, field_name))
                fields[field_name] = interpolation.interpolate_value(
                    start_fields[field_name], end_fields[field_name], t, settings
                )
            else:
                fields[field_name] = start_fields.get(field_name, end_fields.get(field_name))
        result[entity_id] = fields
    return result


class PlaybackEngine:
    """Computes a virtual, continuously-eased sub-frame between two
    sequential keyframes.

    Stateless besides its :attr:`asset_swap_threshold` configuration —
    every call to :meth:`get_interpolated_state` is a pure function of its
    arguments and never mutates ``start_frame``/``end_frame``.
    """

    def __init__(self, asset_swap_threshold: float = DEFAULT_ASSET_SWAP_THRESHOLD) -> None:
        self.asset_swap_threshold = asset_swap_threshold

    def get_interpolated_frame(self, start_frame: Frame, end_frame: Frame, frame_progress: float) -> Frame:
        """The real-timeline counterpart to :meth:`get_interpolated_state`:
        eases an actual rendered ``Frame`` toward another one.

        Resolves both frames to dense ``KeyframeState`` snapshots (see
        ``domain.ai_keyframe.resolve_keyframe_state``), interpolates them,
        then bakes the result back onto a deep copy of ``start_frame`` (see
        ``domain.ai_keyframe.build_frame_from_keyframe``) — so the returned
        ``Frame`` is immediately renderable through the exact same pipeline
        as any other frame (``services.export_renderer``/``ui.canvas_view``),
        with no caller-visible difference beyond its (fresh, unsaved) id.

        Never mutates ``start_frame``/``end_frame``. Passing the same
        ``Frame`` for both (the common "last pose has no next pose to ease
        toward" case) is safe and returns it unchanged at any progress.
        """
        start_state = ai_keyframe.resolve_keyframe_state(start_frame)
        end_state = ai_keyframe.resolve_keyframe_state(end_frame)
        interpolated_state = self.get_interpolated_state(start_state, end_state, frame_progress)
        return ai_keyframe.build_frame_from_keyframe(start_frame, interpolated_state, label=start_frame.label)

    def get_interpolated_state(
        self, start_frame: KeyframeState, end_frame: KeyframeState, frame_progress: float
    ) -> KeyframeState:
        """Return a new ``KeyframeState`` for the scene at ``frame_progress``
        (``0.0`` = ``start_frame``, ``1.0`` = ``end_frame``) between the two
        given keyframes. Clamps ``frame_progress`` to ``[0, 1]``.

        Continuous properties (bone rotations/positions, camera,
        environment transforms) are eased per-property using whichever
        :class:`~pivotcut.domain.models.InterpolationSettings`
        ``start_frame.interpolation_map`` supplies for that property id
        (``domain.interpolation.DEFAULT_INTERPOLATION_SETTINGS`` otherwise
        — see ``domain.interpolation``'s module docstring for the id
        scheme). Discrete properties (``asset_swaps``) switch all at once
        at :attr:`asset_swap_threshold`: before it, the result reflects
        ``start_frame``'s swaps; at/after it, ``end_frame``'s.

        Never mutates either input frame.
        """
        t = 0.0 if frame_progress < 0.0 else 1.0 if frame_progress > 1.0 else frame_progress
        settings_map = start_frame.interpolation_map

        return KeyframeState(
            frame_index=start_frame.frame_index,
            bone_rotations=_interpolate_flat_map(
                start_frame.bone_rotations,
                end_frame.bone_rotations,
                t,
                settings_map,
                interpolation.bone_rotation_property_id,
                angular=True,
            ),
            bone_positions=_interpolate_nested_map(
                start_frame.bone_positions,
                end_frame.bone_positions,
                t,
                settings_map,
                interpolation.bone_axis_property_id,
            ),
            asset_swaps=self._resolve_asset_swaps(start_frame, end_frame, t),
            environment_transforms=_interpolate_nested_map(
                start_frame.environment_transforms,
                end_frame.environment_transforms,
                t,
                settings_map,
                interpolation.environment_property_id,
            ),
            camera=self._interpolate_camera(start_frame, end_frame, t, settings_map),
        )

    def _resolve_asset_swaps(self, start_frame: KeyframeState, end_frame: KeyframeState, t: float) -> dict[str, str]:
        result = dict(start_frame.asset_swaps)
        if t >= self.asset_swap_threshold:
            result.update(end_frame.asset_swaps)
        return result

    def _interpolate_camera(
        self,
        start_frame: KeyframeState,
        end_frame: KeyframeState,
        t: float,
        settings_map: dict[str, InterpolationSettings],
    ) -> CameraState | None:
        start_camera, end_camera = start_frame.camera, end_frame.camera
        if start_camera is None:
            return end_camera
        if end_camera is None:
            return start_camera
        return CameraState(
            x=interpolation.interpolate_value(
                start_camera.x, end_camera.x, t, _settings_for(settings_map, interpolation.camera_property_id("x"))
            ),
            y=interpolation.interpolate_value(
                start_camera.y, end_camera.y, t, _settings_for(settings_map, interpolation.camera_property_id("y"))
            ),
            zoom=interpolation.interpolate_value(
                start_camera.zoom,
                end_camera.zoom,
                t,
                _settings_for(settings_map, interpolation.camera_property_id("zoom")),
            ),
        )
