"""Keyboard shortcuts: mutes, levels and preset recalls from KDE Plasma's global shortcuts, which work
whichever window has the focus, and with the window in the tray. Works like MIDI (midi.py) and OSC (osc.py);
the shared part is remote.py. There is no feedback: a keyboard has nothing to show it on.

App-wide, never in presets: ~/.config/aa-hdspe-aio-mixer/shortcuts.json
  {"enabled": bool, "bindings": [binding, ...]}

Plasma's side: every binding is one action of the app's component ("aa-hdspe-aio-mixer", named after the
app) in Plasma's global shortcuts service (kglobalaccel, run by KWin), over D-Bus (Gio, python3-gi). So the
bindings are listed in System Settings › Keyboard › Shortcuts too, where their keys can be changed; the
panel follows. Plasma keeps the keys it was given; at start the app takes Plasma's (they may have been
changed there meanwhile). On: the actions are active (Plasma grabs their keys); off, and when the app quits:
inactive (the keys are free for anything else), as KDE's own apps do.

A binding (see remote.py for what, target, invert and mode) ties one key combination to one thing in the
mixer. Its own parts:
  key     : "Ctrl+Alt+M" (Qt's portable text), "" = none yet (set one here, or in System Settings)
  mode    : mute only, as remote.py. "follow" = muted while the keys are held (Plasma says when they're let go);
            invert then = sounding while held
  action  : level only. "up" / "down": by `step` dB, kept between db_lo and db_hi; "set": to `level` dB.
            With -inf at the bottom, the steps go between -60 dB and the top: one more step down is -inf
            (a connection's lowest is -90 dB), and one up from there is -60 dB.
  target  : "preset:5" (a preset recall: always one preset; there's no value to pick one by)
Plasma's own shortcuts come first: a key combination something else in Plasma has can't be used.

A key held down repeats; when Plasma tells when keys are let go, a repeat isn't another press.
"""
from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence

from .. import APP_ID, APP_NAME
from .memory import atomic_write_json, read_json
from .remote import (DB_FLOOR, DB_TOP, FOLLOW, LEVEL, MODES, MUTE, PRESET, TOGGLE, WHATS, RemoteControl, db_text,
                     mute_behaviour)
from .routing import MAX_DB, MIN_DB

UP, DOWN, SET = "up", "down", "set"
ACTIONS = (UP, DOWN, SET)
STEP_MIN, STEP_MAX, DEFAULT_STEP = 1, 24, 3
CURVE_FLOOR = -60.0        # with -inf at the bottom: the lowest step above -inf
COMPONENT = APP_ID
SERVICE, PATH, IFACE = "org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel"
COMPONENT_IFACE = "org.kde.kglobalaccel.Component"
SET_PRESENT, NO_AUTOLOADING = 2, 4      # KGlobalAccel::SetShortcutFlag
CALL_TIMEOUT_MS = 3000
MODIFIER_KEYS = {Qt.Key_Shift, Qt.Key_Control, Qt.Key_Meta, Qt.Key_Alt, Qt.Key_AltGr, Qt.Key_Super_L,
                 Qt.Key_Super_R, Qt.Key_Hyper_L, Qt.Key_Hyper_R, Qt.Key_CapsLock, Qt.Key_NumLock,
                 Qt.Key_ScrollLock, Qt.Key_unknown}
MODIFIERS = (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
             | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier
             | Qt.KeyboardModifier.KeypadModifier)


# ---------------------------------------------------------------------- keys
def key_code(text: str) -> int:
    """"Ctrl+Alt+M" → Qt's int for it (key | modifiers), 0 for none or what isn't one key combination."""
    seq = QKeySequence.fromString(text or "", QKeySequence.SequenceFormat.PortableText)
    if seq.count() != 1 or seq[0].key() in (Qt.Key_unknown, 0):
        return 0
    return seq[0].toCombined()


def key_text(code: int, native: bool = False) -> str:
    """Qt's int → "Ctrl+Alt+M" (portable: as saved; native: as shown, in the desktop's words), "" for 0."""
    if not code:
        return ""
    fmt = QKeySequence.SequenceFormat.NativeText if native else QKeySequence.SequenceFormat.PortableText
    return QKeySequence(code).toString(fmt)


