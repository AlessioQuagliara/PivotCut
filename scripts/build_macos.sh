#!/usr/bin/env bash
# Build PivotCut.app for macOS Apple Silicon (arm64) via PyInstaller.
#
# Usage:
#   ./scripts/build_macos.sh [--clean] [--skip-tests]
#   bash scripts/build_macos.sh [--clean] [--skip-tests]
#
#   --clean       Remove build/ and dist/ before building, without prompting.
#   --skip-tests  Skip the pytest gate (only for iterating on packaging
#                 itself; never use this to ship a build).
#
# This is a local, unsigned development build - see README.md "Build macOS
# (Apple Silicon)" for prerequisites, expected output, and troubleshooting.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

CLEAN=false
SKIP_TESTS=false
for arg in "$@"; do
  case "$arg" in
    --clean) CLEAN=true ;;
    --skip-tests) SKIP_TESTS=true ;;
    *)
      echo "error: unknown option: $arg" >&2
      echo "usage: $0 [--clean] [--skip-tests]" >&2
      exit 1
      ;;
  esac
done

# -- Preconditions ------------------------------------------------------------

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: PivotCut.app can only be built on macOS (detected: $(uname -s))." >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "error: '$PYTHON_BIN' not found. Activate the project venv first:" >&2
  echo "         source .venv/bin/activate" >&2
  exit 1
fi

ARCH="$("$PYTHON_BIN" -c 'import platform; print(platform.machine())')"
if [[ "$ARCH" != "arm64" ]]; then
  echo "error: this build must run from a native arm64 Python interpreter." >&2
  echo "       Detected machine: $ARCH (this looks like an x86_64/Rosetta Python)." >&2
  echo "       See README.md 'Build macOS (Apple Silicon)' -> 'Python/Rosetta x86_64'." >&2
  exit 1
fi
echo "==> Python architecture OK: $ARCH"

if ! "$PYTHON_BIN" -m PyInstaller --version >/dev/null 2>&1; then
  echo "error: PyInstaller is not installed in this environment." >&2
  echo "       Install project dependencies first: pip install -r requirements.txt" >&2
  exit 1
fi
echo "==> PyInstaller found: $("$PYTHON_BIN" -m PyInstaller --version)"

# -- Clean (only this project's own build output, nothing else) -------------

BUILD_DIR="$PROJECT_ROOT/build"
DIST_DIR="$PROJECT_ROOT/dist"

if [[ "$CLEAN" == true ]]; then
  echo "==> --clean: removing $BUILD_DIR and $DIST_DIR"
  rm -rf "$BUILD_DIR" "$DIST_DIR"
elif [[ -d "$BUILD_DIR" || -d "$DIST_DIR" ]]; then
  if [[ -t 0 ]]; then
    read -r -p "build/ and/or dist/ already exist. Remove them before rebuilding? [y/N] " reply
    if [[ "$reply" =~ ^[Yy]$ ]]; then
      rm -rf "$BUILD_DIR" "$DIST_DIR"
    fi
  else
    echo "==> build/ and/or dist/ already exist (non-interactive shell: not prompting)."
    echo "    Re-run with --clean to remove them automatically."
  fi
fi

# -- Tests must pass before packaging ----------------------------------------

if [[ "$SKIP_TESTS" == true ]]; then
  echo "==> Skipping pytest (--skip-tests passed) - do not ship this build."
else
  echo "==> Running pytest before build..."
  if ! "$PYTHON_BIN" -m pytest; then
    echo "error: tests failed - aborting build." >&2
    exit 1
  fi
  echo "==> All tests passed."
fi

# -- Build --------------------------------------------------------------

echo "==> Building PivotCut.app with PyInstaller (pivotcut.spec)..."
"$PYTHON_BIN" -m PyInstaller --noconfirm pivotcut.spec

APP_BUNDLE="$DIST_DIR/PivotCut.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "error: build finished but $APP_BUNDLE was not produced." >&2
  exit 1
fi

echo "==> Build complete."
echo "==> App bundle: $APP_BUNDLE"
echo "==> Launch with: open \"$APP_BUNDLE\""
