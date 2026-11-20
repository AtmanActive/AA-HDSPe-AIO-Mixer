"""Minimal ctypes binding to libasound's sequencer API (snd_seq_*), enough for MIDI remote control (midi.py).

The app opens one sequencer client, "AA-HDSPe-AIO-Mixer", with two ports that other apps can connect to as
well (qpwgraph, aconnect):
  "Control In"   : MIDI from a controller (written to us), plus the system announcements (ports coming and
                   going), so a controller plugged in later is connected when it shows up;
  "Feedback Out" : MIDI to the controller (LEDs, motor faders).
The client is non-blocking: fileno() is for a QSocketNotifier, read() returns what has arrived.
Ports are named "client name:port name"; numbers change between boots, names don't.

MockSeq is a stand-in with one simulated controller, for --mock and tests: it touches no ALSA.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
from dataclasses import dataclass

from .alsa import AlsaError

OPEN_DUPLEX = 3
NONBLOCK = 1
CAP_READ, CAP_WRITE, CAP_SUBS_READ, CAP_SUBS_WRITE, CAP_NO_EXPORT = 1, 2, 32, 64, 128
TYPE_MIDI_GENERIC, TYPE_APPLICATION = 1 << 1, 1 << 20
CLIENT_SYSTEM, PORT_SYSTEM_ANNOUNCE = 0, 1
ADDRESS_SUBSCRIBERS, QUEUE_DIRECT = 254, 253
EV_NOTEON, EV_NOTEOFF, EV_CONTROLLER, EV_PGMCHANGE, EV_PITCHBEND = 6, 7, 10, 11, 13
EV_ANNOUNCE = range(60, 68)          # client/port start, exit, change; port (un)subscribed
POLLIN = 1

CLIENT_NAME = "AA-HDSPe-AIO-Mixer"
IN_PORT_NAME = "Control In"
OUT_PORT_NAME = "Feedback Out"

# Message kinds (Msg.kind)
NOTE_ON, NOTE_OFF, CC, PROGRAM, PITCH_BEND = "note_on", "note_off", "cc", "program", "pitchbend"
ANNOUNCE = "announce"
BEND_CENTRE, BEND_MAX = 8192, 16383     # a pitch bend's value: 0..16383, 8192 = the centre (no bend)


@dataclass(frozen=True)
class Msg:
    """One MIDI message. channel 0-15; number = note, controller or program (0 for a pitch bend);
    value = velocity, CC value, or a pitch bend's 0..16383 (8192 = centre). A note-on with velocity 0 is read
    as a note-off."""
    kind: str
    channel: int = 0
    number: int = 0
    value: int = 0


@dataclass(frozen=True)
class SeqPort:
    client: int
    port: int
    client_name: str
    port_name: str
    readable: bool        # a MIDI source: we can read from it (a controller's output)
    writable: bool        # a MIDI destination: we can write to it (a controller's input)

    @property
    def name(self) -> str:
        """What is stored: stays the same across boots, unlike the numbers."""
        return f"{self.client_name}:{self.port_name}"

    @property
    def text(self) -> str:
        """What is shown."""
        if self.port_name.startswith(self.client_name):
            return self.port_name
        return f"{self.client_name} · {self.port_name}"


class _Addr(ctypes.Structure):
    _fields_ = [("client", ctypes.c_ubyte), ("port", ctypes.c_ubyte)]


class _Note(ctypes.Structure):
    _fields_ = [("channel", ctypes.c_ubyte), ("note", ctypes.c_ubyte), ("velocity", ctypes.c_ubyte),
                ("off_velocity", ctypes.c_ubyte), ("duration", ctypes.c_uint)]


class _Ctrl(ctypes.Structure):
    _fields_ = [("channel", ctypes.c_ubyte), ("unused", ctypes.c_ubyte * 3), ("param", ctypes.c_uint),
                ("value", ctypes.c_int)]


class _Data(ctypes.Union):
    _fields_ = [("note", _Note), ("control", _Ctrl), ("addr", _Addr), ("raw32", ctypes.c_uint * 3)]


class _Time(ctypes.Union):
    _fields_ = [("tick", ctypes.c_uint), ("real", ctypes.c_uint * 2)]


class SeqEvent(ctypes.Structure):
    """snd_seq_event_t (28 bytes)."""
    _fields_ = [("type", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("tag", ctypes.c_ubyte),
                ("queue", ctypes.c_ubyte), ("time", _Time), ("source", _Addr), ("dest", _Addr), ("data", _Data)]


assert ctypes.sizeof(SeqEvent) == 28


class _PollFd(ctypes.Structure):
    _fields_ = [("fd", ctypes.c_int), ("events", ctypes.c_short), ("revents", ctypes.c_short)]


_p = ctypes.c_void_p
_pp = ctypes.POINTER(ctypes.c_void_p)
_i, _u, _s = ctypes.c_int, ctypes.c_uint, ctypes.c_char_p
_lib = None


def _load():
    global _lib
    if _lib is not None:
        return _lib
    lib = ctypes.CDLL(ctypes.util.find_library("asound") or "libasound.so.2")
    sig = {
        "snd_strerror": (_s, [_i]),
        "snd_seq_open": (_i, [_pp, _s, _i, _i]),
        "snd_seq_close": (_i, [_p]),
        "snd_seq_set_client_name": (_i, [_p, _s]),
        "snd_seq_client_id": (_i, [_p]),
        "snd_seq_create_simple_port": (_i, [_p, _s, _u, _u]),
        "snd_seq_connect_from": (_i, [_p, _i, _i, _i]),
        "snd_seq_connect_to": (_i, [_p, _i, _i, _i]),
        "snd_seq_disconnect_from": (_i, [_p, _i, _i, _i]),
        "snd_seq_disconnect_to": (_i, [_p, _i, _i, _i]),
        "snd_seq_poll_descriptors_count": (_i, [_p, ctypes.c_short]),
        "snd_seq_poll_descriptors": (_i, [_p, ctypes.POINTER(_PollFd), _u, ctypes.c_short]),
        "snd_seq_event_input": (_i, [_p, ctypes.POINTER(ctypes.POINTER(SeqEvent))]),
        "snd_seq_event_output_direct": (_i, [_p, ctypes.POINTER(SeqEvent)]),
        "snd_seq_client_info_malloc": (_i, [_pp]),
        "snd_seq_client_info_free": (None, [_p]),
        "snd_seq_client_info_set_client": (None, [_p, _i]),
        "snd_seq_client_info_get_client": (_i, [_p]),
        "snd_seq_client_info_get_name": (_s, [_p]),
        "snd_seq_query_next_client": (_i, [_p, _p]),
        "snd_seq_port_info_malloc": (_i, [_pp]),
        "snd_seq_port_info_free": (None, [_p]),
        "snd_seq_port_info_set_client": (None, [_p, _i]),
        "snd_seq_port_info_set_port": (None, [_p, _i]),
        "snd_seq_port_info_get_port": (_i, [_p]),
        "snd_seq_port_info_get_name": (_s, [_p]),
        "snd_seq_port_info_get_capability": (_u, [_p]),
        "snd_seq_query_next_port": (_i, [_p, _p]),
    }
    for name, (res, args) in sig.items():
        f = getattr(lib, name)
        f.restype, f.argtypes = res, args
    _lib = lib
    return lib


def _check(err: int, what: str) -> int:
    if err < 0:
        raise AlsaError(-err, f"{what}: {_load().snd_strerror(err).decode()}")
    return err


class SeqClient:
    """The app's sequencer client. Not open until open(); every call raises AlsaError on failure."""

    def __init__(self):
        self._h = ctypes.c_void_p()
        self.id = -1
        self.in_port = self.out_port = -1
        self._ev = SeqEvent()

    @property
    def is_open(self) -> bool:
        return bool(self._h)

    def open(self, name: str = CLIENT_NAME):
        if self._h:
            return
        lib = _load()
        h = ctypes.c_void_p()
        _check(lib.snd_seq_open(ctypes.byref(h), b"default", OPEN_DUPLEX, NONBLOCK), "open the ALSA sequencer")
        try:
            _check(lib.snd_seq_set_client_name(h, name.encode()), "name the sequencer client")
            self.id = _check(lib.snd_seq_client_id(h), "sequencer client id")
            kind = TYPE_MIDI_GENERIC | TYPE_APPLICATION
            self.in_port = _check(lib.snd_seq_create_simple_port(
                h, IN_PORT_NAME.encode(), CAP_WRITE | CAP_SUBS_WRITE, kind), "create the MIDI input port")
            self.out_port = _check(lib.snd_seq_create_simple_port(
                h, OUT_PORT_NAME.encode(), CAP_READ | CAP_SUBS_READ, kind), "create the MIDI output port")
            # Ports coming and going, so a controller plugged in later can be connected.
            _check(lib.snd_seq_connect_from(h, self.in_port, CLIENT_SYSTEM, PORT_SYSTEM_ANNOUNCE),
                   "listen to the sequencer's announcements")
        except AlsaError:
            lib.snd_seq_close(h)
            raise
        self._h = h

    def close(self):
        if self._h:
            _load().snd_seq_close(self._h)
            self._h = ctypes.c_void_p()
            self.id = self.in_port = self.out_port = -1

    def fileno(self) -> int | None:
        if not self._h:
            return None
        lib = _load()
        n = lib.snd_seq_poll_descriptors_count(self._h, POLLIN)
        if n < 1:
            return None
        fds = (_PollFd * n)()
        lib.snd_seq_poll_descriptors(self._h, fds, n, POLLIN)
        return fds[0].fd

    def ports(self) -> list[SeqPort]:
        """Other clients' ports that can be connected to (not the system's, not ours)."""
        if not self._h:
            return []
        lib = _load()
        ci, pi = ctypes.c_void_p(), ctypes.c_void_p()
        _check(lib.snd_seq_client_info_malloc(ctypes.byref(ci)), "client_info_malloc")
        _check(lib.snd_seq_port_info_malloc(ctypes.byref(pi)), "port_info_malloc")
        out = []
        try:
            lib.snd_seq_client_info_set_client(ci, -1)
            while lib.snd_seq_query_next_client(self._h, ci) >= 0:
                client = lib.snd_seq_client_info_get_client(ci)
                if client in (CLIENT_SYSTEM, self.id):
                    continue
                cname = (lib.snd_seq_client_info_get_name(ci) or b"").decode(errors="replace")
                lib.snd_seq_port_info_set_client(pi, client)
                lib.snd_seq_port_info_set_port(pi, -1)
                while lib.snd_seq_query_next_port(self._h, pi) >= 0:
                    caps = lib.snd_seq_port_info_get_capability(pi)
                    if caps & CAP_NO_EXPORT:
                        continue
                    r = caps & (CAP_READ | CAP_SUBS_READ) == CAP_READ | CAP_SUBS_READ
                    w = caps & (CAP_WRITE | CAP_SUBS_WRITE) == CAP_WRITE | CAP_SUBS_WRITE
                    if r or w:
                        out.append(SeqPort(client, lib.snd_seq_port_info_get_port(pi), cname,
                                           (lib.snd_seq_port_info_get_name(pi) or b"").decode(errors="replace"),
                                           r, w))
        finally:
            lib.snd_seq_port_info_free(pi)
            lib.snd_seq_client_info_free(ci)
        return out

    def connect_input(self, p: SeqPort):
        _check(_load().snd_seq_connect_from(self._h, self.in_port, p.client, p.port), f"connect from {p.text}")

    def disconnect_input(self, p: SeqPort):
        _load().snd_seq_disconnect_from(self._h, self.in_port, p.client, p.port)      # gone already is fine

    def connect_output(self, p: SeqPort):
        _check(_load().snd_seq_connect_to(self._h, self.out_port, p.client, p.port), f"connect to {p.text}")

    def disconnect_output(self, p: SeqPort):
        _load().snd_seq_disconnect_to(self._h, self.out_port, p.client, p.port)

    def read(self) -> list[Msg]:
        """Everything that has arrived (note, CC, program and pitch bend messages, and announcements); [] if
        nothing."""
        if not self._h:
            return []
        lib = _load()
        out = []
        ev = ctypes.POINTER(SeqEvent)()
        for _ in range(4096):                      # a bound, so a flood can't hold up the GUI thread
            r = lib.snd_seq_event_input(self._h, ctypes.byref(ev))
            if r == -errno.ENOSPC:                 # input overrun: some events lost, carry on
                continue
            if r < 0 or not ev:
                break
            e = ev.contents
            if e.type in (EV_NOTEON, EV_NOTEOFF):
                n = e.data.note
                kind = NOTE_ON if e.type == EV_NOTEON and n.velocity else NOTE_OFF
                out.append(Msg(kind, n.channel & 15, n.note & 127, n.velocity & 127))
            elif e.type == EV_CONTROLLER:
                c = e.data.control
                out.append(Msg(CC, c.channel & 15, c.param & 127, max(0, min(127, c.value))))
            elif e.type == EV_PGMCHANGE:
                c = e.data.control
                out.append(Msg(PROGRAM, c.channel & 15, max(0, min(127, c.value))))
            elif e.type == EV_PITCHBEND:               # ALSA has it -8192..8191, around 0
                c = e.data.control
                out.append(Msg(PITCH_BEND, c.channel & 15, 0, max(0, min(BEND_MAX, c.value + BEND_CENTRE))))
            elif e.type in EV_ANNOUNCE:
                out.append(Msg(ANNOUNCE))
        return out

    def send(self, m: Msg):
        if not self._h:
            return
        e = self._ev
        ctypes.memset(ctypes.byref(e), 0, ctypes.sizeof(e))
        e.queue = QUEUE_DIRECT
        e.source.port = self.out_port
        e.dest.client, e.dest.port = ADDRESS_SUBSCRIBERS, 0
        if m.kind in (NOTE_ON, NOTE_OFF):
            e.type = EV_NOTEON if m.kind == NOTE_ON else EV_NOTEOFF
            e.data.note.channel, e.data.note.note, e.data.note.velocity = m.channel, m.number, m.value
        elif m.kind == CC:
            e.type = EV_CONTROLLER
            e.data.control.channel, e.data.control.param, e.data.control.value = m.channel, m.number, m.value
        elif m.kind == PROGRAM:
            e.type = EV_PGMCHANGE
            e.data.control.channel, e.data.control.value = m.channel, m.number
        elif m.kind == PITCH_BEND:
            e.type = EV_PITCHBEND
            e.data.control.channel, e.data.control.value = m.channel, m.value - BEND_CENTRE
        else:
            return
        _load().snd_seq_event_output_direct(self._h, ctypes.byref(e))    # no one listening is fine


