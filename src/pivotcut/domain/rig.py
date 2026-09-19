"""Pure, Qt-free hierarchical cut-out rig model (forward kinematics only).

See ``pivotcut.domain.models`` for the canonical scene-wide coordinate
system documentation (origin, axis directions, rotation sign, units). This
module additionally documents how a single bone's *local* transform is
built and how bones compose into *world* transforms.

Local-to-parent transform (see :func:`local_matrix`), applied to a point
expressed in the PNG's own local pixel space, in this exact order:

1. translate the point to ``(bone.x, bone.y)`` in the parent's local space;
2. rotate it around the pivot (``bone.pivot_x``/``bone.pivot_y``, in the
   PNG's local pixel coordinates — its position after step 1 is used as the
   rotation center);
3. scale it around that same pivot.

The root bone of a rig has no parent, so its "parent space" *is* the scene
(world) space directly — see :func:`world_transforms`.

Rendering (``pivotcut.ui``) is the only layer allowed to convert
:class:`Matrix2D` into a Qt ``QTransform``; this module never imports Qt.
"""

from __future__ import annotations

import copy
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Iterable

from pivotcut.domain.assets import Asset


class RigValidationError(Exception):
    """Raised by :func:`validate_rig` (or transform resolution) on an invalid rig."""


@dataclass(frozen=True)
class Matrix2D:
    """2D affine transform: ``x' = a*x + c*y + tx``, ``y' = b*x + d*y + ty``.

    A plain, typed, Qt-free stand-in for ``QTransform``. Field layout
    mirrors ``QTransform(m11, m12, m21, m22, dx, dy)`` 1:1 so the UI layer
    can convert with a direct field mapping.
    """

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def apply_point(self, x: float, y: float) -> tuple[float, float]:
        return (self.a * x + self.c * y + self.tx, self.b * x + self.d * y + self.ty)

    def apply_vector(self, dx: float, dy: float) -> tuple[float, float]:
        """Transform a direction/delta, ignoring translation."""
        return (self.a * dx + self.c * dy, self.b * dx + self.d * dy)

    def then(self, other: Matrix2D) -> Matrix2D:
        """Compose so that ``self`` is applied first, then ``other``.

        Equivalent to ``other.apply_point(*self.apply_point(x, y))``.
        """
        return Matrix2D(
            a=other.a * self.a + other.c * self.b,
            b=other.b * self.a + other.d * self.b,
            c=other.a * self.c + other.c * self.d,
            d=other.b * self.c + other.d * self.d,
            tx=other.a * self.tx + other.c * self.ty + other.tx,
            ty=other.b * self.tx + other.d * self.ty + other.ty,
        )

    def inverse(self) -> Matrix2D:
        det = self.a * self.d - self.b * self.c
        if det == 0:
            raise ZeroDivisionError("Matrix2D is not invertible (zero determinant).")
        inv_det = 1.0 / det
        ia, ib, ic, id_ = self.d * inv_det, -self.b * inv_det, -self.c * inv_det, self.a * inv_det
        itx = -(ia * self.tx + ic * self.ty)
        ity = -(ib * self.tx + id_ * self.ty)
        return Matrix2D(a=ia, b=ib, c=ic, d=id_, tx=itx, ty=ity)

    @staticmethod
    def identity() -> Matrix2D:
        return Matrix2D()

    @staticmethod
    def translation(dx: float, dy: float) -> Matrix2D:
        return Matrix2D(tx=dx, ty=dy)

    @staticmethod
    def rotation_degrees(degrees: float) -> Matrix2D:
        """Positive degrees rotate clockwise in this Y-down coordinate system."""
        theta = math.radians(degrees)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        return Matrix2D(a=cos_t, b=sin_t, c=-sin_t, d=cos_t)

    @staticmethod
    def scale(sx: float, sy: float) -> Matrix2D:
        return Matrix2D(a=sx, d=sy)

    @staticmethod
    def rotation_about(cx: float, cy: float, degrees: float) -> Matrix2D:
        return (
            Matrix2D.translation(-cx, -cy)
            .then(Matrix2D.rotation_degrees(degrees))
            .then(Matrix2D.translation(cx, cy))
        )

    @staticmethod
    def scale_about(cx: float, cy: float, sx: float, sy: float) -> Matrix2D:
        return (
            Matrix2D.translation(-cx, -cy).then(Matrix2D.scale(sx, sy)).then(Matrix2D.translation(cx, cy))
        )


