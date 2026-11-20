"""Remote control, shared by MIDI (midi.py) and OSC (osc.py): what a binding can do to the mixer, and the
controller that applies bindings to the engine and sends feedback.

A binding ties one incoming message to one thing in the mixer:
  what    : "mute"   - a channel's or a connection's mute
            "level"  - an output's level, or a connection's level
            "preset" - recall a preset (all of it, as the Recall dialog's defaults)
  target  : "in:0", "pb:8", "out:6"  a channel, by its kind and first hardware channel, so it follows the
                                     channel when it is joined into or split from a stereo pair
            "in:0>out:6"             a connection (only acts while that connection exists)
            "preset:5"               preset slot 6 (0-based)
            "preset:number"          the preset picked by the message's value
  invert  : mute: high = sounding instead of muted; level: the top end of the control is the bottom level
  mode    : mute only. "toggle": each press toggles; "press_mute" / "press_unmute": each press mutes /
            unmutes (whatever it was); "follow": muted while held (high)
  db_lo, db_hi : level only. The level at the bottom and the top of the control; db_lo may be -inf.
            With finite limits the level moves evenly in dB. With -inf at the bottom it follows a mixing
            desk's fader curve (amplitude ~ travel³): 0 dB of a 0 dB top is at the top, -6 dB at about four
            fifths, -18 dB at half, -60 dB at a tenth. A connection is never removed: its lowest is -90 dB.

Feedback: when a mute or level changes (from a controller, the window or a preset), the controller is sent
what shows it (lights, motor faders). A level moved from the controller isn't echoed back to it. Presets send
no feedback.
"""
from __future__ import annotations

import math
import os

from PySide6.QtCore import QObject, QTimer, Signal

from .memory import atomic_write_json
from .routing import MIN_DB

MUTE, LEVEL, PRESET = "mute", "level", "preset"
WHATS = (MUTE, LEVEL, PRESET)
TOGGLE, FOLLOW, PRESS_MUTE, PRESS_UNMUTE = "toggle", "follow", "press_mute", "press_unmute"
MODES = (TOGGLE, FOLLOW, PRESS_MUTE, PRESS_UNMUTE)
PRESET_BY_NUMBER = "preset:number"
DB_FLOOR = -90            # lowest finite limit
DB_TOP = 6                # highest limit (the mixer's maximum is +6.02 dB)
LEVEL_DELAY_MS = 15       # a moving fader's messages are applied at most this often (the last one wins)


def db_text(db: float) -> str:
    if db == -math.inf:
        return "−∞ dB"
    return f"{'+' if db > 0 else '−' if db < 0 else ''}{abs(db):g} dB"


def mute_change(mode: str, invert: bool, high: bool, muted: bool) -> bool | None:
    """The new mute state, or None when the message changes nothing (a release, unless it's "follow")."""
    if mode == FOLLOW:
        return high != invert
    if not high:
        return None
    return {TOGGLE: not muted, PRESS_MUTE: True, PRESS_UNMUTE: False}.get(mode, not muted)


def level_db(p: float, lo: float, hi: float) -> float:
    """Position 0..1 of the control → level in dB (see the top of this file)."""
    p = max(0.0, min(1.0, p))
    if lo == -math.inf:
        return -math.inf if p <= 0 else hi + 60 * math.log10(p)
    return lo + (hi - lo) * p


def level_pos(db: float, lo: float, hi: float) -> float:
    if db == -math.inf:
        return 0.0
    p = 10 ** ((db - hi) / 60) if lo == -math.inf else (db - lo) / (hi - lo)
    return max(0.0, min(1.0, p))


def limits_from(d: dict) -> tuple[float, float]:
    """db_lo, db_hi from a saved binding (db_lo None = -inf), clamped to DB_FLOOR..DB_TOP."""
    lo = d.get("db_lo")
    try:
        lo = -math.inf if lo is None else max(DB_FLOOR, min(DB_TOP, float(lo)))
        hi = max(DB_FLOOR, min(DB_TOP, float(d.get("db_hi", 0.0))))
    except (TypeError, ValueError):
        lo, hi = -math.inf, 0.0
    return lo, hi


def mute_behaviour(mode: str, invert: bool, press: str, held: str) -> str:
    """e.g. "Each press toggles", "Muted while held"; press/held say what a press and a hold are."""
    if mode != FOLLOW:
        does = {TOGGLE: "toggles", PRESS_MUTE: "mutes", PRESS_UNMUTE: "unmutes"}.get(mode, "toggles")
        return f"Each {press} {does}"
    return f"{'Sounding' if invert else 'Muted'} {held}"


