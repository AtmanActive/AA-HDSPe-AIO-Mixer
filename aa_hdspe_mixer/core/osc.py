"""OSC remote control: mutes, levels and preset recalls from OSC messages over UDP (TouchOSC, Open Stage
Control, a DAW, a script), with feedback sent back. Works like MIDI (midi.py); the shared part is remote.py.

App-wide, never in presets: ~/.config/aa-hdspe-aio-mixer/osc.json
  {"enabled": bool, "listen_port": int, "listen_network": bool, "send_host": str, "send_port": int,
   "bindings": [binding, ...]}
listen_network false = only programs on this computer can send (UDP on 127.0.0.1); true = any computer on
the network can (all addresses). OSC has no passwords, so anyone who can reach the port can control the
mixer. send_host "" = no feedback. The defaults suit TouchOSC (it sends to 8000 and listens on 9000).

A binding (see remote.py for what, target, invert, mode and the level limits) ties one OSC address to one
thing in the mixer. OSC's own parts:
  address : "/1/fader1": the message's address, exactly (no wildcards)
  value   : level only. "position": the first argument is the control's position, 0 to 1 (as TouchOSC
            faders send it), mapped with the limits; "db": it is the level in dB, kept within the limits
            (-100 and below = -inf when the bottom is -inf).
  target  : "preset:number" - the first argument picks the preset (1 = preset 1)
The first argument that is a number (int, float, true/false) is the value. A press is a value of 0.5 and up,
or a message with no value at all (a trigger); "follow" mutes while it is 0.5 and up.

Feedback, to send_host:send_port, as one float: mutes 1.0 / 0.0 (as TouchOSC buttons take it), levels as
their position 0 to 1, or in dB (-100 for -inf).
"""
from __future__ import annotations

import math
import struct
from dataclasses import asdict, dataclass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QHostInfo, QNetworkInterface, QUdpSocket

from .memory import atomic_write_json, read_json
from .remote import (DB_FLOOR, FOLLOW, LEVEL, MODES, MUTE, PRESET, PRESET_BY_NUMBER, TOGGLE, WHATS,
                     RemoteControl, db_text, level_behaviour, level_db, level_pos, limits_from, mute_behaviour)
from .routing import MAX_DB, MIN_DB

POSITION, DB = "position", "db"
VALUES = (POSITION, DB)
DB_OFF = -100.0                   # in dB values: this and below is -inf
DEFAULT_LISTEN_PORT, DEFAULT_SEND_PORT = 8000, 9000
PATTERN_CHARS = set(" #*,?[]{}")


# ---------------------------------------------------------------------- the OSC format (1.0)
@dataclass(frozen=True)
class OscMsg:
    address: str
    args: tuple = ()


def _pad(b: bytes) -> bytes:
    return b + b"\0" * (4 - len(b) % 4)               # a string: its bytes, a 0, padded to a multiple of 4


def encode(m: OscMsg) -> bytes:
    """One message. Arguments: float (f), int (i), str (s), bool (T/F)."""
    tags, data = ",", b""
    for a in m.args:
        if isinstance(a, bool):
            tags += "T" if a else "F"
        elif isinstance(a, int):
            tags += "i"
            data += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            data += struct.pack(">f", a)
        else:
            tags += "s"
            data += _pad(str(a).encode())
    return _pad(m.address.encode()) + _pad(tags.encode()) + data


