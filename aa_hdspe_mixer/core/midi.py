"""MIDI remote control: mutes, levels and preset recalls from a MIDI controller, with feedback to it.

App-wide, never in presets: ~/.config/aa-hdspe-aio-mixer/midi.json
  {"enabled": bool, "input": port name, "output": port name, "bindings": [binding, ...]}
Port names are "client:port" as alsaseq.py gives them ("" = none). A port that isn't there (a controller
not plugged in) stays chosen and is connected when it shows up.

A binding (see remote.py for what, target, invert, mode and the level limits) ties one MIDI message to one
thing in the mixer. MIDI's own parts:
  target  : "preset:number" - the preset picked by the program number or the CC value (0 = preset 1)
  message : "note", "cc", "program" or "pitchbend" (MESSAGES says which fit which `what`)
  channel : 1-16, or 0 = any channel
  number  : the note, controller or program number (0-127; programs are shown 1-128). Not used by pitchbend
            (there is one pitch bend per channel), kept 0.
  A press is a note-on, a CC value of 64 and up, or a bend up; "follow" mutes while the note is held, the CC
  value is 64 and up, or it's bent up.
A pitch bend is "bent up" from halfway up between the centre and the top (12288 of 0..16383) and toggles or
recalls once each time it gets there, not with every message on the way. For a level its full travel is
used (bottom to top, 14 bits), as motor faders that send pitch bend have it.

Feedback: when a mute or level changes (from MIDI, the window or a preset), the output port gets the value a
controller needs to show it: note-on velocity 127/0 or CC 127/0 for mutes (LEDs), the CC value or pitch bend
for levels (motor faders). A level moved from the controller isn't echoed back to it. Presets, and mutes on a
pitch bend, send no feedback.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from PySide6.QtCore import QSocketNotifier

from .alsaseq import ANNOUNCE, BEND_CENTRE, BEND_MAX, CC, NOTE_OFF, NOTE_ON, PITCH_BEND, PROGRAM, Msg, SeqPort
from .memory import atomic_write_json, read_json
from .remote import (DB_FLOOR, DB_TOP, FOLLOW, LEVEL, LEVEL_DELAY_MS, MODES, MUTE, PRESET, PRESET_BY_NUMBER,  # noqa: F401
                     PRESS_MUTE, PRESS_UNMUTE, TOGGLE, WHATS, RemoteControl, db_text, level_behaviour, level_db,
                     level_pos, limits_from, mute_change)
from .routing import MAX_DB

NOTE, CONTROLLER, PROGRAM_CHANGE, PITCHBEND = "note", "cc", "program", "pitchbend"
MESSAGES = {MUTE: (NOTE, CONTROLLER, PITCHBEND), LEVEL: (CONTROLLER, PITCHBEND),
            PRESET: (PROGRAM_CHANGE, NOTE, CONTROLLER, PITCHBEND)}
BEND_UP = (BEND_CENTRE + BEND_MAX + 1) // 2      # 12288: a pitch bend counts as "bent up" from here
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(n: int) -> str:
    """60 = C4."""
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


@dataclass
class Binding:
    id: str = ""
    what: str = MUTE
    target: str = ""
    message: str = NOTE
    channel: int = 0
    number: int = 0
    invert: bool = False
    mode: str = TOGGLE
    db_lo: float = -math.inf
    db_hi: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["db_lo"] = None if self.db_lo == -math.inf else self.db_lo
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Binding":
        def num(key, lo, hi, default):
            try:
                return max(lo, min(hi, int(d.get(key, default))))
            except (TypeError, ValueError):
                return default
        lo, hi = limits_from(d)
        return cls(id=str(d.get("id", "")), what=str(d.get("what", MUTE)), target=str(d.get("target", "")),
                   message=str(d.get("message", NOTE)), channel=num("channel", 0, 16, 0),
                   number=num("number", 0, 127, 0), invert=bool(d.get("invert", False)),
                   mode=d.get("mode") if d.get("mode") in MODES else TOGGLE, db_lo=lo, db_hi=hi)

    @property
    def by_number(self) -> bool:
        return self.what == PRESET and self.target == PRESET_BY_NUMBER

    @property
    def out_channel(self) -> int:
        """The channel feedback is sent on (channel 1 for "any")."""
        return self.channel - 1 if self.channel else 0


def validate(b: Binding) -> str:
    """"" or what's wrong with the binding, as a sentence for the user."""
    if b.what not in WHATS:
        return "Choose what to control."
    if not b.target:
        return {MUTE: "Choose a channel or connection to mute.", LEVEL: "Choose an output or connection.",
                PRESET: "Choose a preset."}[b.what]
    if b.message not in MESSAGES[b.what]:
        return {MUTE: "Mutes take a note, a controller (CC) or pitch bend.",
                LEVEL: "Levels take a controller (CC) or pitch bend.",
                PRESET: "Presets take a program change, a note, a controller (CC) or pitch bend."}[b.what]
    if b.by_number and b.message in (NOTE, PITCHBEND):
        return f"A {'note' if b.message == NOTE else 'pitch bend'} can't pick the preset by number: choose a preset."
    if not (0 <= b.channel <= 16 and 0 <= b.number <= 127):
        return "The MIDI channel is 1-16, the number 0-127."
    if b.what == LEVEL and not b.db_hi > b.db_lo:
        return "The top level must be above the bottom level."
    return ""