@dataclass
class Bone:
    """One rigid part of a cut-out rig: a PNG asset placed in local space.

    ``x``/``y``/``rotation``/``scale_x``/``scale_y`` are local, relative to
    ``parent_id`` — except for the root bone (``parent_id is None``), whose
    values are in world/scene space directly. ``pivot_x``/``pivot_y`` are in
    the PNG's own local pixel coordinates (0,0 = image top-left).

    ``attach_x``/``attach_y`` (Milestone 6A, Character Rig Builder) are the
    point, in the **parent's** local pixel space, where this bone's pivot
    must land — meaningless for the root bone (no parent). They are kept
    *consistent with* ``x``/``y`` by construction rather than folded into
    the FK math: :func:`local_matrix`/:func:`world_transforms` are
    completely unchanged from Milestone 2 and only ever read ``x``/``y``.
    Use :func:`set_attach_point`/:func:`set_pivot_preserving_attach_point`
    to edit either quantity while keeping the other visually fixed — see
    their docstrings for the exact derivation. Plain pose editing (dragging
    a bone on the main canvas) never touches attach/pivot at all, only
    ``x``/``y``/``rotation`` directly, exactly as in Milestone 2-5A.
    """

    id: str
    name: str
    parent_id: str | None
    asset_id: str
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    pivot_x: float = 0.0
    pivot_y: float = 0.0
    attach_x: float = 0.0
    attach_y: float = 0.0
    z_index: int = 0
    visible: bool = True
    opacity: float = 1.0


@dataclass
class Rig:
    """A hierarchical cut-out character *instance*, posed in a specific Frame.

    See :class:`RigTemplate` for the reusable, pose-free counterpart used by
    the Character Rig Builder / Rig Library.
    """

    id: str
    name: str
    root_bone_id: str
    bones: list[Bone] = field(default_factory=list)


@dataclass
class RigTemplate:
    """A reusable character structure with no timeline pose of its own.

    Captured from a ``Rig`` instance via :func:`rig_template_from_rig` (Save
    Selected Rig as Template) and turned back into a fresh, independent
    ``Rig`` instance — with brand-new ids throughout — via
    :func:`instantiate_rig_template` (Add Character From Library). Living in
    ``Project.rig_templates`` rather than any ``Frame``, a template is never
    itself posed or rendered directly.
    """

    id: str
    name: str
    root_bone_id: str
    bones: list[Bone] = field(default_factory=list)
    canvas_width: float | None = None
    canvas_height: float | None = None
    category: str = ""


def find_bone(rig: Rig, bone_id: str) -> Bone | None:
    for bone in rig.bones:
        if bone.id == bone_id:
            return bone
    return None


def find_bone_in_list(bones: Iterable[Bone], bone_id: str) -> Bone | None:
    for bone in bones:
        if bone.id == bone_id:
            return bone
    return None


def children_of(rig: Rig, bone_id: str) -> list[Bone]:
    return [bone for bone in rig.bones if bone.parent_id == bone_id]


def _validate_bone_collection(owner_label: str, root_bone_id: str, bones: list[Bone], assets: Iterable[Asset]) -> None:
    """Shared structural/semantic validation for both ``Rig`` and ``RigTemplate``.

    Checks: unique bone ids, existing root, exactly one parentless bone
    matching ``root_bone_id``, all ``parent_id`` references resolve, no
    cycles, all ``asset_id`` references exist in ``assets``, ``opacity`` in
    [0, 1], and non-zero ``scale_x``/``scale_y``.
    """
    if not bones:
        raise RigValidationError(f"{owner_label} has no bones.")

    bone_ids = [bone.id for bone in bones]
    if len(bone_ids) != len(set(bone_ids)):
        raise RigValidationError(f"{owner_label} has duplicate bone ids.")
    bone_id_set = set(bone_ids)

    if root_bone_id not in bone_id_set:
        raise RigValidationError(f"root_bone_id {root_bone_id!r} does not reference an existing bone.")

    roots = [bone for bone in bones if bone.parent_id is None]
    if len(roots) != 1:
        raise RigValidationError(
            f"{owner_label} must have exactly one bone without parent_id, found {len(roots)}."
        )
    if roots[0].id != root_bone_id:
        raise RigValidationError(
            f"The bone without parent_id ({roots[0].id!r}) must match root_bone_id ({root_bone_id!r})."
        )

    asset_ids = {asset.id for asset in assets}
    for bone in bones:
        if bone.parent_id is not None and bone.parent_id not in bone_id_set:
            raise RigValidationError(
                f"Bone {bone.id!r} references missing parent_id {bone.parent_id!r}."
            )
        if bone.asset_id not in asset_ids:
            raise RigValidationError(f"Bone {bone.id!r} references missing asset_id {bone.asset_id!r}.")
        if not (0.0 <= bone.opacity <= 1.0):
            raise RigValidationError(f"Bone {bone.id!r} has opacity {bone.opacity} outside [0, 1].")
        if bone.scale_x == 0.0 or bone.scale_y == 0.0:
            raise RigValidationError(f"Bone {bone.id!r} has a zero scale component.")

    _check_no_cycles_in_bones(owner_label, bones)


