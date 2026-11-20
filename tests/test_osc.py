"""OSC remote control (core/osc.py) on the mock card: the format, the mapping, the settings file, and the
controller over real UDP on this computer (127.0.0.1, ports the system picks), with a test socket as the
controller."""
import json
import math
import struct
import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QHostAddress, QUdpSocket

from aa_hdspe_mixer.core.backend import MockBackend
from aa_hdspe_mixer.core.engine import Engine
from aa_hdspe_mixer.core.osc import (DB, OscBinding, OscControl, OscMsg, OscSettings, UdpTransport,
                                     behaviour_text, decode, describe, encode, feedback, is_high, level_from,
                                     message_text, number, preset_from, validate)
from aa_hdspe_mixer.core.remote import FOLLOW, PRESS_MUTE
from aa_hdspe_mixer.core.routing import MIN_DB


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QCoreApplication.instance() or QApplication([])


def wait_until(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.002)
    return cond()


class Controller:
    """A test OSC controller: sends to the app, receives its feedback."""
    def __init__(self):
        self.sock = QUdpSocket()
        assert self.sock.bind(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0)
        self.got: list[OscMsg] = []
        self.sock.readyRead.connect(self._read)

    def _read(self):
        while self.sock.hasPendingDatagrams():
            self.got += decode(bytes(self.sock.receiveDatagram().data()))

    def send(self, port, *msgs, raw=None):
        for m in msgs:
            self.sock.writeDatagram(encode(m), QHostAddress("127.0.0.1"), port)
        if raw is not None:
            self.sock.writeDatagram(raw, QHostAddress("127.0.0.1"), port)


@pytest.fixture
def rig(app, tmp_path):
    e = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e.start()
    path = str(tmp_path / "osc.json")
    json.dump({"listen_port": 0}, open(path, "w"))                 # 0: a free port, picked by the system
    oc = OscControl(e, UdpTransport(), path)
    ctl = Controller()
    oc.set_enabled(True)
    oc.set_send("127.0.0.1", ctl.sock.localPort())
    assert oc.udp.is_open and oc.dest is not None
    yield e, oc, ctl
    oc.shutdown()
    e.shutdown()


def test_the_format():
    m = OscMsg("/a/b", (1, 0.5, "hi", True, False))
    data = encode(m)
    assert len(data) % 4 == 0 and decode(data) == [m]
    assert data[:8] == b"/a/b\0\0\0\0" and data[8:16] == b",ifsTF\0\0"
    inner = encode(OscMsg("/x", (2,)))
    bundle = b"#bundle\0" + bytes(8) + struct.pack(">i", len(data)) + data
    nested = b"#bundle\0" + bytes(8) + struct.pack(">i", len(bundle)) + bundle + struct.pack(">i", len(inner)) + inner
    assert decode(nested) == [m, OscMsg("/x", (2,))]
    d = b"/d\0\0,dhs\0\0\0\0" + struct.pack(">dq", 2.5, -3) + b"ok\0\0"
    assert decode(d) == [OscMsg("/d", (2.5, -3, "ok"))]
    for bad in (b"", b"hello", b"/a\0\0,f\0\0\0", b"#bundle\0" + bytes(8) + struct.pack(">i", 99)):
        with pytest.raises(ValueError):
            decode(bad)
    assert decode(b"/old\0\0\0\0") == [OscMsg("/old")]                         # no type tags: no arguments
    assert number(OscMsg("/a", ("x", True))) == 1.0 and number(OscMsg("/a", ("x",))) is None
    assert describe(OscMsg("/1/fader1", (0.73412,))) == "/1/fader1 0.7341"
    assert describe(OscMsg("/a", (1, 2, 3, 4, 5))) == "/a 1 2 3 4 …"


def test_the_mapping():
    pos = OscBinding(what="level", target="out:6", address="/f", db_lo=-40, db_hi=6)
    assert level_from(pos, 1.0) == 6 and level_from(pos, 0.0) == -40 and level_from(pos, 7) == 6
    assert feedback(pos, -17.0) == OscMsg("/f", (0.5,))
    db = OscBinding(what="level", target="out:6", address="/f", value=DB, db_hi=6)
    assert level_from(db, -12.34) == -12.3 and level_from(db, 20) == 6 and level_from(db, -100) == -math.inf
    assert level_from(db, -95) == pytest.approx(MIN_DB, abs=0.1)
    assert feedback(db, -math.inf) == OscMsg("/f", (-100.0,)) and feedback(db, -3.0) == OscMsg("/f", (-3.0,))
    mute = OscBinding(what="mute", target="in:0", address="/m", invert=True)
    assert feedback(mute, True) == OscMsg("/m", (0.0,))
    assert is_high(OscMsg("/m")) and is_high(OscMsg("/m", (1,))) and not is_high(OscMsg("/m", (0.2,)))
    by = OscBinding(what="preset", target="preset:number", address="/p")
    assert preset_from(by, OscMsg("/p", (3,))) == 2 and preset_from(by, OscMsg("/p", (0,))) is None
    assert preset_from(OscBinding(what="preset", target="preset:4", address="/p"), OscMsg("/p", (0.0,))) is None
    assert message_text(db) == "/f · in dB" and message_text(mute) == "/m"
    assert behaviour_text(OscBinding(what="mute", mode=FOLLOW)) == "Muted at 0.5 and up"
    assert behaviour_text(OscBinding(what="mute", mode=PRESS_MUTE)) == "Each press mutes"
    assert "starts with /" in validate(OscBinding(what="mute", target="in:0", address="1/a"))
    assert "can't have" in validate(OscBinding(what="mute", target="in:0", address="/a/*"))
    assert validate(OscBinding(what="mute", target="in:0", address="/a")) == ""


