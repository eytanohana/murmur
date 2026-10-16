#!/usr/bin/env bash
# Build murmur.app (ad-hoc signed) and dist/murmur-<version>-arm64.dmg from a fresh checkout.
# Needs only uv and the macOS command line tools (iconutil, codesign, hdiutil).
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "$(uname -m)" != "arm64" ]; then
  echo "murmur.app is built for Apple Silicon; run this on an arm64 Mac." >&2
  exit 1
fi

VERSION="$(uv version --short)"
APP="dist/murmur.app"
DMG="dist/murmur-${VERSION}-arm64.dmg"
START=$SECONDS

echo "==> Clean"
rm -rf build dist

echo "==> Install the build tools (dependency group: build)"
uv sync --locked --group build

echo "==> Icon and DMG background"
uv run python scripts/make_assets.py build

echo "==> Bundle with PyInstaller"
uv run pyinstaller packaging/murmur.spec --noconfirm --distpath dist --workpath build/pyi

echo "==> Ad-hoc sign"
# No Developer ID, so no notarization and no hardened runtime (which would also need
# entitlements for the JIT in numba and library validation for mlx's dylibs).
codesign --force --deep --sign - --timestamp=none "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> Disk image"
uv run dmgbuild -s packaging/dmg_settings.py \
  -D app="$APP" -D background=build/dmg-background.png -D icon=build/murmur.icns \
  murmur "$DMG"

echo "==> Done in $((SECONDS - START)) s"
du -sh "$APP" "$DMG"
