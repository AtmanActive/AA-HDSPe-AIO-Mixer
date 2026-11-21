#!/bin/sh
# Builds dist/aa-hdspe-aio-mixer-<version>-1-any.pkg.tar.zst, for Arch Linux and CachyOS. Run on Arch
# (GitHub Actions: an archlinux container) as a normal user: makepkg refuses root.
# Usage: packaging/arch/build.sh
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK="$ROOT/build/arch"

rm -rf "$WORK"
mkdir -p "$WORK" "$ROOT/dist"
cp "$ROOT/packaging/arch/PKGBUILD" "$WORK/"
cd "$WORK"
# --nodeps: nothing is compiled, so the runtime dependencies needn't be installed to build.
APP_VERSION=$(sh "$ROOT/packaging/version.sh") APP_SOURCE="$ROOT" PKGDEST="$ROOT/dist" PKGEXT=.pkg.tar.zst \
    makepkg --force --nodeps --noconfirm
ls "$ROOT"/dist/*.pkg.tar.zst
