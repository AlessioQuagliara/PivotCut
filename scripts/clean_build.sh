#!/usr/bin/env bash
# Remove PivotCut's PyInstaller build output (build/ and dist/ only).
#
# Usage: ./scripts/clean_build.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BUILD_DIR="$PROJECT_ROOT/build"
DIST_DIR="$PROJECT_ROOT/dist"

rm -rf "$BUILD_DIR" "$DIST_DIR"
echo "Removed $BUILD_DIR and $DIST_DIR"
