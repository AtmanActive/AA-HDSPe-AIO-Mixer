#!/bin/sh
# Builds dist/aa-hdspe-aio-mixer_<version>-1_all.deb, for Debian 13 and later (and Ubuntu 24.04 and later).
# Needs only dpkg-deb. Usage: packaging/debian/build.sh
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
APP=aa-hdspe-aio-mixer
VERSION=$(sh "$ROOT/packaging/version.sh")
REL=${PKG_RELEASE:-1}
WORK="$ROOT/build/debian"
PKG="$WORK/$APP"

rm -rf "$WORK"
sh "$ROOT/packaging/stage.sh" "$PKG"

# Debian's place for the license.
mv "$PKG/usr/share/doc/$APP/LICENSE" "$PKG/usr/share/doc/$APP/copyright"

install -d "$PKG/DEBIAN"
SIZE=$(du -sk --exclude=DEBIAN "$PKG" | cut -f1)
cat > "$PKG/DEBIAN/control" <<EOF
Package: $APP
Version: $VERSION-$REL
Architecture: all
Maintainer: AtmanActive <AtmanActive@users.noreply.github.com>
Installed-Size: $SIZE
Section: sound
Priority: optional
Homepage: https://github.com/AtmanActive/AA-HDSPe-AIO-Mixer
Depends: python3 (>= 3.11),
 python3-pyside6.qtcore, python3-pyside6.qtgui, python3-pyside6.qtwidgets, python3-pyside6.qtnetwork,
 python3-pyside6.qtqml, python3-pyside6.qtquick, python3-pyside6.qtquickcontrols2, python3-pyside6.qtsvg,
 qml6-module-qtquick, qml6-module-qtquick-controls, qml6-module-qtquick-layouts,
 qml6-module-qtquick-window, qml6-module-qtquick-templates, qt6-svg-plugins,
 libasound2t64 | libasound2
Recommends: python3-gi, gir1.2-glib-2.0, pipewire-bin, alsa-utils
Description: Routing-focused hardware mixer for RME HDSPe AIO and AIO Pro audio cards
 Routes the card's inputs and playback channels to its outputs, with levels,
 mutes, presets, meters, the card's and clock's settings, virtual PipeWire
 devices, and remote control over MIDI, OSC and keyboard shortcuts.
 .
 Works with the in-kernel snd-hdspm driver and the out-of-tree snd-hdspe driver.
 python3-gi is for keyboard shortcuts (KDE Plasma), pipewire-bin for virtual
 PipeWire devices, and alsa-utils for the test tone.
EOF

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/${APP}_${VERSION}-${REL}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$OUT"
echo "$OUT"
