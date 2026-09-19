"""Import/export a single :class:`~pivotcut.domain.rig.RigTemplate` as a
standalone, shareable "Character File" (``.pivotcut-rig.json`` — Milestone
6C: "Character Library portabile").

Never embeds PNG binary data — only an informative **asset manifest**
(id/name/dimensions/relative+absolute path hints) for every asset the
template's bones depend on. An imported template's ``bone.asset_id``
values still point at ids from the *exporting* project's registry, which
generally don't exist in the *importing* one; callers resolve the gap in
three steps:

1. :func:`auto_resolve_manifest` — best-effort, no UI: tries each entry's
   relative path (against the Character File's own folder), then its
   absolute source path, then a same-name/dimensions match against assets
   already in the target project; imports/dedupes newly-found PNGs via
   ``services.asset_manager.import_png_asset`` as it goes.
2. :func:`missing_asset_ids` — whatever's left after step 1.
3. An explicit UI step for the remainder (``ui/asset_mapping_dialog.py``),
   then :func:`remap_template_asset_ids` before ever inserting the template
   into ``Project.rig_templates`` — this module never silently produces a
   broken rig referencing assets that don't exist.

Backward compatibility: files written before Milestone 6C used the JSON key
``asset_references`` (no ``file_type``/``relative_path_hint``/``metadata``);
:func:`import_rig_template` still reads those exactly as before. New files
add a ``file_type`` marker and write ``asset_manifest`` (a superset of the
old shape), but the on-disk *format_version* stays 1 — nothing about the
existing required fields changed, only new, optional ones were added.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pivotcut.domain.assets import Asset
from pivotcut.domain.models import Project
from pivotcut.domain.rig import RigTemplate
from pivotcut.services import asset_manager
from pivotcut.services.project_io import IncompleteProjectDataError
from pivotcut.services.project_io import _bone_from_dict as _parse_bone

SUPPORTED_FORMAT_VERSION = 1
TEMPLATE_FILE_SUFFIX = ".pivotcut-rig.json"
FILE_TYPE = "pivotcut-rig"


class RigTemplateIOError(Exception):
    """Base class for rig template import/export errors."""


class InvalidRigTemplateFileError(RigTemplateIOError):
    """The file could not be read/written, isn't valid JSON, or has the wrong shape."""


class UnsupportedRigTemplateFormatError(RigTemplateIOError):
    """The file's ``format_version`` is missing or not supported."""


@dataclass
class AssetReference:
    """Informative (never binary) metadata about one asset a template depends on.

    ``relative_path_hint`` is relative to the Character File itself (so the
    pair "file + PNGs" stays resolvable if moved/shared together);
    ``source_path_hint`` is the exporting machine's absolute path, kept only
    as a last-resort fallback (see :func:`auto_resolve_manifest`).
    """

    id: str
    name: str
    width: int
    height: int
    source_path_hint: str = ""
    relative_path_hint: str = ""


@dataclass
class RigTemplateImport:
    """The result of reading an external Character File, before asset resolution."""

    template: RigTemplate
    asset_references: list[AssetReference] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def export_rig_template(template: RigTemplate, assets: list[Asset], path: Path) -> None:
    """Write ``template`` to ``path`` as a standalone, shareable Character File.

    Never embeds PNG binaries. Writing this never mutates ``Project`` and
    the caller must not mark the project dirty for it (see
    ``app/main_window.py``'s "Save Selected Character to File…"/"Export
    Selected Rig Template…" handlers).
    """
    referenced_ids = {bone.asset_id for bone in template.bones}
    assets_by_id = {asset.id: asset for asset in assets}
    asset_manifest = [
        AssetReference(
            id=asset_id,
            name=assets_by_id[asset_id].name,
            width=assets_by_id[asset_id].width,
            height=assets_by_id[asset_id].height,
            source_path_hint=assets_by_id[asset_id].source_path,
            relative_path_hint=_relative_hint(Path(assets_by_id[asset_id].source_path), path.parent),
        )
        for asset_id in sorted(referenced_ids)
        if asset_id in assets_by_id
    ]
    data = {
        "file_type": FILE_TYPE,
        "format_version": SUPPORTED_FORMAT_VERSION,
        "template": asdict(template),
        "asset_manifest": [asdict(ref) for ref in asset_manifest],
        "metadata": {
            "created_with": "PivotCut",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "template_name": template.name,
        },
    }
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise InvalidRigTemplateFileError(f"Cannot write rig template file: {path}") from exc


def _relative_hint(asset_path: Path, character_file_dir: Path) -> str:
    """Best-effort relative path from ``character_file_dir`` to ``asset_path``.

    Uses ``os.path.relpath``-style traversal (unlike ``Path.relative_to``,
    which refuses to walk up ``..``) since the PNG commonly lives in a
    sibling/parent folder relative to wherever the Character File is saved.
    """
    try:
        import os

        return os.path.relpath(asset_path, start=character_file_dir)
    except ValueError:
        return ""  # different drive on Windows, or another unresolvable case