# ---------------------------------------------------------------------- the mapping (pure)
def matches(b: Binding, m: Msg) -> bool:
    if b.channel and m.channel != b.channel - 1:
        return False
    if b.message == NOTE:
        return m.kind in (NOTE_ON, NOTE_OFF) and m.number == b.number
    if b.message == CONTROLLER:
        return m.kind == CC and m.number == b.number
    if b.message == PROGRAM_CHANGE:
        return m.kind == PROGRAM and (b.by_number or m.number == b.number)
    if b.message == PITCHBEND:
        return m.kind == PITCH_BEND
    return False


def _high(b: Binding, m: Msg) -> bool:
    if b.message == NOTE:
        return m.kind == NOTE_ON
    return m.value >= (BEND_UP if b.message == PITCHBEND else 64)


def mute_from(b: Binding, m: Msg, muted: bool) -> bool | None:
    """The new mute state, or None when the message changes nothing (a release, unless it's "follow")."""
    return mute_change(b.mode, b.invert, _high(b, m), muted)


def level_from(b: Binding, m: Msg) -> float:
    p = m.value / (BEND_MAX if b.message == PITCHBEND else 127)
    db = level_db(1 - p if b.invert else p, b.db_lo, b.db_hi)
    return db if db == -math.inf else round(min(MAX_DB, db), 1)


def preset_from(b: Binding, m: Msg) -> int | None:
    """The preset slot (0-based) to recall, or None."""
    if b.by_number:
        return m.number if m.kind == PROGRAM else m.value
    slot = int(b.target.split(":")[1])
    if b.message == PROGRAM_CHANGE:
        return slot
    return slot if _high(b, m) else None


def feedback(b: Binding, state) -> Msg | None:
    """What tells the controller the state (muted: bool; level: dB), or None (presets)."""
    if b.what == MUTE and b.message != PITCHBEND:
        v = 127 if bool(state) != b.invert else 0
        return Msg(NOTE_ON if b.message == NOTE else CC, b.out_channel, b.number, v)
    if b.what == LEVEL:
        p = level_pos(state, b.db_lo, b.db_hi)
        p = 1 - p if b.invert else p
        if b.message == PITCHBEND:
            return Msg(PITCH_BEND, b.out_channel, 0, round(p * BEND_MAX))
        return Msg(CC, b.out_channel, b.number, round(p * 127))
    return None


def describe(m: Msg) -> str:
    """e.g. "CH01: CC 7 = 100", for the last message received."""
    if m.kind == CC:
        text = f"CC {m.number} = {m.value}"
    elif m.kind == PROGRAM:
        text = f"Program {m.number + 1}"
    elif m.kind == NOTE_ON:
        text = f"Note {m.number:03d} ({note_name(m.number)}) on"
    elif m.kind == NOTE_OFF:
        text = f"Note {m.number:03d} ({note_name(m.number)}) off"
    elif m.kind == PITCH_BEND:
        v = m.value - BEND_CENTRE
        text = f"Pitch bend {'+' if v > 0 else '−' if v < 0 else ''}{abs(v)}"
    else:
        return ""
    return f"CH{m.channel + 1:02d}: {text}"