def combination(key: int, modifiers: int) -> str:
    """A key press (Qt key, modifiers, as QML's KeyEvent has them) → its portable text, or "" while it's only
    modifiers (or no key at all)."""
    if key in MODIFIER_KEYS or not key or key > 0x01FFFFFF:
        return ""
    mods = Qt.KeyboardModifier(int(modifiers)) & MODIFIERS
    if key == Qt.Key_Backtab:                         # Shift+Tab arrives as Backtab
        key = Qt.Key_Tab
        mods |= Qt.KeyboardModifier.ShiftModifier
    return key_text(int(key) | mods.value)


def needs_modifier(key: int, modifiers: int) -> bool:
    """A key on its own (or with only Shift) that would be taken from every window: letters, digits, the space
    bar, Tab, Return, the arrows, … Function keys (F1-F35) and the extra keys (media, volume, launch) may be
    used alone."""
    mods = Qt.KeyboardModifier(int(modifiers)) & (Qt.KeyboardModifier.ControlModifier
                                                  | Qt.KeyboardModifier.AltModifier
                                                  | Qt.KeyboardModifier.MetaModifier)
    if mods != Qt.KeyboardModifier.NoModifier:
        return False
    return not (Qt.Key_F1.value <= key <= Qt.Key_F35.value or key >= Qt.Key_Back.value)


def shown(text: str) -> str:
    """A saved key combination as the desktop writes it ("Ctrl+Alt+M"; Meta is "Meta")."""
    return key_text(key_code(text), native=True) if text else ""


# ---------------------------------------------------------------------- bindings
@dataclass
class KeyBinding:
    id: str = ""
    what: str = MUTE
    target: str = ""
    key: str = ""
    invert: bool = False
    mode: str = TOGGLE
    action: str = UP
    step: int = DEFAULT_STEP
    level: float = 0.0
    db_lo: float = -math.inf
    db_hi: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["db_lo"] = None if self.db_lo == -math.inf else self.db_lo
        d["level"] = None if self.level == -math.inf else self.level
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "KeyBinding":
        def num(key, default, lo, hi, none=None):
            v = d.get(key, default)
            if v is None and none is not None:
                return none
            try:
                return max(lo, min(hi, float(v)))
            except (TypeError, ValueError):
                return default
        key = str(d.get("key") or "")
        return cls(id=str(d.get("id", "")), what=str(d.get("what", MUTE)), target=str(d.get("target", "")),
                   key=key_text(key_code(key)), invert=bool(d.get("invert", False)),
                   mode=d.get("mode") if d.get("mode") in MODES else TOGGLE,
                   action=d.get("action") if d.get("action") in ACTIONS else UP,
                   step=int(num("step", DEFAULT_STEP, STEP_MIN, STEP_MAX)),
                   level=num("level", 0.0, DB_FLOOR, DB_TOP, none=-math.inf),
                   db_lo=num("db_lo", -math.inf, DB_FLOOR, DB_TOP, none=-math.inf),
                   db_hi=num("db_hi", 0.0, DB_FLOOR, DB_TOP))


def validate(b: KeyBinding) -> str:
    """"" or what's wrong with the binding, as a sentence for the user (Plasma's side is checked on saving)."""
    if b.what not in WHATS:
        return "Choose what to control."
    if not b.target:
        return {MUTE: "Choose a channel or connection to mute.", LEVEL: "Choose an output or connection.",
                PRESET: "Choose a preset."}[b.what]
    if b.what == PRESET and not b.target.split(":")[-1].isdigit():
        return "Choose a preset."
    if b.key and not key_code(b.key):
        return "That isn't a key combination."
    if b.what == LEVEL and b.action != SET and not b.db_hi > b.db_lo:
        return "The top level must be above the bottom level."
    return ""


def friendly(b: KeyBinding, target_text: str) -> str:
    """The action's name in System Settings, e.g. "Mute: Input AN 1/2", "Level up 3 dB: Output AES"."""
    if b.what == MUTE:
        does = {TOGGLE: "Mute or unmute", FOLLOW: "Mute while held", "press_mute": "Mute",
                "press_unmute": "Unmute"}.get(b.mode, "Mute")
        if b.mode == FOLLOW and b.invert:
            does = "Sound while held"
    elif b.what == LEVEL:
        does = {UP: f"Level up {b.step} dB", DOWN: f"Level down {b.step} dB",
                SET: f"Level to {db_text(b.level)}"}[b.action]
    else:
        does = "Recall"
    return f"{does}: {target_text}"


