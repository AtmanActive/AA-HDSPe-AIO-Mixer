#!/bin/sh
# Builds dist/AA-HDSPe-AIO-Mixer-<version>-x86_64.AppImage: the app with its own Python and Qt (PySide6
# wheels), for any x86_64 distribution with glibc 2.28 or later. Downloads, from GitHub and PyPI:
#   - a relocatable Python (github.com/niess/python-appimage, manylinux_2_28 build)
#   - PySide6-Essentials from PyPI
#   - appimagetool (github.com/AppImage/appimagetool)
# The system still provides libasound (always there), PipeWire's tools and aplay, as for the packages.
# Keyboard shortcuts need PyGObject, which has no wheels, so they're off in the AppImage (the app says so).
# Usage: packaging/appimage/build.sh
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
APP=aa-hdspe-aio-mixer
VERSION=$("$ROOT/packaging/version.sh")
PY=3.12                                  # the Python inside the AppImage
PYSIDE="PySide6-Essentials>=6.8,<6.9"    # the Qt the app is tested with (Debian 13 has 6.8)
WORK="$ROOT/build/appimage"
APPDIR="$WORK/AppDir"
export APPIMAGE_EXTRACT_AND_RUN=1         # run the AppImages used here without FUSE (CI has none)

rm -rf "$WORK"
mkdir -p "$WORK" "$ROOT/dist"
cd "$WORK"

# ---- Python
# With a token (GitHub Actions passes one), GitHub's API limits are per repository, not per shared runner IP.
if [ -n "${GITHUB_TOKEN:-}" ]; then set -- -H "Authorization: Bearer $GITHUB_TOKEN"; else set --; fi
url=$(curl -fsSL "$@" "https://api.github.com/repos/niess/python-appimage/releases/tags/python$PY" \
      | grep -o '"browser_download_url": *"[^"]*manylinux_2_28_x86_64\.AppImage"' | head -n1 | cut -d'"' -f4)
[ -n "$url" ] || { echo "No Python $PY AppImage found at niess/python-appimage" >&2; exit 1; }
echo "Python: $url"
curl -fsSL -o python.AppImage "$url"
chmod +x python.AppImage
./python.AppImage --appimage-extract >/dev/null
mv squashfs-root "$APPDIR"

# Python's own launcher, menu entry and icons go: the app brings its own.
rm -f "$APPDIR/AppRun" "$APPDIR"/*.desktop "$APPDIR"/*.png "$APPDIR"/*.svg "$APPDIR/.DirIcon"
rm -rf "$APPDIR/usr/share/applications" "$APPDIR/usr/share/metainfo" "$APPDIR/usr/share/icons"
# A fixed path for AppRun, whatever the Python's patch version.
PYDIR=$(cd "$APPDIR/opt" && ls -d python3.* | head -n1)
ln -s "$PYDIR" "$APPDIR/opt/python"
PYBIN="$APPDIR/opt/python/bin/python$PY"
[ -e "$APPDIR/opt/python/bin/python3" ] || ln -s "python$PY" "$APPDIR/opt/python/bin/python3"

# ---- Qt
"$PYBIN" -m pip install --no-cache-dir --no-warn-script-location --disable-pip-version-check "$PYSIDE"
# Qt's developer tools and headers: not needed to run the app.
SITE=$("$PYBIN" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
( cd "$SITE/PySide6" && rm -rf include typesystems glue scripts doc examples \
    assistant designer linguist lrelease lupdate qmlformat qmllint qmlls qmltestrunner Designer* \
    Qt/libexec/qmlcachegen Qt/libexec/qmlimportscanner Qt/libexec/qmltyperegistrar \
    Qt/lib/libQt6Designer* Qt/plugins/designer )
rm -rf "$SITE"/pip "$SITE"/pip-* "$APPDIR/opt/python/bin/pip"*

# ---- the app
install -d "$APPDIR/usr/share/$APP"
tar -C "$ROOT" --exclude='__pycache__' --exclude='*.pyc' -cf - aa_hdspe_mixer | tar -C "$APPDIR/usr/share/$APP" -xf -
"$PYBIN" -m compileall -q "$APPDIR/usr/share/$APP"     # the AppImage is read-only: Python can't cache later
install -m 755 "$ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"
install -m 644 "$ROOT/packaging/$APP.desktop" "$APPDIR/$APP.desktop"
install -m 644 "$ROOT/aa_hdspe_mixer/resources/icon_fader_3d.svg" "$APPDIR/$APP.svg"
ln -s "$APP.svg" "$APPDIR/.DirIcon"

# ---- pack
curl -fsSL -o appimagetool.AppImage \
    https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x appimagetool.AppImage
OUT="$ROOT/dist/AA-HDSPe-AIO-Mixer-$VERSION-x86_64.AppImage"
ARCH=x86_64 ./appimagetool.AppImage --no-appstream "$APPDIR" "$OUT"
echo "$OUT"