def _string(data: bytes, i: int) -> tuple[str, int]:
    end = data.index(b"\0", i)
    return data[i:end].decode("utf-8", "replace"), (end // 4 + 1) * 4


def decode(data: bytes, depth: int = 0) -> list[OscMsg]:
    """The messages in one packet: a message, or a bundle (of messages and bundles; time tags ignored, all
    are applied now). Raises ValueError for what isn't OSC."""
    try:
        if data.startswith(b"#bundle\0"):
            if depth > 8:
                raise ValueError("bundles nested too deep")
            out, i = [], 16                             # "#bundle", time tag
            while i + 4 <= len(data):
                (n,) = struct.unpack_from(">i", data, i)
                if n < 0 or i + 4 + n > len(data):
                    raise ValueError("bad bundle element size")
                out += decode(data[i + 4:i + 4 + n], depth + 1)
                i += 4 + n
            return out
        address, i = _string(data, 0)
        if not address.startswith("/"):
            raise ValueError("not an OSC message")
        if i >= len(data):
            return [OscMsg(address)]                    # no type tags (very old senders): no arguments
        tags, i = _string(data, i)
        args = []
        for t in tags[1:] if tags.startswith(",") else "":
            if t in "if":
                args.append(struct.unpack_from(">i" if t == "i" else ">f", data, i)[0])
                i += 4
            elif t in "hdt":
                args.append(struct.unpack_from({"h": ">q", "d": ">d", "t": ">Q"}[t], data, i)[0])
                i += 8
            elif t in "sS":
                s, i = _string(data, i)
                args.append(s)
            elif t == "b":
                (n,) = struct.unpack_from(">i", data, i)
                args.append(bytes(data[i + 4:i + 4 + n]))
                i += 4 + (n + 3) // 4 * 4
            elif t in "TF":
                args.append(t == "T")
            elif t in "NI":
                args.append(None)
            elif t in "crm":
                args.append(struct.unpack_from(">i", data, i)[0])
                i += 4
            else:
                break                                   # unknown: the rest can't be read
        return [OscMsg(address, tuple(args))]
    except (struct.error, ValueError, IndexError) as e:
        raise ValueError(str(e) or "not OSC") from e


def number(m: OscMsg) -> float | None:
    """The message's value: its first argument that is a number (true/false = 1/0), or None."""
    for a in m.args:
        if isinstance(a, bool):
            return 1.0 if a else 0.0
        if isinstance(a, (int, float)) and math.isfinite(a):
            return float(a)
    return None


def describe(m: OscMsg) -> str:
    """e.g. "/1/fader1 0.734", for the last message received."""
    def arg(a):
        if isinstance(a, bool) or a is None:
            return {True: "true", False: "false", None: "nil"}[a]
        if isinstance(a, float):
            return f"{a:.4g}"
        if isinstance(a, str):
            return f'"{a}"'
        if isinstance(a, bytes):
            return f"({len(a)} bytes)"
        return str(a)
    shown = " ".join(arg(a) for a in m.args[:4]) + (" …" if len(m.args) > 4 else "")
    return f"{m.address} {shown}".rstrip()


# ---------------------------------------------------------------------- bindings
@dataclass
class OscBinding:
    id: str = ""
    what: str = MUTE
    target: str = ""
    address: str = ""
    value: str = POSITION
    invert: bool = False
    mode: str = TOGGLE
    db_lo: float = -math.inf
    db_hi: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["db_lo"] = None if self.db_lo == -math.inf else self.db_lo
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "OscBinding":
        lo, hi = limits_from(d)
        return cls(id=str(d.get("id", "")), what=str(d.get("what", MUTE)), target=str(d.get("target", "")),
                   address=str(d.get("address", "")),
                   value=d.get("value") if d.get("value") in VALUES else POSITION,
                   invert=bool(d.get("invert", False)),
                   mode=d.get("mode") if d.get("mode") in MODES else TOGGLE, db_lo=lo, db_hi=hi)

    @property
    def by_number(self) -> bool:
        return self.what == PRESET and self.target == PRESET_BY_NUMBER


def validate(b: OscBinding) -> str:
    """"" or what's wrong with the binding, as a sentence for the user."""
    if b.what not in WHATS:
        return "Choose what to control."
    if not b.target:
        return {MUTE: "Choose a channel or connection to mute.", LEVEL: "Choose an output or connection.",
                PRESET: "Choose a preset."}[b.what]
    if not b.address.startswith("/") or len(b.address) < 2:
        return "The OSC address starts with / (for example /1/fader1)."
    if PATTERN_CHARS & set(b.address):
        return "The OSC address can't have spaces or # * , ? [ ] { } in it."
    if b.what == LEVEL and not b.db_hi > b.db_lo:
        return "The top level must be above the bottom level."
    return ""


def matches(b: OscBinding, m: OscMsg) -> bool:
    return m.address == b.address


def is_high(m: OscMsg) -> bool:
    v = number(m)
    return v is None or v >= 0.5                     # no value: a trigger, a press


def level_from(b: OscBinding, v: float) -> float:
    if b.value == DB:
        if v <= DB_OFF and b.db_lo == -math.inf:
            return -math.inf
        lo = MIN_DB if b.db_lo == -math.inf else b.db_lo
        return round(max(lo, min(b.db_hi, MAX_DB, v)), 1)
    p = max(0.0, min(1.0, v))
    db = level_db(1 - p if b.invert else p, b.db_lo, b.db_hi)
    return db if db == -math.inf else round(min(MAX_DB, db), 1)


def preset_from(b: OscBinding, m: OscMsg) -> int | None:
    """The preset slot (0-based) to recall, or None."""
    if b.by_number:
        v = number(m)
        return None if v is None or v < 1 else int(round(v)) - 1
    return int(b.target.split(":")[1]) if is_high(m) else None


def feedback(b: OscBinding, state) -> OscMsg | None:
    """What tells the controller the state (muted: bool; level: dB), or None (presets)."""
    if b.what == MUTE:
        return OscMsg(b.address, (1.0 if bool(state) != b.invert else 0.0,))
    if b.what == LEVEL:
        if b.value == DB:
            return OscMsg(b.address, (DB_OFF if state == -math.inf else round(float(state), 1),))
        p = level_pos(state, b.db_lo, b.db_hi)
        return OscMsg(b.address, (round(1 - p if b.invert else p, 4),))
    return None


def message_text(b: OscBinding) -> str:
    """e.g. "/1/fader1 · 0 to 1"."""
    if b.what == LEVEL:
        return f"{b.address} · {'in dB' if b.value == DB else '0 to 1'}"
    return b.address


def behaviour_text(b: OscBinding) -> str:
    if b.what == MUTE:
        text = mute_behaviour(b.mode, b.invert, "press", "at 0.5 and up")
        return text + (" · lit when sounding" if b.invert and b.mode != FOLLOW else "")
    if b.what == LEVEL:
        return level_behaviour(b) if b.value == POSITION else f"{db_text(b.db_lo)} … {db_text(b.db_hi)}"
    if b.by_number:
        return "Value 1 recalls preset 1, …"
    return ""


class OscSettings:
    def __init__(self, path: str):
        self.path = path
        self.enabled = False
        self.listen_port, self.listen_network = DEFAULT_LISTEN_PORT, False
        self.send_host, self.send_port = "", DEFAULT_SEND_PORT
        self.bindings: list[OscBinding] = []
        try:
            d = read_json(path)
        except (OSError, ValueError):
            d = {}
        if isinstance(d, dict):
            def port(key, default):
                try:
                    v = int(d.get(key, default))
                except (TypeError, ValueError):
                    return default
                return v if 0 <= v <= 65535 else default
            self.enabled = bool(d.get("enabled", False))
            self.listen_port = port("listen_port", DEFAULT_LISTEN_PORT)
            self.listen_network = bool(d.get("listen_network", False))
            self.send_host = str(d.get("send_host") or "").strip()
            self.send_port = port("send_port", DEFAULT_SEND_PORT)
            for item in d.get("bindings") or []:
                if isinstance(item, dict):
                    b = OscBinding.from_dict(item)
                    if b.id and not validate(b) and all(x.id != b.id for x in self.bindings):
                        self.bindings.append(b)

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "listen_port": self.listen_port, "listen_network": self.listen_network,
                "send_host": self.send_host, "send_port": self.send_port,
                "bindings": [b.to_dict() for b in self.bindings]}

    def save(self):
        try:
            atomic_write_json(self.path, self.to_dict())
        except OSError:
            pass

    def next_id(self) -> str:
        n = max([int(b.id[1:]) for b in self.bindings if b.id[:1] == "o" and b.id[1:].isdigit()] + [0])
        return f"o{n + 1}"