def step_level(b: KeyBinding, cur: float) -> float:
    """The level after one press of a level binding, from the level now (dB, maybe -inf)."""
    if b.action == SET:
        return b.level
    lo, hi = b.db_lo, b.db_hi
    floor = lo if lo != -math.inf else min(hi, CURVE_FLOOR)
    if b.action == UP:
        if cur != -math.inf and cur >= hi:
            return cur                                # at the top already (or above it, from the window)
        new = min(hi, floor if cur == -math.inf or cur < floor else cur + b.step)
    else:
        if cur == -math.inf or (lo != -math.inf and cur <= lo):
            return cur
        new = min(cur - b.step, hi)
        if new < floor:
            new = floor if lo != -math.inf or cur > floor else -math.inf
    return new if new == -math.inf else round(min(MAX_DB, new), 1)


def message_text(b: KeyBinding) -> str:
    """e.g. "Ctrl+Alt+M", for the box."""
    return shown(b.key) or "No keys yet"


def behaviour_text(b: KeyBinding) -> str:
    if b.what == MUTE:
        return mute_behaviour(b.mode, b.invert, "press", "while held")
    if b.what == LEVEL:
        if b.action == SET:
            return f"Each press sets {db_text(b.level)}"
        updown = "up" if b.action == UP else "down"
        return f"Each press: {b.step} dB {updown}, {db_text(b.db_lo)} … {db_text(b.db_hi)}"
    return ""


class KeySettings:
    def __init__(self, path: str):
        self.path = path
        self.enabled = False
        self.bindings: list[KeyBinding] = []
        try:
            d = read_json(path)
        except (OSError, ValueError):
            d = {}
        if isinstance(d, dict):
            self.enabled = bool(d.get("enabled", False))
            for item in d.get("bindings") or []:
                if isinstance(item, dict):
                    b = KeyBinding.from_dict(item)
                    if b.id and not validate(b) and all(x.id != b.id for x in self.bindings):
                        self.bindings.append(b)

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "bindings": [b.to_dict() for b in self.bindings]}

    def save(self):
        try:
            atomic_write_json(self.path, self.to_dict())
        except OSError:
            pass

    def next_id(self) -> str:
        n = max([int(b.id[1:]) for b in self.bindings if b.id[:1] == "k" and b.id[1:].isdigit()] + [0])
        return f"k{n + 1}"