def message_text(b: Binding) -> str:
    """e.g. "CH01: CC 7", "CH--: Note 060 (C4)", "CH02: Any program" (as describe() shows what's received)."""
    ch = f"CH{b.channel:02d}" if b.channel else "CH--"
    if b.message == NOTE:
        what = f"Note {b.number:03d} ({note_name(b.number)})"
    elif b.message == CONTROLLER:
        what = f"CC {b.number}"
    elif b.message == PITCHBEND:
        what = "Pitch bend"
    else:
        what = "Any program" if b.by_number else f"Program {b.number + 1}"
    return f"{ch}: {what}"


def behaviour_text(b: Binding) -> str:
    if b.what == MUTE:
        if b.mode != FOLLOW:
            does = {TOGGLE: "toggles", PRESS_MUTE: "mutes", PRESS_UNMUTE: "unmutes"}[b.mode]
            if b.message == PITCHBEND:
                return f"Each bend up {does}"
            return f"Each press {does}" + (" · lit when sounding" if b.invert else "")
        if b.message == NOTE:
            return "Sounding while held" if b.invert else "Muted while held"
        if b.message == PITCHBEND:
            return "Sounding while bent up" if b.invert else "Muted while bent up"
        return "Muted below 64" if b.invert else "Muted at 64 and up"
    if b.what == LEVEL:
        return level_behaviour(b)
    if b.by_number:
        return "Program 1 recalls preset 1, …" if b.message == PROGRAM_CHANGE else "Value 0 recalls preset 1, …"
    return "Recalled on each bend up" if b.message == PITCHBEND else ""


# ---------------------------------------------------------------------- settings file
class MidiSettings:
    def __init__(self, path: str):
        self.path = path
        self.enabled = False
        self.input = self.output = ""
        self.bindings: list[Binding] = []
        try:
            d = read_json(path)
        except (OSError, ValueError):
            d = {}
        if isinstance(d, dict):
            self.enabled = bool(d.get("enabled", False))
            self.input = str(d.get("input") or "")
            self.output = str(d.get("output") or "")
            for item in d.get("bindings") or []:
                if isinstance(item, dict):
                    b = Binding.from_dict(item)
                    if b.id and not validate(b) and all(x.id != b.id for x in self.bindings):
                        self.bindings.append(b)

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "input": self.input, "output": self.output,
                "bindings": [b.to_dict() for b in self.bindings]}

    def save(self):
        try:
            atomic_write_json(self.path, self.to_dict())
        except OSError:
            pass

    def next_id(self) -> str:
        n = max([int(b.id[1:]) for b in self.bindings if b.id[:1] == "b" and b.id[1:].isdigit()] + [0])
        return f"b{n + 1}"