def validate_rig(rig: Rig, assets: Iterable[Asset]) -> None:
    """Validate structural and semantic invariants of ``rig`` — see :func:`_validate_bone_collection`."""
    _validate_bone_collection(f"Rig {rig.id!r}", rig.root_bone_id, rig.bones, assets)


def validate_rig_template(template: RigTemplate, assets: Iterable[Asset]) -> None:
    """Validate structural and semantic invariants of ``template`` — see :func:`_validate_bone_collection`."""
    _validate_bone_collection(f"RigTemplate {template.id!r}", template.root_bone_id, template.bones, assets)


def _check_no_cycles_in_bones(owner_label: str, bones: list[Bone]) -> None:
    bones_by_id = {bone.id: bone for bone in bones}
    for start_id in bones_by_id:
        visited: set[str] = set()
        current: str | None = start_id
        while current is not None:
            if current in visited:
                raise RigValidationError(f"Cycle detected in {owner_label} involving bone {current!r}.")
            visited.add(current)
            current = bones_by_id[current].parent_id


def local_matrix(bone: Bone) -> Matrix2D:
    """Build the bone's local-to-parent transform (see module docstring)."""
    anchor_x = bone.x + bone.pivot_x
    anchor_y = bone.y + bone.pivot_y
    return (
        Matrix2D.translation(bone.x, bone.y)
        .then(Matrix2D.rotation_about(anchor_x, anchor_y, bone.rotation))
        .then(Matrix2D.scale_about(anchor_x, anchor_y, bone.scale_x, bone.scale_y))
    )


def world_transforms(rig: Rig) -> dict[str, Matrix2D]:
    """Compute the world-space transform of every bone in ``rig``.

    The root bone's local transform *is* its world transform (its parent
    space is the scene). Raises ``RigValidationError`` if a cycle or a
    dangling ``parent_id`` is encountered during resolution.
    """
    bones_by_id = {bone.id: bone for bone in rig.bones}
    cache: dict[str, Matrix2D] = {}

    def resolve(bone_id: str, stack: tuple[str, ...]) -> Matrix2D:
        if bone_id in cache:
            return cache[bone_id]
        if bone_id in stack:
            raise RigValidationError(f"Cycle detected in rig {rig.id!r} involving bone {bone_id!r}.")
        bone = bones_by_id.get(bone_id)
        if bone is None:
            raise RigValidationError(f"Bone {bone_id!r} not found in rig {rig.id!r}.")
        local = local_matrix(bone)
        if bone.parent_id is None:
            world = local
        else:
            parent_world = resolve(bone.parent_id, stack + (bone_id,))
            world = local.then(parent_world)
        cache[bone_id] = world
        return world

    for bone_id in bones_by_id:
        resolve(bone_id, ())
    return cache


def world_transform_for_bone(rig: Rig, bone_id: str) -> Matrix2D:
    return world_transforms(rig)[bone_id]


def create_rig_from_asset(asset: Asset, scene_width: float, scene_height: float) -> Rig:
    """Build a single-root-bone rig centered in the scene, named after the asset.

    Takes plain scene dimensions rather than ``SceneSettings`` so this
    module never has to import ``domain.models`` (which itself imports
    ``Rig`` from here) — that would create an import cycle.
    """
    bone_id = str(uuid.uuid4())
    bone = Bone(
        id=bone_id,
        name=asset.name,
        parent_id=None,
        asset_id=asset.id,
        x=scene_width / 2,
        y=scene_height / 2,
        pivot_x=asset.width / 2,
        pivot_y=asset.height / 2,
    )
    return Rig(id=str(uuid.uuid4()), name=asset.name, root_bone_id=bone_id, bones=[bone])


_COPY_SUFFIX_RE = re.compile(r"^(?P<base>.*) \(copy(?: (?P<num>\d+))?\)$")


def _duplicate_name(name: str) -> str:
    """Same "(copy)"/"(copy 2)" incrementing suffix convention as ``domain.timeline``."""
    if not name:
        return name
    match = _COPY_SUFFIX_RE.match(name)
    if match:
        base = match.group("base")
        num = int(match.group("num")) if match.group("num") else 1
        return f"{base} (copy {num + 1})"
    return f"{name} (copy)"