# ---------------------------------------------------------------------- UDP
class UdpTransport(QObject):
    """The app's OSC socket: receives on a UDP port, sends to any host. In the GUI thread (Qt's event loop)."""
    received = Signal(bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sock: QUdpSocket | None = None

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    @property
    def port(self) -> int:
        """The port it listens on (the one picked by the system, when asked for 0)."""
        return self._sock.localPort() if self._sock else 0

    def open(self, port: int, network: bool):
        self.close()
        s = QUdpSocket(self)
        addr = QHostAddress(QHostAddress.SpecialAddress.Any if network else QHostAddress.SpecialAddress.LocalHost)
        if not s.bind(addr, port, QAbstractSocket.BindFlag.DontShareAddress):   # in use: say so, don't share
            err = s.errorString()
            s.deleteLater()
            raise OSError(err)
        s.readyRead.connect(self._read)
        self._sock = s

    def close(self):
        if self._sock is not None:
            self._sock.close()
            self._sock.deleteLater()
            self._sock = None

    def _read(self):
        s = self._sock
        for _ in range(4096):                         # a bound, so a flood can't hold up the GUI thread
            if s is None or not s.hasPendingDatagrams():
                break
            self.received.emit(bytes(s.receiveDatagram().data()))

    def send(self, data: bytes, host: QHostAddress, port: int):
        if self._sock is not None:
            self._sock.writeDatagram(data, host, port)


def local_addresses() -> list[str]:
    """This computer's IPv4 addresses on the network (not 127.0.0.1), for telling a controller where to send."""
    out = []
    for a in QNetworkInterface.allAddresses():
        if a.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol and not a.isLoopback():
            out.append(a.toString())
    return out


# ---------------------------------------------------------------------- the controller
class OscControl(RemoteControl):
    """Listens for OSC on the chosen UDP port and applies the bindings (remote.py); feedback goes to the
    chosen host and port. `transport` is a UdpTransport."""
    NAME = "OSC"

    def __init__(self, engine, transport: UdpTransport, path: str, parent=None):
        super().__init__(engine, OscSettings(path), parent)
        self.udp = transport
        self.udp.received.connect(self._on_packet)
        self.dest: QHostAddress | None = None          # send_host, looked up
        self.dest_error = ""
        self._lookup = 0
        if self.settings.enabled:
            self._open()

    # ------------------------------------------------------------------ the socket, the feedback host
    def _is_open(self) -> bool:
        return self.udp.is_open

    def _can_send(self) -> bool:
        return self.udp.is_open and self.dest is not None

    def _send(self, m: OscMsg):
        self.udp.send(encode(m), self.dest, self.settings.send_port)

    def _open(self):
        s = self.settings
        try:
            self.udp.open(s.listen_port, s.listen_network)
        except OSError as e:
            self.error = f"OSC is off: couldn't listen on UDP port {s.listen_port} ({e})."
            return
        self.error = ""
        self._sent.clear()
        self._look_up()

    def _close_transport(self):
        self.udp.close()

    def _look_up(self):
        """Finds send_host (a name or an address); feedback starts when it's found."""
        self.dest, self.dest_error = None, ""
        self._lookup += 1
        host = self.settings.send_host
        if not host or not self.udp.is_open:
            return
        addr = QHostAddress()
        if addr.setAddress(host):
            self._found(self._lookup, [addr])
            return
        n = self._lookup
        QHostInfo.lookupHost(host, lambda info: self._found(n, info.addresses()))

    def _found(self, n: int, addresses: list):
        if n != self._lookup:                          # an earlier lookup: the host has changed since
            return
        v4 = [a for a in addresses if a.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol]
        if not (v4 or addresses):
            self.dest, self.dest_error = None, f"Can't find {self.settings.send_host}: no feedback is sent."
        else:
            self.dest, self.dest_error = (v4 or addresses)[0], ""
            self._sent.clear()                          # show it everything
            self._fb_timer.start()
        self.stateChanged.emit()

    def set_listen(self, port: int, network: bool):
        port, network = max(0, min(65535, int(port))), bool(network)
        if (port, network) == (self.settings.listen_port, self.settings.listen_network):
            return
        self.settings.listen_port, self.settings.listen_network = port, network
        self.settings.save()
        if self.settings.enabled:
            self._close()
            self._open()
        self.stateChanged.emit()

    def set_send(self, host: str, port: int):
        host, port = str(host or "").strip(), max(0, min(65535, int(port)))
        if (host, port) == (self.settings.send_host, self.settings.send_port):
            return
        self.settings.send_host, self.settings.send_port = host, port
        self.settings.save()
        self._look_up()
        self._sent.clear()
        self._fb_timer.start()
        self.stateChanged.emit()

    @property
    def listening_port(self) -> int:
        return self.udp.port

    # ------------------------------------------------------------------ the mapping
    def _validate(self, b: OscBinding) -> str:
        return validate(b)

    def _feedback(self, b: OscBinding, state):
        return feedback(b, state)

    def _level_db(self, b: OscBinding, m: OscMsg) -> float:
        return level_from(b, number(m))

    # ------------------------------------------------------------------ incoming
    def _on_packet(self, data: bytes):
        try:
            msgs = decode(data)
        except ValueError:
            self._heard(None, "(something that isn't OSC)", learnable=False)
            return
        for m in msgs:
            if not self._heard(m, describe(m)):
                self.handle(m)

    def handle(self, m: OscMsg):
        """Applies one incoming message to every binding it matches."""
        for b in list(self.settings.bindings):
            if not matches(b, m):
                continue
            if b.what == MUTE:
                self._set_mute(b, is_high(m))
            elif b.what == LEVEL:
                v = number(m)
                if v is not None:
                    self._queue_level(b, m, feedback(b, level_from(b, v)))   # the control is there already
            else:
                slot = preset_from(b, m)
                if slot is not None:
                    self._recall(slot)


__all__ = ["OscMsg", "OscBinding", "OscSettings", "OscControl", "UdpTransport", "encode", "decode", "number",
           "describe", "validate", "matches", "is_high", "level_from", "preset_from", "feedback", "message_text",
           "behaviour_text", "local_addresses", "POSITION", "DB", "VALUES", "DB_OFF", "DB_FLOOR",
           "DEFAULT_LISTEN_PORT", "DEFAULT_SEND_PORT"]