# ---------------------------------------------------------------------- Plasma's global shortcuts (D-Bus)
class PlasmaShortcuts(QObject):
    """The app's component in kglobalaccel, over the session D-Bus with Gio (whose callbacks run in Qt's event
    loop, which is GLib's). Actions are by their unique name (the binding's id) and friendly name; keys are
    Qt's ints (one key combination each). Without Plasma or python3-gi: available is False, error says why."""
    pressed = Signal(str)               # action
    released = Signal(str)              # action
    keyChanged = Signal(str, int)       # action, its key now (changed in System Settings), 0 = none
    availableChanged = Signal()

    def __init__(self, parent=None, bus=None):
        super().__init__(parent)
        self.error = ""
        self._conn = None
        self._owner = ""
        self._subs: list[int] = []
        self._watch = 0
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except (ImportError, ValueError):
            # The AppImage has its own Python, without PyGObject (no wheels): the system's python3-gi can't help.
            self.error = ("Keyboard shortcuts aren't available in the AppImage: they need the app's package for "
                          "your distribution." if os.environ.get("APPIMAGE") else
                          "Keyboard shortcuts need python3-gi (GObject introspection for Python) to reach Plasma.")
            return
        self._Gio, self._GLib = Gio, GLib
        try:
            self._conn = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as e:
            self.error = f"Keyboard shortcuts: no session D-Bus ({e.message})."
            return
        flags = Gio.DBusSignalFlags.NONE
        sub = self._conn.signal_subscribe
        self._subs = [
            sub(SERVICE, COMPONENT_IFACE, "globalShortcutPressed", None, COMPONENT, flags,
                lambda *a: self._on_signal(self.pressed, a[5])),
            sub(SERVICE, COMPONENT_IFACE, "globalShortcutReleased", None, COMPONENT, flags,
                lambda *a: self._on_signal(self.released, a[5])),
            sub(SERVICE, IFACE, "yourShortcutGotChanged", PATH, None, flags, lambda *a: self._on_changed(a[5])),
            sub(SERVICE, IFACE, "yourShortcutsChanged", PATH, None, flags, lambda *a: self._on_changed(a[5])),
        ]
        self._watch = Gio.bus_watch_name_on_connection(self._conn, SERVICE, Gio.BusNameWatcherFlags.NONE,
                                                       self._appeared, self._vanished)

    @property
    def available(self) -> bool:
        return bool(self._owner)

    def _appeared(self, _conn, _name, owner):
        self._owner, self.error = owner, ""
        self.availableChanged.emit()

    def _vanished(self, _conn, _name):
        was = self._owner
        self._owner = ""
        if self._conn is not None:
            self.error = "Plasma's global shortcuts service (kglobalaccel) isn't running: keyboard shortcuts need " \
                         "KDE Plasma."
        if was or self._conn is not None:
            self.availableChanged.emit()

    def _on_signal(self, sig, params):
        try:
            component, action = params.unpack()[:2]
        except (TypeError, ValueError):
            return
        if component == COMPONENT:
            sig.emit(action)

    def _on_changed(self, params):
        try:
            action_id, keys = params.unpack()
        except (TypeError, ValueError):
            return
        if len(action_id) >= 2 and action_id[0] == COMPONENT:
            # ai (one int per key combination) or a(ai) (key sequences: up to 4 combinations; the first is it)
            first = [k if isinstance(k, int) else (k[0][0] if k and k[0] else 0) for k in keys]
            self.keyChanged.emit(action_id[1], next((k for k in first if k), 0))

    def _call(self, method: str, sig: str, args: tuple, iface: str = IFACE, path: str = PATH):
        """A call; its reply unpacked, or None (and error set) when it fails."""
        if not self.available:
            return None
        try:
            r = self._conn.call_sync(SERVICE, path, iface, method, self._GLib.Variant(sig, args), None,
                                     self._Gio.DBusCallFlags.NO_AUTO_START, CALL_TIMEOUT_MS, None)
        except self._GLib.Error as e:
            self.error = f"Plasma's shortcuts: {e.message}"
            return None
        return r.unpack() if r is not None else ()

    @staticmethod
    def _id(action: str, name: str) -> list[str]:
        return [COMPONENT, action, APP_NAME, name]

    def set_key(self, action: str, name: str, key: int, active: bool, autoload: bool = False) -> int | None:
        """Registers the action and gives it `key` (0 = none). With autoload, a key Plasma has kept for it
        wins. Returns the key it has now (0 when Plasma refused it: taken), None when Plasma can't be reached."""
        aid = self._id(action, name)
        if self._call("doRegister", "(as)", (aid,)) is None:
            return None
        flags = (SET_PRESENT if active else 0) | (0 if autoload else NO_AUTOLOADING)
        r = self._call("setShortcut", "(asaiu)", (aid, [key] if key else [], flags))
        if r is None:
            return None
        if not active:
            self._call("setInactive", "(as)", (aid,))
        return next((k for k in r[0] if k), 0)

    def set_inactive(self, action: str, name: str):
        self._call("setInactive", "(as)", (self._id(action, name),))

    def unregister(self, action: str):
        self._call("unregister", "(ss)", (COMPONENT, action))

    def owner_of(self, key: int) -> str:
        """Who else in Plasma has this key combination ("KWin: Walk Through Windows"), or ""."""
        r = self._call("getGlobalShortcutsByKey", "(i)", (key,))
        for info in (r[0] if r else []):
            if len(info) >= 4 and info[2] != COMPONENT:
                return f"{info[3] or info[2]}: {info[1] or info[0]}"
        return ""

    def block(self, on: bool):
        """While on, Plasma's shortcuts don't act (for recording one in the window, as KDE's apps do)."""
        self._call("blockGlobalShortcuts", "(b)", (bool(on),))

    def close(self):
        if self._conn is not None:
            for s in self._subs:
                self._conn.signal_unsubscribe(s)
            self._subs = []
            if self._watch:
                self._Gio.bus_unwatch_name(self._watch)
                self._watch = 0


