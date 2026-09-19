"""Pure interpolation/easing math for Smooth Animation.

No Qt, no Pillow, no I/O — every function here takes and returns plain
floats/dataclasses. See ``domain.models.InterpolationType``/
``InterpolationSettings`` for the data these functions consume, and
``services.playback_engine`` for how they're applied across a whole
:class:`~pivotcut.domain.models.KeyframeState`.

Property id convention (used by ``services.playback_engine`` and stored as
keys of ``KeyframeState.interpolation_map`` — see the helpers at the bottom
of this module, which are the *only* place that builds these strings so
every caller stays in sync):

- ``"bone:<bone_id>:rotation"``
- ``"bone:<bone_id>:x"`` / ``"bone:<bone_id>:y"``
- ``"camera:x"`` / ``"camera:y"`` / ``"camera:zoom"``
- ``"env:<layer_id>:<field>"``, ``field`` one of
  ``domain.ai_keyframe.LAYER_TRANSFORM_FIELDS``
"""

from __future__ import annotations

from pivotcut.domain.models import InterpolationSettings, InterpolationType

#: Used whenever a property has no entry in ``KeyframeState.interpolation_map``.
DEFAULT_INTERPOLATION_SETTINGS = InterpolationSettings()

_NEWTON_RAPHSON_ITERATIONS = 8
_NEWTON_RAPHSON_TOLERANCE = 1e-7
_BISECTION_ITERATIONS = 30


def _clamp01(t: float) -> float:
    return 0.0 if t < 0.0 else 1.0 if t > 1.0 else t


# -- Cubic Bézier timing curve (CSS cubic-bezier()-style easing) ---------------------


def _bezier_component(t: float, p1: float, p2: float) -> float:
    """One axis of a cubic Bézier at parametric ``t``, with implicit fixed
    endpoints ``0`` (at ``t=0``) and ``1`` (at ``t=1``)."""
    one_minus_t = 1.0 - t
    return 3.0 * one_minus_t * one_minus_t * t * p1 + 3.0 * one_minus_t * t * t * p2 + t * t * t


def _bezier_derivative(t: float, p1: float, p2: float) -> float:
    """d/dt of :func:`_bezier_component`, for Newton-Raphson."""
    one_minus_t = 1.0 - t
    return 3.0 * one_minus_t * one_minus_t * p1 + 6.0 * one_minus_t * t * (p2 - p1) + 3.0 * t * t * (1.0 - p2)


def _solve_t_for_x(x: float, x1: float, x2: float) -> float:
    """Find the parametric ``t`` whose Bézier X coordinate equals ``x``.

    Newton-Raphson first (fast, a handful of iterations for any easing
    curve in common use), falling back to bisection if it stalls (a zero
    derivative, or simply not converging within budget) — bisection always
    converges here because every ``x1``/``x2`` this app allows (control
    handles in ``[0, 1]``) keeps X(t) monotonically non-decreasing.
    """
    t = x  # X(t) tracks t closely for typical easing curves — a good seed.
    for _ in range(_NEWTON_RAPHSON_ITERATIONS):
        error = _bezier_component(t, x1, x2) - x
        if abs(error) < _NEWTON_RAPHSON_TOLERANCE:
            return t
        derivative = _bezier_derivative(t, x1, x2)
        if abs(derivative) < 1e-6:
            break
        t = _clamp01(t - error / derivative)

    lower, upper = 0.0, 1.0
    t = _clamp01(t)
    for _ in range(_BISECTION_ITERATIONS):
        error = _bezier_component(t, x1, x2) - x
        if abs(error) < _NEWTON_RAPHSON_TOLERANCE:
            return t
        if error < 0.0:
            lower = t
        else:
            upper = t
        t = (lower + upper) / 2.0
    return t


def ease_cubic_bezier(control_points: tuple[float, float, float, float], x: float) -> float:
    """Eased progress ``y`` in ``[0, 1]`` for linear progress ``x`` in
    ``[0, 1]``, along the cubic Bézier timing curve ``control_points =
    (x1, y1, x2, y2)`` (implicit fixed endpoints ``(0, 0)``/``(1, 1)``).
    """
    x = _clamp01(x)
    if x == 0.0 or x == 1.0:
        return x
    x1, y1, x2, y2 = control_points
    t = _solve_t_for_x(x, x1, x2)
    return _bezier_component(t, y1, y2)


# -- Value/angle interpolation --------------------------------------------------------


def _eased_progress(t: float, settings: InterpolationSettings) -> float:
    if settings.type is InterpolationType.STEP:
        # Classic hard snap: hold the start value for the entire
        # transition, only reaching `end` exactly at t=1.
        return 0.0 if t < 1.0 else 1.0
    if settings.type is InterpolationType.LINEAR:
        return t
    if settings.type is InterpolationType.CUBIC_SPLINE:
        return ease_cubic_bezier(settings.control_points, t)
    raise ValueError(f"Unknown InterpolationType: {settings.type!r}")


def interpolate_value(start: float, end: float, t: float, settings: InterpolationSettings | None = None) -> float:
    """Interpolate a plain scalar (position, zoom, scale, ...) from
    ``start`` to ``end`` at progress ``t`` (clamped to ``[0, 1]``), eased
    according to ``settings`` (:data:`DEFAULT_INTERPOLATION_SETTINGS` if omitted).
    """
    settings = settings if settings is not None else DEFAULT_INTERPOLATION_SETTINGS
    progress = _eased_progress(_clamp01(t), settings)
    return start + (end - start) * progress


def _shortest_angle_delta_degrees(start_degrees: float, end_degrees: float) -> float:
    """Signed delta in ``(-180, 180]`` that reaches an angle equivalent to
    ``end_degrees`` from ``start_degrees`` by the shortest path — e.g.
    ``350 -> 10`` gives ``+20``, never ``-340``."""
    return ((end_degrees - start_degrees + 180.0) % 360.0) - 180.0


def interpolate_angle_degrees(
    start_degrees: float, end_degrees: float, t: float, settings: InterpolationSettings | None = None
) -> float:
    """Like :func:`interpolate_value`, but for a rotation in degrees:
    unwraps the start/end pair to the shortest angular path first, so a
    bone always rotates the "short way around" instead of jumping the raw
    numeric difference (e.g. 350deg -> 10deg advances +20deg, not -340deg).

    The result is **not** wrapped back into ``[0, 360)`` — it stays
    continuous from ``start_degrees``, matching how ``domain.rig.Bone.rotation``
    is already allowed to hold values outside that range.
    """
    delta = _shortest_angle_delta_degrees(start_degrees, end_degrees)
    return start_degrees + interpolate_value(0.0, delta, t, settings)


# -- Property id scheme (see module docstring) -----------------------------------


def bone_rotation_property_id(bone_id: str) -> str:
    return f"bone:{bone_id}:rotation"


def bone_axis_property_id(bone_id: str, axis: str) -> str:
    """``axis`` is ``"x"`` or ``"y"``."""
    return f"bone:{bone_id}:{axis}"


def camera_property_id(field_name: str) -> str:
    """``field_name`` is one of ``"x"``, ``"y"``, ``"zoom"``."""
    return f"camera:{field_name}"


def environment_property_id(layer_id: str, field_name: str) -> str:
    """``field_name`` is one of ``domain.ai_keyframe.LAYER_TRANSFORM_FIELDS``."""
    return f"env:{layer_id}:{field_name}"
