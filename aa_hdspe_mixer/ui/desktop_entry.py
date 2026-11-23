"""User-local desktop entry and icon, so Wayland compositors show the app's icon.

On Wayland the window icon is not taken from QWindow::setWindowIcon: the compositor (KWin) matches the
window's app_id (QGuiApplication::desktopFileName) to <app_id>.desktop and uses its Icon= entry. Without
that file KWin shows its generic "W" icon. This writes, only when missing or outdated:
  ~/.local/share/applications/<app_id>.desktop
  ~/.local/share/icons/hicolor/scalable/apps/<app_id>.svg
It also gives the app a launcher in the application menu.

Installed from a package (.deb, .rpm, Arch package), the package has its own entry in /usr/share: then
nothing is written, and an entry this file wrote earlier (running from the source) is removed, so it doesn't
hide the package's; so is its icon, which would otherwise stand in for the package's icon (a user-local icon
comes first in the theme lookup). From an AppImage, the entry starts that AppImage.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .. import APP_ID, APP_NAME

# The Exec= line of an entry written for a run from the source; how an entry of ours is told from the user's.
_SOURCE_EXEC = "Exec=env PYTHONPATH="


def _data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def _system_entry() -> str | None:
    """The package's desktop entry, if the app is installed system-wide."""
    for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
        path = os.path.join(d, "applications", f"{APP_ID}.desktop") if d else ""
        if path and os.path.isfile(path):
            return path
    return None


def _write_if_changed(path: str, data: bytes) -> bool:
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return True


def _remove_source_entry(entry_path: str) -> bool:
    """Remove a user-local entry written for a run from the source. Returns True if one was removed."""
    try:
        with open(entry_path, encoding="utf-8", errors="replace") as f:
            ours = any(line.startswith(_SOURCE_EXEC) for line in f)
        if ours:
            os.remove(entry_path)
        return ours
    except OSError:
        return False


def _icon_path() -> str:
    return os.path.join(_data_home(), "icons", "hicolor", "scalable", "apps", f"{APP_ID}.svg")


def _remove_icon_if_unused(entry_path: str) -> bool:
    """Remove the user-local icon once no user-local entry of ours is left to show it. Returns True if removed."""
    if os.path.exists(entry_path):          # e.g. an AppImage's entry, which names this icon by path
        return False
    try:
        os.remove(_icon_path())
        return True
    except OSError:
        return False


def _exec_line() -> str:
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return 'Exec="' + appimage.replace("\\", "\\\\").replace('"', '\\"') + '"'
    pkg_parent = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return f"{_SOURCE_EXEC}{pkg_parent} {sys.executable} -m aa_hdspe_mixer"


def _refresh_menu():
    tool = shutil.which("kbuildsycoca6") or shutil.which("update-desktop-database")
    if tool:
        args = [tool] if tool.endswith("kbuildsycoca6") else [tool, os.path.join(_data_home(), "applications")]
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_desktop_entry(icon_svg: str) -> bool:
    """Install/refresh the entry and icon. Returns True if anything changed. Never raises."""
    try:
        entry_path = os.path.join(_data_home(), "applications", f"{APP_ID}.desktop")
        if not os.environ.get("APPIMAGE") and _system_entry():
            changed = _remove_source_entry(entry_path)
            changed |= _remove_icon_if_unused(entry_path)
            if changed:
                _refresh_menu()
            return changed
        with open(icon_svg, "rb") as f:
            icon = f.read()
        icon_path = _icon_path()
        entry = "\n".join([
            "[Desktop Entry]",
            "Type=Application",
            f"Name={APP_NAME}",
            "GenericName=Hardware Mixer",
            "Comment=Routing mixer for the RME HDSPe AIO and AIO Pro",
            _exec_line(),
            # A full path, not the theme name: a name has to be found through the icon theme and its caches,
            # which on some systems (seen on CachyOS) left the launcher showing a blank icon.
            f"Icon={icon_path}",
            "Terminal=false",
            "Categories=AudioVideo;Audio;Mixer;",
            "Keywords=RME;HDSPe;AIO;mixer;routing;TotalMix;",
            "StartupNotify=true",
            "",
        ]).encode()
        changed = _write_if_changed(icon_path, icon)
        changed |= _write_if_changed(entry_path, entry)
        if changed:
            _refresh_menu()
        return changed
    except OSError:
        return False