# -- Attach point / pivot editing (Character Rig Builder, Milestone 6A) -------------


def set_attach_point(bone: Bone, attach_x: float, attach_y: float) -> None:
    """Move ``bone`` so its pivot lands exactly on ``(attach_x, attach_y)`` in
    the parent's local space, keeping the pivot itself fixed.

    Recomputes ``x``/``y`` as ``attach_x - pivot_x``/``attach_y - pivot_y``:
    rotating/scaling about the pivot (see ``local_matrix``) leaves the pivot
    point itself fixed, so this places it exactly at the given anchor.
    Meaningless for a root bone (no parent) — the Rig Builder doesn't offer
    this action for the root part.
    """
    bone.attach_x = attach_x
    bone.attach_y = attach_y
    bone.x = attach_x - bone.pivot_x
    bone.y = attach_y - bone.pivot_y


def set_pivot_preserving_attach_point(bone: Bone, pivot_x: float, pivot_y: float) -> None:
    """Change ``bone``'s pivot while keeping its attach point (parent-space
    anchor, from the *stored* ``attach_x``/``attach_y``) visually fixed.
    """
    bone.pivot_x = pivot_x
    bone.pivot_y = pivot_y
    bone.x = bone.attach_x - pivot_x
    bone.y = bone.attach_y - pivot_y


def create_child_bone(asset: Asset, parent: Bone, name: str | None = None) -> Bone:
    """A new non-root Bone attached to ``parent`` at the parent's own pivot.

    Its own pivot defaults to the new asset's image center. Both attach
    point and pivot are freely re-editable afterwards.
    """
    bone = Bone(
        id=str(uuid.uuid4()),
        name=name if name is not None else asset.name,
        parent_id=parent.id,
        asset_id=asset.id,
        pivot_x=asset.width / 2.0,
        pivot_y=asset.height / 2.0,
    )
    set_attach_point(bone, parent.pivot_x, parent.pivot_y)
    return bone


# -- Structural editing: reparent / remove / duplicate ------------------------------


def reparent_bone(bones: list[Bone], bone_id: str, new_parent_id: str) -> None:
    """Change ``bone_id``'s ``parent_id`` to ``new_parent_id`` in place.

    Raises ``RigValidationError`` if ``new_parent_id`` doesn't exist, is
    ``bone_id`` itself, or is a descendant of ``bone_id`` (either would
    create a self-parent or a cycle).
    """
    bone = find_bone_in_list(bones, bone_id)
    if bone is None:
        raise RigValidationError(f"Bone {bone_id!r} not found.")
    if new_parent_id == bone_id:
        raise RigValidationError(f"Bone {bone_id!r} cannot be its own parent.")
    new_parent = find_bone_in_list(bones, new_parent_id)
    if new_parent is None:
        raise RigValidationError(f"Bone {new_parent_id!r} not found.")

    bones_by_id = {b.id: b for b in bones}
    current: str | None = new_parent_id
    while current is not None:
        if current == bone_id:
            raise RigValidationError(
                f"Cannot reparent {bone_id!r} under {new_parent_id!r}: would create a cycle."
            )
        current = bones_by_id[current].parent_id

    bone.parent_id = new_parent_id


def _collect_subtree(bones: list[Bone], bone_id: str, into: set[str]) -> None:
    into.add(bone_id)
    for child in bones:
        if child.parent_id == bone_id:
            _collect_subtree(bones, child.id, into)


def remove_bone_subtree(bones: list[Bone], bone_id: str, root_bone_id: str) -> list[Bone]:
    """Return a new bone list with ``bone_id`` and all its descendants removed.

    Raises ``RigValidationError`` if ``bone_id`` is the root: a rig always
    needs a root, and removing it would need an explicit new-root choice
    this MVP doesn't offer (the Rig Builder simply disables Remove Part for
    the root part).
    """
    if bone_id == root_bone_id:
        raise RigValidationError("Cannot remove the root part; choose a different root first.")
    to_remove: set[str] = set()
    _collect_subtree(bones, bone_id, to_remove)
    return [bone for bone in bones if bone.id not in to_remove]


