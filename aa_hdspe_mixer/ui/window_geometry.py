"""Remember the main window's size, position and maximised state between runs.

Stored app-wide (not per card) in ~/.config/aa-hdspe-aio-mixer/window.json, saved when the window is hidden
to the tray and when the app quits, restored before the window is first shown.

Sizes are in Qt's logical pixels, which the app's zoom (Qt scale factor) enlarges, so the zoom they were
saved at is stored too: after a zoom change the window keeps its size on screen.

Wayland: the compositor places windows, so a client can't set its own position. There the size and
maximised state come back; the position only does on X11 (or under XWayland).
"""
from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QGuiApplication, QWindow

from ..core.memory import atomic_write_json, config_dir, read_json


def geometry_path() -> str:
    return os.path.join(config_dir(), "window.json")


def capture(window: QWindow, previous: dict | None = None, zoom: float = 1.0) -> dict:
    maximized = bool(window.windowStates() & Qt.WindowState.WindowMaximized)
    geo = dict(previous or {})
    geo["maximized"] = maximized
    # Hidden in the tray (or minimised) right now: the "last" start option starts in the tray then.
    geo["hidden"] = (not window.isVisible()
                     or bool(window.windowStates() & Qt.WindowState.WindowMinimized))
    # The maximised flag survives hiding to the tray (Qt keeps the window states of a hidden window).
    if not maximized:                       # keep the normal size of a maximised window from before
        geo.update(x=window.x(), y=window.y(), width=window.width(), height=window.height(), zoom=zoom)
    return geo


def save(window: QWindow, path: str, zoom: float = 1.0):
    previous = load(path)
    try:
        atomic_write_json(path, capture(window, previous, zoom))
    except OSError:
        pass                                # not worth bothering the user about


def load(path: str) -> dict | None:
    try:
        d = read_json(path)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def fit(geo: dict, screens: list[QRect], min_w: int, min_h: int, zoom: float = 1.0) -> tuple[QRect, bool]:
    """Saved geometry adjusted to the screens there are now and to the zoom now (`zoom`; the saved one is
    geo["zoom"]): the size is clamped to fit, and the position is kept only if the window's top-left corner
    is on some screen. Returns (rect, keep_position)."""
    k = float(geo.get("zoom", 1.0) or 1.0) / zoom          # saved logical px -> logical px now
    w = max(min_w, round((geo.get("width", 0) or 0) * k))
    h = max(min_h, round((geo.get("height", 0) or 0) * k))
    x, y = round((geo.get("x", 0) or 0) * k), round((geo.get("y", 0) or 0) * k)
    home = next((s for s in screens if s.contains(QPoint(x + 20, y + 20))), None)
    if screens:
        biggest = home or max(screens, key=lambda s: s.width() * s.height())
        w, h = min(w, biggest.width()), min(h, biggest.height())
    return QRect(x, y, w, h), home is not None


def restore(window: QWindow, path: str, zoom: float = 1.0) -> bool:
    """Apply the saved geometry to the (not yet shown) window. Returns True if it should be maximised."""
    geo = load(path)
    if not geo:
        return False
    screens = [s.availableGeometry() for s in QGuiApplication.screens()]
    if geo.get("width") and geo.get("height"):
        rect, keep_pos = fit(geo, screens, window.minimumWidth(), window.minimumHeight(), zoom)
        window.resize(rect.width(), rect.height())
        if keep_pos:
            window.setPosition(rect.x(), rect.y())      # ignored by Wayland compositors
    return bool(geo.get("maximized"))
