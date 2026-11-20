#!/bin/sh
# Builds dist/aa-hdspe-aio-mixer-<version>-1.<dist>.noarch.rpm. Run on Fedora (GitHub Actions: a fedora
# container), with rpm-build installed. Usage: packaging/fedora/build.sh
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
VERSION=$("$ROOT/packaging/version.sh")
TOP="$ROOT/build/rpm"

rm -rf "$TOP"
rpmbuild -bb \
    --define "_topdir $TOP" \
    --define "app_version $VERSION" \
    --define "app_source $ROOT" \
    ${PKG_RELEASE:+--define "pkg_release $PKG_RELEASE"} \
    "$ROOT/packaging/fedora/aa-hdspe-aio-mixer.spec"

mkdir -p "$ROOT/dist"
find "$TOP/RPMS" -name '*.rpm' -exec cp {} "$ROOT/dist/" \; -exec sh -c 'echo "$1"' _ {} \;