def remove_bone_reparent_children(bones: list[Bone], bone_id: str, root_bone_id: str) -> list[Bone]:
    """Return a new bone list with ``bone_id`` removed and its direct
    children reparented to ``bone_id``'s own parent.

    Raises ``RigValidationError`` if ``bone_id`` is the root (it has no
    parent to reparent its children to).
    """
    if bone_id == root_bone_id:
        raise RigValidationError("Cannot remove the root part this way; it has no parent to reparent to.")
    removed_bone = find_bone_in_list(bones, bone_id)
    if removed_bone is None:
        raise RigValidationError(f"Bone {bone_id!r} not found.")
    new_parent_id = removed_bone.parent_id

    result: list[Bone] = []
    for bone in bones:
        if bone.id == bone_id:
            continue
        if bone.parent_id == bone_id:
            bone = copy.deepcopy(bone)
            bone.parent_id = new_parent_id
        result.append(bone)
    return result


def set_root_bone(bones: list[Bone], new_root_id: str) -> None:
    """Re-root the hierarchy in place so ``new_root_id`` becomes the root.

    Walks the parent chain from ``new_root_id`` up to the current root and
    reverses each edge along that path (each bone's former parent becomes
    its child instead) — the standard "re-root a tree" operation, so every
    other branch not on that path is completely untouched.
    """
    bones_by_id = {bone.id: bone for bone in bones}
    if new_root_id not in bones_by_id:
        raise RigValidationError(f"Bone {new_root_id!r} not found.")

    path: list[str] = []
    current: str | None = new_root_id
    visited: set[str] = set()
    while current is not None:
        if current in visited:
            raise RigValidationError(f"Cycle detected while re-rooting at bone {current!r}.")
        visited.add(current)
        path.append(current)
        current = bones_by_id[current].parent_id

    for i in range(len(path) - 1):
        child_id, parent_id = path[i], path[i + 1]
        bones_by_id[parent_id].parent_id = child_id
    bones_by_id[new_root_id].parent_id = None


def duplicate_bone(bones: list[Bone], bone_id: str) -> Bone:
    """Duplicate a single bone (not its subtree) as a new sibling.

    The copy gets a fresh id, the same ``parent_id`` as the original, and a
    "(copy)"/"(copy 2)" name suffix. Existing children of the original stay
    attached to the original, not the copy — duplicating a part creates an
    independent new part, not a clone of a whole limb.
    """
    original = find_bone_in_list(bones, bone_id)
    if original is None:
        raise RigValidationError(f"Bone {bone_id!r} not found.")
    new_bone = copy.deepcopy(original)
    new_bone.id = str(uuid.uuid4())
    new_bone.name = _duplicate_name(original.name)
    return new_bone


# -- Rig <-> RigTemplate conversion --------------------------------------------------


def rig_template_from_rig(
    rig: Rig,
    name: str | None = None,
    canvas_width: float | None = None,
    canvas_height: float | None = None,
    category: str = "",
) -> RigTemplate:
    """Capture ``rig``'s current structure/pose as a reusable template.

    Bone ids are kept as-is (a plain deep copy): a template's bones live in
    a separate namespace from any live ``Rig`` instance, so sharing ids with
    the rig it was captured from is harmless — ``instantiate_rig_template``
    always mints brand-new ids for a fresh instance anyway.
    """
    return RigTemplate(
        id=str(uuid.uuid4()),
        name=name if name is not None else rig.name,
        root_bone_id=rig.root_bone_id,
        bones=copy.deepcopy(rig.bones),
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        category=category,
    )


def instantiate_rig_template(template: RigTemplate, x: float, y: float) -> Rig:
    """Create a fresh, independent ``Rig`` instance from ``template``.

    Every bone (and the rig itself) gets a brand-new id — parent-child
    relationships are remapped through an id table, never left pointing at
    the template's own bone ids. The root bone is placed at world position
    ``(x, y)``; every other bone keeps its template-defined local pose
    unchanged. Asset references, pivot, attach point and z-index are all
    preserved exactly from the template.
    """
    id_map: dict[str, str] = {bone.id: str(uuid.uuid4()) for bone in template.bones}
    new_root_id = id_map[template.root_bone_id]

    old_root = find_bone_in_list(template.bones, template.root_bone_id)
    if old_root is None:
        raise RigValidationError(f"RigTemplate {template.id!r} root bone not found among its bones.")
    dx = x - old_root.x
    dy = y - old_root.y

    new_bones: list[Bone] = []
    for bone in template.bones:
        new_bone = copy.deepcopy(bone)
        new_bone.id = id_map[bone.id]
        new_bone.parent_id = id_map[bone.parent_id] if bone.parent_id is not None else None
        if new_bone.parent_id is None:
            new_bone.x += dx
            new_bone.y += dy
        new_bones.append(new_bone)

    return Rig(id=str(uuid.uuid4()), name=template.name, root_bone_id=new_root_id, bones=new_bones)