# ---------------------------------------------------------------------- the controller
class MidiControl(RemoteControl):
    """Connects the chosen MIDI ports and applies the bindings (remote.py). `seq` is an alsaseq.SeqClient
    (or MockSeq)."""
    NAME = "MIDI"

    def __init__(self, engine, seq, path: str, parent=None):
        super().__init__(engine, MidiSettings(path), parent)
        self.seq = seq
        self.ports: list[SeqPort] = []
        self.in_port: SeqPort | None = None       # connected now
        self.out_port: SeqPort | None = None
        self._notifier: QSocketNotifier | None = None
        if self.settings.enabled:
            self._open()

    # ------------------------------------------------------------------ the sequencer, ports
    def _is_open(self) -> bool:
        return self.seq.is_open

    def _can_send(self) -> bool:
        return self.seq.is_open and self.out_port is not None

    def _send(self, m: Msg):
        self.seq.send(m)

    def _open(self):
        try:
            self.seq.open()
        except OSError as e:
            self.error = f"MIDI is off: couldn't open the ALSA sequencer ({e.strerror or e})."
            return
        self.error = ""
        fd = self.seq.fileno()
        if fd is not None:
            self._notifier = QSocketNotifier(fd, QSocketNotifier.Type.Read, self)
            self._notifier.activated.connect(self._on_readable)
        elif hasattr(self.seq, "wakeup"):
            self.seq.wakeup = self._on_readable           # the simulated sequencer
        self._sent.clear()
        self._refresh_ports()

    def _close_transport(self):
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        self.seq.close()
        self.ports, self.in_port, self.out_port = [], None, None

    def _refresh_ports(self):
        """Re-lists the ports, and connects a chosen port that has shown up."""
        if not self.seq.is_open:
            return
        try:
            ports = self.seq.ports()
        except OSError as e:
            ports, self.error = [], str(e)
        before = (self.ports, self.in_port, self.out_port, self.error)
        self.ports = ports
        if self.in_port is not None and self.in_port not in ports:
            self.in_port = None
        if self.out_port is not None and self.out_port not in ports:
            self.out_port = None
        if self.in_port is None and self.settings.input:
            self.in_port = self._connect(self.settings.input, True)
        if self.out_port is None and self.settings.output:
            self.out_port = self._connect(self.settings.output, False)
            if self.out_port is not None:          # a controller (re)connected: show it everything
                self._sent.clear()
                self._fb_timer.start()
        if (self.ports, self.in_port, self.out_port, self.error) != before:
            self.stateChanged.emit()

    def _connect(self, name: str, is_input: bool) -> SeqPort | None:
        p = next((p for p in self.ports if p.name == name and (p.readable if is_input else p.writable)), None)
        if p is None:
            return None
        try:
            (self.seq.connect_input if is_input else self.seq.connect_output)(p)
        except OSError as e:
            self.error = str(e)
            return None
        return p

    def set_port(self, name: str, is_input: bool):
        name = str(name or "")
        if name == (self.settings.input if is_input else self.settings.output):
            return
        cur = self.in_port if is_input else self.out_port
        if cur is not None:
            (self.seq.disconnect_input if is_input else self.seq.disconnect_output)(cur)
        if is_input:
            self.settings.input, self.in_port = name, None
        else:
            self.settings.output, self.out_port = name, None
        self.settings.save()
        self.error = ""
        self._refresh_ports()
        self.stateChanged.emit()

    def port_state(self, is_input: bool) -> str:
        """"none" (none chosen), "connected", "waiting" (chosen, not there) or "off"."""
        if not self.seq.is_open:
            return "off"
        name = self.settings.input if is_input else self.settings.output
        if not name:
            return "none"
        return "connected" if (self.in_port if is_input else self.out_port) is not None else "waiting"

    # ------------------------------------------------------------------ the mapping
    def _validate(self, b: Binding) -> str:
        return validate(b)

    def _tidy(self, b: Binding):
        if b.message == PITCHBEND:
            b.number = 0

    def _feedback(self, b: Binding, state):
        return feedback(b, state)

    def _level_db(self, b: Binding, m: Msg) -> float:
        return level_from(b, m)

    # ------------------------------------------------------------------ incoming
    def _on_readable(self, *_):
        announce = False
        for m in self.seq.read():
            if m.kind == ANNOUNCE:
                announce = True
                continue
            if not self._heard(m, describe(m), learnable=m.kind != NOTE_OFF):   # the press, not the release
                self.handle(m)
        if announce:
            self._refresh_ports()

    def handle(self, m: Msg):
        """Applies one incoming message to every binding it matches."""
        for b in list(self.settings.bindings):
            if not matches(b, m):
                continue
            if b.message == PITCHBEND and b.what != LEVEL:
                rising = self._rising(b, _high(b, m))
                if (b.what == PRESET or b.mode != FOLLOW) and not rising:
                    continue                       # once each time it gets bent up, not on every message
            if b.what == MUTE:
                self._set_mute(b, _high(b, m))
            elif b.what == LEVEL:
                self._queue_level(b, m, Msg(m.kind, b.out_channel, m.number, m.value))  # the control is there
            else:
                slot = preset_from(b, m)
                if slot is not None:
                    self._recall(slot)