def level_behaviour(b) -> str:
    lo, hi = db_text(b.db_lo), db_text(b.db_hi)
    return f"{hi} … {lo} (inverted)" if b.invert else f"{lo} … {hi}"


class RemoteControl(QObject):
    """Applies bindings (with what/target/invert/mode/db_lo/db_hi and an id) to the engine, from the GUI
    thread, and sends feedback. Works whether or not the window is shown. A subclass brings the transport:
    _open, _close_transport, _is_open, _can_send, _send, and the message mapping: _validate, _feedback,
    _level_db. `settings` has enabled, bindings, save() and next_id()."""
    NAME = ""                       # "MIDI", "OSC": in messages to the user
    stateChanged = Signal()         # on/off, ports, which are connected, errors
    bindingsChanged = Signal()
    activityChanged = Signal()      # the last message received
    learned = Signal(object)        # the message heard while learning

    def __init__(self, engine, settings, parent=None):
        super().__init__(parent)
        self.e = engine
        self.settings = settings
        self.error = ""
        self.last = ""
        self.learning = False
        self._sent: dict = {}                      # binding id -> feedback last sent
        self._levels: dict = {}                    # binding id -> last level message, not applied yet
        self._was_high: dict[str, bool] = {}       # binding id -> its last message was high (edges)
        self._level_timer = QTimer(self, singleShot=True, interval=LEVEL_DELAY_MS, timeout=self._apply_levels)
        self._fb_timer = QTimer(self, singleShot=True, interval=0, timeout=self._send_feedback)
        self._act_timer = QTimer(self, singleShot=True, interval=100, timeout=lambda: self.activityChanged.emit())
        for sig in (engine.mutesChanged, engine.outputLevelsChanged, engine.routingChanged, engine.layoutChanged):
            sig.connect(self._fb_timer.start)

    # ------------------------------------------------------------------ for the subclass
    def _open(self): ...
    def _close_transport(self): ...
    def _is_open(self) -> bool: return False
    def _can_send(self) -> bool: return False
    def _send(self, m): ...
    def _validate(self, b) -> str: return ""
    def _tidy(self, b): ...
    def _feedback(self, b, state): return None
    def _level_db(self, b, m) -> float: return -math.inf

    # ------------------------------------------------------------------ on / off
    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def set_enabled(self, on: bool):
        on = bool(on)
        if on != self.settings.enabled:
            self.settings.enabled = on
            self.settings.save()
            self._open() if on else self._close()
            self.stateChanged.emit()

    def _close(self):
        self._close_transport()
        self.error, self.learning = "", False
        self._levels.clear()
        self._was_high.clear()

    def shutdown(self):
        self._close()

    # ------------------------------------------------------------------ export / import (File menu)
    def export(self) -> dict:
        """On/off, the transport's settings and the bindings, as in its settings file."""
        return self.settings.to_dict()

    def replace(self, data: dict):
        """Everything from an exported file (export()): what's here now is replaced. Read through the settings
        class, so what it wouldn't load from its own file is dropped here too."""
        self._close()
        self._before_replace()
        old = self.settings
        tmp = old.path + ".import"
        atomic_write_json(tmp, data if isinstance(data, dict) else {})
        try:
            new = type(old)(tmp)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        new.path = old.path
        new.save()
        self.settings = new
        self._sent.clear()
        self._levels.clear()
        self._was_high.clear()
        self._after_replace()
        if new.enabled:
            self._open()
        self.bindingsChanged.emit()
        self.stateChanged.emit()
        self._fb_timer.start()

    def _before_replace(self): ...
    def _after_replace(self): ...

    # ------------------------------------------------------------------ bindings
    def bindings(self) -> list:
        return list(self.settings.bindings)

    def binding(self, bid: str):
        return next((b for b in self.settings.bindings if b.id == bid), None)

    def save_binding(self, b) -> str:
        """Adds (no id) or replaces a binding. Returns "" or what's wrong (nothing is saved then)."""
        err = self._validate(b)
        if err:
            return err
        self._tidy(b)
        if b.id and self.binding(b.id) is not None:
            self.settings.bindings = [b if x.id == b.id else x for x in self.settings.bindings]
        else:
            b.id = self.settings.next_id()
            self.settings.bindings.append(b)
        self.settings.save()
        self._forget(b.id)
        self.bindingsChanged.emit()
        self._fb_timer.start()
        return ""

    def remove_binding(self, bid: str):
        if self.binding(bid) is not None:
            self.settings.bindings = [b for b in self.settings.bindings if b.id != bid]
            self.settings.save()
            self._forget(bid)
            self.bindingsChanged.emit()

    def _forget(self, bid: str):
        self._sent.pop(bid, None)
        self._levels.pop(bid, None)
        self._was_high.pop(bid, None)

    def set_learning(self, on: bool):
        self.learning = bool(on) and self._is_open()

    # ------------------------------------------------------------------ incoming
    def _heard(self, m, text: str, learnable: bool = True) -> bool:
        """Every message received: shown as the last one; while learning, a learnable one is taken (and
        True is returned: don't act on it)."""
        self.last = text
        if not self._act_timer.isActive():
            self._act_timer.start()
        if self.learning:
            if learnable:
                self.learning = False
                self.learned.emit(m)
            return True
        return False

    def _rising(self, b, high: bool) -> bool:
        """True when this message is high and the binding's last one wasn't (once per press)."""
        was = self._was_high.get(b.id, False)
        self._was_high[b.id] = high
        return high and not was

    def _unit_key(self, spec: str) -> str | None:
        """"out:7" → the key of the unit holding output 7 now ("out:6" when 6+7 is a stereo pair)."""
        try:
            kind, hw = spec.split(":")
            hw = int(hw)
        except ValueError:
            return None
        u = next((u for u in self.e.units(kind) if any(c.hw == hw for c in u.channels)), None)
        return u.key if u else None

    def resolve(self, b) -> tuple | None:
        """("unit", key) or ("conn", src key, dst key) for a mute or level binding, None if not present now."""
        if ">" in b.target:
            s, d = (self._unit_key(x) for x in b.target.split(">", 1))
            if s is None or d is None or self.e.connection_db(s, d) == -math.inf:
                return None
            return ("conn", s, d)
        key = self._unit_key(b.target)
        if key is None or (b.what == LEVEL and not key.startswith("out:")):
            return None
        return ("unit", key)

    def state(self, b):
        """A mute binding's target muted (bool), a level binding's level (dB), or None if not present."""
        t = self.resolve(b)
        if t is None:
            return None
        if b.what == MUTE:
            return self.e.unit_muted(t[1]) if t[0] == "unit" else self.e.connection_muted(t[1], t[2])
        if t[0] == "unit":
            return self.e.output_level_db(int(t[1].split(":")[1]))
        return self.e.connection_db(t[1], t[2])

    def _set_mute(self, b, high: bool):
        t, cur = self.resolve(b), self.state(b)
        if t is None:
            return
        new = mute_change(b.mode, b.invert, high, cur)
        if new is None or new == cur:
            if new is not None:                    # nothing to change, but the controller may think so
                self._sent.pop(b.id, None)
                self._fb_timer.start()
            return
        if t[0] == "unit":
            self.e.set_unit_mute(t[1], new)
        else:
            self.e.set_connection_mute(t[1], t[2], new)

    def _queue_level(self, b, m, echo):
        """A level message, applied with the others shortly (the last one wins). `echo` is the feedback the
        level will give, which the control shows already."""
        self._levels[b.id] = m
        self._sent[b.id] = echo
        if not self._level_timer.isActive():
            self._level_timer.start()

    def _apply_levels(self):
        pending, self._levels = self._levels, {}
        for bid, m in pending.items():
            b = self.binding(bid)
            t = self.resolve(b) if b else None
            if t is None:
                continue
            db = self._level_db(b, m)
            if self.state(b) == db:
                continue
            if t[0] == "unit":
                self.e.set_output_level(t[1], db)
            else:                                  # a connection is never removed from here
                self.e.set_connection_db(t[1], t[2], max(MIN_DB, db))

    def _recall(self, slot: int):
        if not self.e.recall_preset(slot):
            self.e.message.emit("warning", f"{self.NAME}: there is no preset {slot + 1} to recall.")

    # ------------------------------------------------------------------ feedback
    def _send_feedback(self):
        if not self._can_send():
            return
        for b in self.settings.bindings:
            if b.what == PRESET or b.id in self._levels:     # a level still to apply: the control is there
                continue
            st = self.state(b)
            m = self._feedback(b, st) if st is not None else None
            if m is not None and self._sent.get(b.id) != m:
                self._send(m)
                self._sent[b.id] = m

    def flush(self):
        """Applies waiting level messages and sends waiting feedback now (tests)."""
        self._level_timer.stop()
        self._apply_levels()
        self._fb_timer.stop()
        self._send_feedback()