class MockSeq:
    """A simulated sequencer with one controller ("Mock Controller", in and out). inject() plays a message
    from it; what the app sends is kept in `sent`. wakeup is called on inject (set by the app)."""

    def __init__(self):
        self.ports_list = [SeqPort(128, 0, "Mock Controller", "Mock Controller MIDI 1", True, True)]
        self.open_ok = True
        self.is_open = False
        self.id, self.in_port, self.out_port = 129, 0, 1
        self.inputs: list[SeqPort] = []
        self.outputs: list[SeqPort] = []
        self.sent: list[Msg] = []
        self._inbox: list[Msg] = []
        self.wakeup = None

    def open(self, name: str = CLIENT_NAME):
        if not self.open_ok:
            raise AlsaError(errno.ENOENT, "open the ALSA sequencer: No such file or directory")
        self.is_open = True

    def close(self):
        self.is_open = False
        self.inputs, self.outputs, self._inbox = [], [], []

    def fileno(self):
        return None

    def ports(self):
        return list(self.ports_list) if self.is_open else []

    def connect_input(self, p):
        self.inputs.append(p)

    def disconnect_input(self, p):
        self.inputs = [q for q in self.inputs if q != p]

    def connect_output(self, p):
        self.outputs.append(p)

    def disconnect_output(self, p):
        self.outputs = [q for q in self.outputs if q != p]

    def read(self):
        out, self._inbox = self._inbox, []
        return out

    def send(self, m: Msg):
        if self.is_open and self.outputs:
            self.sent.append(m)

    def inject(self, *msgs: Msg):
        if not self.is_open:                       # a closed client hears nothing
            return
        self._inbox.extend(msgs)
        if self.wakeup:
            self.wakeup()

    def plug(self, p: SeqPort):
        """A port shows up (a controller plugged in)."""
        self.ports_list.append(p)
        self.inject(Msg(ANNOUNCE))

    def unplug(self, name: str):
        self.ports_list = [p for p in self.ports_list if p.name != name]
        self.inputs = [p for p in self.inputs if p.name != name]
        self.outputs = [p for p in self.outputs if p.name != name]
        self.inject(Msg(ANNOUNCE))