class MockPlasma(QObject):
    """PlasmaShortcuts without Plasma (the mock card, tests): the same methods and signals, in memory.
    press() / release() are a key pressed and let go; others = key combinations other components have."""
    pressed = Signal(str)
    released = Signal(str)
    keyChanged = Signal(str, int)
    availableChanged = Signal()

    def __init__(self, parent=None, available: bool = True):
        super().__init__(parent)
        self.available = available
        self.error = "" if available else "Plasma's global shortcuts service (kglobalaccel) isn't running."
        self.keys: dict[str, int] = {}
        self.names: dict[str, str] = {}
        self.active: set[str] = set()
        self.others: dict[int, str] = {}
        self.blocked = False

    def set_key(self, action, name, key, active, autoload=False):
        if not self.available:
            return None
        if not (autoload and action in self.keys):
            self.keys[action] = 0 if key in self.others else key
        self.names[action] = name
        (self.active.add if active else self.active.discard)(action)
        return self.keys[action]

    def set_inactive(self, action, name):
        self.active.discard(action)

    def unregister(self, action):
        self.keys.pop(action, None)
        self.names.pop(action, None)
        self.active.discard(action)

    def owner_of(self, key):
        return self.others.get(key, "")

    def block(self, on):
        self.blocked = bool(on)

    def close(self):
        pass

    def _fires(self, action):
        return self.available and action in self.active and not self.blocked

    def press(self, action):
        if self._fires(action):
            self.pressed.emit(action)

    def release(self, action):
        if self._fires(action):
            self.released.emit(action)

    def change(self, action, key):
        """As System Settings does."""
        self.keys[action] = key
        self.keyChanged.emit(action, key)

    def set_available(self, on):
        self.available = bool(on)
        self.error = "" if on else "Plasma's global shortcuts service (kglobalaccel) isn't running."
        self.availableChanged.emit()


