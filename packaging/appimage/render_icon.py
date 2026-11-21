"""Renders an SVG to a square PNG with Qt: the AppImage's .DirIcon, which appimagetool wants as a PNG.
Run with the AppImage's own Python (its PySide6), so the build needs no SVG converter.
Usage: python3 render_icon.py ICON.svg OUT.png [SIZE]"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

src, out = sys.argv[1], sys.argv[2]
size = int(sys.argv[3]) if len(sys.argv) > 3 else 256
app = QGuiApplication(sys.argv[:1])
renderer = QSvgRenderer(src)
if not renderer.isValid():
    sys.exit(f"Not a valid SVG: {src}")
image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
image.fill(Qt.GlobalColor.transparent)
painter = QPainter(image)
renderer.render(painter)
painter.end()
if not image.save(out, "PNG"):
    sys.exit(f"Couldn't write {out}")
