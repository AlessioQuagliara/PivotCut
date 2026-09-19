"""Resolve on-disk paths that work identically from source and from a
PyInstaller-frozen ``.app`` bundle.

This is the **only** place in the codebase that inspects frozen-runtime
state (``sys.frozen``/``sys._MEIPASS``, set by PyInstaller's bootloader) —
every other module just calls :func:`resource_path` and never needs to
know whether it's running from ``src/`` or from inside
``PivotCut.app/Contents/Frameworks``.

``src/pivotcut/resources/`` currently holds no real assets (just
``.gitkeep``), so nothing calls this yet — it exists ahead of time so a
future icon/template lookup has one correct, already-tested place to live
instead of every call site growing its own frozen/dev branch.
"""

from __future__ import annotations

import sys
from pathlib import Path


def package_root() -> Path:
    """The ``pivotcut`` package directory, in source or frozen mode.

    In development this is ``src/pivotcut/`` (this file's own parent
    directory). When frozen, PyInstaller's bootloader sets
    ``sys._MEIPASS`` to the bundle's extracted data root; ``pivotcut.spec``
    collects ``resources/`` under ``pivotcut/resources`` inside that root,
    so the same relative layout applies in both cases.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass is not None:
            return Path(meipass) / "pivotcut"
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    """Path to a file under ``pivotcut/resources/``, from source or frozen."""
    return package_root() / "resources" / Path(*parts)
