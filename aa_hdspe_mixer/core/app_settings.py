"""The app's own settings (cog button), app-wide rather than per card: ~/.config/aa-hdspe-aio-mixer/settings.json.

  start_window : how the window starts
                 "minimized" - straight into the system tray, window not shown
                 "maximized" - shown maximised
                 "window"    - shown as a normal window, at its remembered size (default)
                 "last"      - as it was left at the last quit: in the tray, maximised or normal
  color_mode   : the app's colours
                 "system"    - follow the desktop's light/dark colour scheme, live (default)
                 "light", "dark"
  zoom         : size of the whole app, level -7 … +7 (ZOOM_PERCENT: 25 % … 400 %), 0 = 100 % (default).
                 Applied at start (Qt's scale factor), so a change takes effect after a restart.
"""
from __future__ import annotations

import os

from .memory import atomic_write_json, config_dir, read_json

START_WINDOW_CHOICES = ("minimized", "maximized", "window", "last")
START_WINDOW_LABELS = {"minimized": "Minimized (system tray)", "maximized": "Maximized", "window": "Window",
                       "last": "Last (as it was left)"}
COLOR_MODE_CHOICES = ("system", "light", "dark")
COLOR_MODE_LABELS = {"system": "System", "light": "Light", "dark": "Dark"}
ZOOM_CHOICES = tuple(range(-7, 8))
ZOOM_PERCENT = {-7: 25, -6: 30, -5: 40, -4: 50, -3: 60, -2: 75, -1: 80, 0: 100,
                1: 125, 2: 150, 3: 175, 4: 200, 5: 250, 6: 300, 7: 400}
DEFAULTS = {"start_window": "window", "color_mode": "system", "zoom": 0}
CHOICES = {"start_window": START_WINDOW_CHOICES, "color_mode": COLOR_MODE_CHOICES, "zoom": ZOOM_CHOICES}


def zoom_percent(level: int) -> int:
    """-7 … +7 → 25 % … 400 %."""
    return ZOOM_PERCENT[level]


def zoom_factor(level: int) -> float:
    return zoom_percent(level) / 100


def zoom_label(level: int) -> str:
    return f"{zoom_percent(level)}%"


def settings_path() -> str:
    return os.path.join(config_dir(), "settings.json")


class AppSettings:
    def __init__(self, path: str | None = None):
        self.path = path or settings_path()
        self.values = dict(DEFAULTS)
        try:
            d = read_json(self.path)
        except (OSError, ValueError):
            d = {}
        if isinstance(d, dict):
            for key, choices in CHOICES.items():
                v = d.get(key)
                if type(v) is type(choices[0]) and v in choices:     # (no bool for an int setting)
                    self.values[key] = d[key]

    def _set(self, key: str, value) -> bool:
        if type(value) is not type(CHOICES[key][0]) or value not in CHOICES[key]:
            return False
        if value != self.values[key]:
            self.values[key] = value
            self.save()
        return True

    @property
    def start_window(self) -> str:
        return self.values["start_window"]

    def set_start_window(self, value: str) -> bool:
        return self._set("start_window", value)

    @property
    def color_mode(self) -> str:
        return self.values["color_mode"]

    def set_color_mode(self, value: str) -> bool:
        return self._set("color_mode", value)

    @property
    def zoom(self) -> int:
        return self.values["zoom"]

    def set_zoom(self, value: int) -> bool:
        return self._set("zoom", value)

    def save(self):
        try:
            atomic_write_json(self.path, self.values)
        except OSError:
            pass


def start_mode(setting: str, last: dict | None) -> str:
    """What to do at start: "tray", "maximized" or "normal". `last` = the saved window state (window.json)."""
    if setting == "minimized":
        return "tray"
    if setting == "maximized":
        return "maximized"
    if setting == "last" and last:
        if last.get("hidden"):
            return "tray"
        if last.get("maximized"):
            return "maximized"
    return "normal"


def is_dark(mode: str, system_dark: bool) -> bool:
    """Whether the app draws dark, for colour mode `mode` and the desktop's own scheme."""
    return system_dark if mode not in ("light", "dark") else mode == "dark"