# ---------------------------------------------------------------------- the controller
class ShortcutControl(RemoteControl):
    """Applies the bindings (remote.py) when their keys are pressed. `plasma` is a PlasmaShortcuts (or a test
    double with the same methods and signals); `names` gives a binding's friendly name for System Settings
    (a function of the binding; by default its target)."""
    NAME = "Keyboard shortcuts"

    def __init__(self, engine, plasma, path: str, names=None, parent=None):
        super().__init__(engine, KeySettings(path), parent)
        self.plasma = plasma
        self.names = names or (lambda b: friendly(b, b.target))
        self.recording = False
        self._releases = False                     # Plasma has said a key was let go: repeats can be told
        self._held: set[str] = set()
        self._named: dict[str, str] = {}           # binding id -> the name Plasma was given
        plasma.pressed.connect(self._on_pressed)
        plasma.released.connect(self._on_released)
        plasma.keyChanged.connect(self._on_key_changed)
        plasma.availableChanged.connect(self._publish)
        self._publish()

    # ------------------------------------------------------------------ Plasma's side
    def _is_open(self) -> bool:
        return self.settings.enabled and self.plasma.available

    def _publish(self):
        """Every binding to Plasma (active while on), taking the keys Plasma has kept (System Settings). One whose
        keys were removed there while the app wasn't running is deleted, as _on_key_changed does."""
        changed = False
        if self.plasma.available:
            for b in list(self.settings.bindings):
                self._named[b.id] = self.names(b)
                got = self.plasma.set_key(b.id, self._named[b.id], key_code(b.key), self.settings.enabled,
                                          autoload=True)
                if got is None:
                    continue
                if not got and b.key and not self.plasma.owner_of(key_code(b.key)):   # not taken: removed
                    self._drop(b)
                elif key_text(got) != b.key and (got or not b.key):
                    b.key = key_text(got)
                    changed = True
        self.error = "" if self.plasma.available else self.plasma.error
        if changed:
            self.settings.save()
            self.bindingsChanged.emit()
        self.stateChanged.emit()

    def _open(self):
        self._publish()

    def _close_transport(self):
        self.set_recording(False)
        self._held.clear()
        if self.plasma.available:
            for b in self.settings.bindings:
                self.plasma.set_inactive(b.id, self.names(b))

    def _close(self):
        super()._close()
        self.error = "" if self.plasma.available else self.plasma.error

    def shutdown(self):
        self._close_transport()                    # the keys are free while the app isn't running

    def _before_replace(self):                     # an import: the old bindings leave Plasma too
        if self.plasma.available:
            for b in self.settings.bindings:
                self.plasma.unregister(b.id)
        self._named.clear()
        self._held.clear()

    def _after_replace(self):
        if not self.settings.enabled:              # on: _open() gives them to Plasma
            self._publish()

    def rename_all(self):
        """The friendly names again, where they changed (a channel renamed, a preset stored): System Settings
        shows them."""
        if self.plasma.available:
            for b in self.settings.bindings:
                name = self.names(b)
                if self._named.get(b.id) != name:
                    self._named[b.id] = name
                    self.plasma.set_key(b.id, name, key_code(b.key), self.settings.enabled, autoload=True)

    def _on_key_changed(self, action: str, key: int):
        """Keys changed in System Settings: taken. Keys removed there delete the binding (a binding saved here
        without keys stays, to be given some later)."""
        b = self.binding(action)
        if b is None or key_text(key) == b.key:
            return
        if not key:
            self._drop(b)
            return
        b.key = key_text(key)
        self.settings.save()
        self.bindingsChanged.emit()

    def _drop(self, b: KeyBinding):
        name = self._named.get(b.id) or self.names(b)
        self.remove_binding(b.id)                  # and from System Settings' list
        self.e.message.emit("info", f"Removed the keyboard shortcut “{name}”: "
                                    "its keys were removed in System Settings.")

    # ------------------------------------------------------------------ bindings
    def _validate(self, b: KeyBinding) -> str:
        err = validate(b)
        if err or not b.key:
            return err
        other = next((x for x in self.settings.bindings if x.key == b.key and x.id != b.id), None)
        if other is not None:
            return f"{shown(b.key)} is already a shortcut here: {self.names(other)}."
        owner = self.plasma.owner_of(key_code(b.key)) if self.plasma.available else ""
        if owner:
            return f"{shown(b.key)} is already a Plasma shortcut ({owner})."
        return ""

    def save_binding(self, b: KeyBinding) -> str:
        err = super().save_binding(b)
        if err or not self.plasma.available:
            return err
        self._named[b.id] = self.names(b)
        got = self.plasma.set_key(b.id, self._named[b.id], key_code(b.key), self.settings.enabled)
        if got is not None and key_text(got) != b.key:
            if b.key and not got:
                self.e.message.emit("warning", f"Plasma didn't take {shown(b.key)}: something else has it.")
            b.key = key_text(got)
            self.settings.save()
            self.bindingsChanged.emit()
        self.error = self.plasma.error if got is None else ""
        return ""

    def remove_binding(self, bid: str):
        if self.binding(bid) is not None and self.plasma.available:
            self.plasma.unregister(bid)
        self._named.pop(bid, None)
        super().remove_binding(bid)

    def set_recording(self, on: bool):
        """Recording a key combination in the window: Plasma's shortcuts (and these) don't act meanwhile."""
        on = bool(on) and self.plasma.available
        if on != self.recording:
            self.recording = on
            self.plasma.block(on)

    # ------------------------------------------------------------------ incoming
    def _on_pressed(self, action: str):
        b = self.binding(action)
        if b is None or not self.settings.enabled or self.recording:
            return
        repeat = self._releases and action in self._held
        self._held.add(action)
        self._heard(None, f"{shown(b.key) or action}: pressed", learnable=False)
        if repeat and b.what != LEVEL:              # a held key repeats; levels keep stepping
            return
        self.handle(b, True)

    def _on_released(self, action: str):
        self._releases = True
        self._held.discard(action)
        b = self.binding(action)
        if b is None or not self.settings.enabled or self.recording:
            return
        self._heard(None, f"{shown(b.key) or action}: let go", learnable=False)
        if b.what == MUTE and b.mode == FOLLOW:
            self.handle(b, False)

    def handle(self, b: KeyBinding, down: bool):
        if b.what == MUTE:
            self._set_mute(b, down)
        elif b.what == LEVEL:
            if down:
                self._step(b)
        elif down:
            self._recall(int(b.target.split(":")[1]))

    def _step(self, b: KeyBinding):
        t, cur = self.resolve(b), self.state(b)
        if t is None:
            return
        new = step_level(b, cur)
        if t[0] == "unit":
            if new != cur:
                self.e.set_output_level(t[1], new)
        else:                                      # a connection is never removed from here
            new = max(MIN_DB, new)
            if new != cur:
                self.e.set_connection_db(t[1], t[2], new)


__all__ = ["KeyBinding", "KeySettings", "ShortcutControl", "PlasmaShortcuts", "MockPlasma", "validate", "friendly",
           "step_level", "message_text", "behaviour_text", "key_code", "key_text", "combination", "needs_modifier", "shown",
           "UP", "DOWN", "SET", "ACTIONS", "STEP_MIN", "STEP_MAX", "DEFAULT_STEP", "COMPONENT"]