def import_rig_template(path: Path) -> RigTemplateImport:
    """Read a standalone Character File.

    Deliberately does **not** validate asset references against any
    project's asset registry (there isn't one yet at this point) — only
    structural well-formedness of the JSON/dataclass shape. Call
    :func:`auto_resolve_manifest` then :func:`missing_asset_ids` against the
    target ``Project.assets`` next.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InvalidRigTemplateFileError(f"Cannot read rig template file: {path}") from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise InvalidRigTemplateFileError(f"Rig template file is not valid JSON: {path}") from exc

    if not isinstance(data, dict):
        raise InvalidRigTemplateFileError("Rig template file must contain a JSON object.")

    format_version = data.get("format_version")
    if format_version != SUPPORTED_FORMAT_VERSION:
        raise UnsupportedRigTemplateFormatError(
            f"Unsupported rig template format_version: {format_version!r} "
            f"(expected {SUPPORTED_FORMAT_VERSION})"
        )

    template_data = data.get("template")
    if not isinstance(template_data, dict):
        raise InvalidRigTemplateFileError("Missing or invalid 'template' object.")
    template = _template_from_dict(template_data)

    # "asset_manifest" (Milestone 6C) supersedes the pre-6C "asset_references"
    # key; either is accepted so older Character Files keep loading exactly
    # as before.
    refs_data = data.get("asset_manifest", data.get("asset_references", []))
    if not isinstance(refs_data, list):
        raise InvalidRigTemplateFileError("'asset_manifest' must be a list when present.")
    asset_references = [_asset_reference_from_dict(entry) for entry in refs_data]

    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    return RigTemplateImport(template=template, asset_references=asset_references, metadata=metadata)


def missing_asset_ids(template: RigTemplate, existing_assets: list[Asset]) -> list[str]:
    """Which asset ids ``template``'s bones reference that aren't in ``existing_assets``."""
    existing_ids = {asset.id for asset in existing_assets}
    referenced_ids = {bone.asset_id for bone in template.bones}
    return sorted(referenced_ids - existing_ids)


def find_asset_by_fingerprint(existing_assets: list[Asset], ref: AssetReference) -> Asset | None:
    """A same-name/same-dimensions match already in the project — the
    "auto-match by filename" heuristic. No file hashing: just the filename
    (``Asset.name`` is the import-time filename stem) plus width/height,
    which is enough to catch "this PNG was already imported here under a
    different id" without touching the filesystem at all.
    """
    return next(
        (a for a in existing_assets if a.name == ref.name and a.width == ref.width and a.height == ref.height),
        None,
    )


def auto_resolve_manifest(
    asset_manifest: list[AssetReference],
    character_file_path: Path,
    project: Project,
    project_dir: Path | None,
) -> dict[str, str]:
    """Best-effort, no-UI resolution of ``asset_manifest`` entries against
    ``project``. Returns ``{old_asset_id: resolved_asset_id}`` for every
    entry that could be resolved automatically (imports/dedupes newly-found
    PNGs into ``project.assets`` as it goes, via
    ``services.asset_manager.import_png_asset`` — which already refuses to
    duplicate an asset that resolves to the same on-disk file).

    Resolution order per entry:

    1. ``ref.id`` already names an asset in ``project.assets`` (importing
       back into the same/a sibling project) — nothing to do.
    2. ``relative_path_hint``, resolved against the Character File's own
       folder.
    3. ``source_path_hint`` (the exporting machine's absolute path).
    4. A same-name/same-dimensions asset already in the project (see
       :func:`find_asset_by_fingerprint`).

    Entries that resolve none of these are simply absent from the returned
    mapping — never raises for a file that can't be found.
    """
    mapping: dict[str, str] = {}
    existing_ids = {asset.id for asset in project.assets}
    character_file_dir = character_file_path.parent

    for ref in asset_manifest:
        if ref.id in existing_ids:
            continue

        resolved_path: Path | None = None
        if ref.relative_path_hint:
            candidate = (character_file_dir / ref.relative_path_hint).resolve()
            if candidate.is_file():
                resolved_path = candidate
        if resolved_path is None and ref.source_path_hint:
            candidate = Path(ref.source_path_hint)
            if candidate.is_file():
                resolved_path = candidate

        if resolved_path is not None:
            try:
                asset = asset_manager.import_png_asset(project, resolved_path, project_dir)
            except asset_manager.AssetImportError:
                asset = None
            if asset is not None:
                mapping[ref.id] = asset.id
                existing_ids.add(asset.id)
                continue

        fingerprint_match = find_asset_by_fingerprint(project.assets, ref)
        if fingerprint_match is not None:
            mapping[ref.id] = fingerprint_match.id

    return mapping


def remap_template_asset_ids(template: RigTemplate, mapping: dict[str, str]) -> RigTemplate:
    """A copy of ``template`` with every bone's ``asset_id`` rewritten through ``mapping``.

    ``mapping`` is "old (exporting-project) asset id" -> "id of an asset
    that now exists in the target project" (built by the Rig Builder's
    import UI, one entry per resolved :func:`missing_asset_ids` gap). Bone
    asset ids not present in ``mapping`` are left unchanged (already valid
    in the target project, e.g. re-importing into the same project).
    """
    new_template = copy.deepcopy(template)
    for bone in new_template.bones:
        if bone.asset_id in mapping:
            bone.asset_id = mapping[bone.asset_id]
    return new_template


def _template_from_dict(data: dict[str, Any]) -> RigTemplate:
    bones_data = data.get("bones")
    if not isinstance(bones_data, list):
        raise InvalidRigTemplateFileError("Template 'bones' must be a list.")
    try:
        bones = [_parse_bone(entry) for entry in bones_data]
    except IncompleteProjectDataError as exc:
        raise InvalidRigTemplateFileError(f"Invalid bone entry in template: {exc}") from exc

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
        raise InvalidRigTemplateFileError(f"Invalid or incomplete 'template' object: {exc}") from exc


def _asset_reference_from_dict(data: Any) -> AssetReference:
    if not isinstance(data, dict):
        raise InvalidRigTemplateFileError("Each 'asset_manifest' entry must be an object.")
    try:
        return AssetReference(
            id=str(data["id"]),
            name=str(data["name"]),
            width=int(data["width"]),
            height=int(data["height"]),
            source_path_hint=str(data.get("source_path_hint", "")),
            relative_path_hint=str(data.get("relative_path_hint", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidRigTemplateFileError(f"Invalid or incomplete 'asset_manifest' entry: {exc}") from exc
