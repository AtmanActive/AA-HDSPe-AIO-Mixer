#!/bin/sh
# Lays the app out under DESTDIR as the distribution packages (.deb, .rpm, Arch) install it:
#   /usr/bin/aa-hdspe-aio-mixer                                   launcher
#   /usr/share/aa-hdspe-aio-mixer/aa_hdspe_mixer/                 the app
#   /usr/share/applications/aa-hdspe-aio-mixer.desktop            menu entry
#   /usr/share/icons/hicolor/scalable/apps/aa-hdspe-aio-mixer.svg icon
#   /usr/share/doc/aa-hdspe-aio-mixer/                            README, LICENSE
# The app lives outside Python's site-packages, so the packages don't depend on one Python version.
# Usage: packaging/stage.sh DESTDIR
set -eu

DESTDIR=${1:?usage: stage.sh DESTDIR}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
APP=aa-hdspe-aio-mixer

install -d "$DESTDIR/usr/bin" "$DESTDIR/usr/share/$APP" "$DESTDIR/usr/share/applications" \
           "$DESTDIR/usr/share/icons/hicolor/scalable/apps" "$DESTDIR/usr/share/doc/$APP"

# The app, without Python's caches.
tar -C "$ROOT" --exclude='__pycache__' --exclude='*.pyc' -cf - aa_hdspe_mixer | tar -C "$DESTDIR/usr/share/$APP" -xf -
find "$DESTDIR/usr/share/$APP" -type d -exec chmod 755 {} +
find "$DESTDIR/usr/share/$APP" -type f -exec chmod 644 {} +

cat > "$DESTDIR/usr/bin/$APP" <<EOF
#!/bin/sh
export PYTHONPATH=/usr/share/$APP\${PYTHONPATH:+:\$PYTHONPATH}
exec /usr/bin/python3 -m aa_hdspe_mixer "\$@"
EOF
chmod 755 "$DESTDIR/usr/bin/$APP"

install -m 644 "$ROOT/packaging/$APP.desktop" "$DESTDIR/usr/share/applications/$APP.desktop"
install -m 644 "$ROOT/aa_hdspe_mixer/resources/icon_fader_3d.svg" \
               "$DESTDIR/usr/share/icons/hicolor/scalable/apps/$APP.svg"
install -m 644 "$ROOT/README.md" "$ROOT/LICENSE" "$DESTDIR/usr/share/doc/$APP/"
