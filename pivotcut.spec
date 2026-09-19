# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for PivotCut — macOS Apple Silicon (arm64), local dev build.

Run via ``scripts/build_macos.sh`` (which also checks the interpreter is
native arm64 and runs the test suite first), or directly with:

    python -m PyInstaller pivotcut.spec

Produces a **directory-based** ``.app`` bundle (never ``--onefile`` — a
single-binary GUI app on macOS is slower to launch and complicates Qt
plugin discovery for no benefit here). This build is unsigned and not
notarized: Gatekeeper will show a warning on first launch (right-click ->
Open) until Milestone 5C (code signing / notarization / DMG), which is out
of scope here. It targets whatever architecture the running Python
interpreter is — ``scripts/build_macos.sh`` enforces that this is native
arm64, so ``target_arch`` is deliberately left as ``None`` (auto-detect
from the interpreter) rather than hardcoded, and this build is never
described as a universal2 binary.
"""

from pathlib import Path

# SPECPATH is injected into this file's exec globals by PyInstaller itself
# (the directory containing this .spec file) — using it instead of a
# hardcoded absolute path keeps this spec portable across machines/venvs.
PROJECT_ROOT = Path(SPECPATH)  # noqa: F821 - PyInstaller-injected global
SRC_DIR = PROJECT_ROOT / "src"
RESOURCES_DIR = SRC_DIR / "pivotcut" / "resources"
ICON_PATH = RESOURCES_DIR / "icons" / "pivotcut.icns"

# Only src/pivotcut/resources/ is bundled as data — never tests/, .venv/,
# user project files, or export output. Included even though it currently
# holds only .gitkeep, so runtime_paths.resource_path() resolves identically
# in source and frozen mode once real assets (e.g. an app icon) land here.
datas = []
if RESOURCES_DIR.is_dir():
    datas.append((str(RESOURCES_DIR), "pivotcut/resources"))

a = Analysis(  # noqa: F821 - PyInstaller-injected global
    [str(SRC_DIR / "pivotcut" / "main.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=datas,
    # No hidden imports needed for PySide6 (pyinstaller-hooks-contrib ships
    # a maintained PySide6 hook that collects the Qt platform/style/
    # imageformat plugins this app needs — QtWidgets/QtGui/QtCore only, no
    # QtNetwork/QtMultimedia/QML) or for Pillow (this project only calls
    # Image.open()/Image.save() for PNG via services/asset_manager.py and
    # services/export_renderer.py; the PNG codec lives inside PIL.Image
    # itself and is collected by Pillow's own bundled hook, not a plugin
    # PyInstaller would otherwise miss).
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821 - PyInstaller-injected global

exe = EXE(  # noqa: F821 - PyInstaller-injected global
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PivotCut",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed app: no Terminal/console window on launch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,  # build for the running interpreter's arch (arm64; see module docstring)
    codesign_identity=None,  # unsigned local build - signing/notarization is Milestone 5C
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821 - PyInstaller-injected global
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PivotCut",
)

app = BUNDLE(  # noqa: F821 - PyInstaller-injected global
    coll,
    name="PivotCut.app",
    icon=str(ICON_PATH) if ICON_PATH.is_file() else None,
    bundle_identifier="com.pivotcut.app",
    info_plist={
        "CFBundleName": "PivotCut",
        "CFBundleDisplayName": "PivotCut",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
        # No LSApplicationCategoryType / notarization-related keys yet -
        # this is an unsigned local build (Milestone 5B), not a
        # distributable one (that's Milestone 5C).
    },
)