def test_settings_file(tmp_path):
    path = str(tmp_path / "osc.json")
    s = OscSettings(path)
    assert (s.listen_port, s.listen_network, s.send_host, s.send_port) == (8000, False, "", 9000)
    s.enabled, s.send_host = True, "tablet.local"
    s.bindings = [OscBinding("o1", "level", "out:6", "/f", DB)]
    s.save()
    d = json.load(open(path))
    d["bindings"].append({"id": "o2", "what": "mute", "target": "in:0", "address": "no slash"})   # bad: dropped
    d["listen_port"] = 99999                                                   # bad: the default
    json.dump(d, open(path, "w"))
    t = OscSettings(path)
    assert (t.enabled, t.send_host, t.listen_port) == (True, "tablet.local", 8000)
    assert [b.id for b in t.bindings] == ["o1"] and t.bindings[0].value == DB and t.next_id() == "o2"


def test_mutes_levels_presets_over_udp(rig):
    e, oc, ctl = rig
    port = oc.listening_port
    oc.save_binding(OscBinding(what="mute", target="in:1", address="/1/toggle1"))          # 1 of pair 0+1
    oc.save_binding(OscBinding(what="level", target="out:6", address="/1/fader1", db_lo=-40, db_hi=0))
    oc.save_binding(OscBinding(what="preset", target="preset:number", address="/preset"))
    assert wait_until(lambda: OscMsg("/1/toggle1", (0.0,)) in ctl.got and OscMsg("/1/fader1", (1.0,)) in ctl.got)

    ctl.got.clear()
    ctl.send(port, OscMsg("/1/toggle1", (1.0,)), OscMsg("/1/toggle1", (0.0,)))           # press, release
    assert wait_until(lambda: e.unit_muted("in:0"))
    assert oc.last == "/1/toggle1 0"

    ctl.send(port, OscMsg("/1/fader1", (0.1,)), OscMsg("/1/fader1", (0.5,)))
    assert wait_until(lambda: e.output_level_db(6) == -20)
    oc.flush()
    assert not [m for m in ctl.got if m.address == "/1/fader1"]                           # no echo

    e.set_output_level("out:6", -10)                                                       # from the window
    assert wait_until(lambda: OscMsg("/1/fader1", (0.75,)) in ctl.got)

    e.store_preset(1, "Two")
    msgs = []
    e.message.connect(lambda level, text: msgs.append(text))
    ctl.send(port, OscMsg("/preset", (2,)), OscMsg("/preset", (9,)))
    assert wait_until(lambda: len(msgs) == 2)
    assert msgs == ["Recalled preset 2: Two", "OSC: there is no preset 9 to recall."]

    ctl.send(port, raw=b"not osc")
    assert wait_until(lambda: oc.last == "(something that isn't OSC)")


def test_learning_ports_and_off(rig, tmp_path):
    e, oc, ctl = rig
    port = oc.listening_port
    oc.save_binding(OscBinding(what="mute", target="in:0", address="/m"))
    got = []
    oc.learned.connect(got.append)
    oc.set_learning(True)
    ctl.send(port, OscMsg("/m", (1.0,)))
    assert wait_until(lambda: got == [OscMsg("/m", (1.0,))]) and not oc.learning
    assert not e.unit_muted("in:0")                                            # learning doesn't act

    taken = QUdpSocket()                                                       # a port someone else has
    assert taken.bind(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0)
    oc.set_listen(taken.localPort(), False)
    assert "couldn't listen on UDP port" in oc.error and not oc.udp.is_open
    oc.set_listen(0, False)
    assert oc.error == "" and oc.udp.is_open

    oc.set_send("no-such-host.invalid", 9000)
    assert wait_until(lambda: oc.dest_error != "", 10) and oc.dest is None

    oc.set_enabled(False)
    assert not oc.udp.is_open
    ctl.send(port, OscMsg("/m", (1.0,)))
    wait_until(lambda: False, 0.1)
    assert not e.unit_muted("in:0")
