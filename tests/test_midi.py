"""MIDI remote control (core/midi.py) on the mock card with the simulated sequencer (alsaseq.MockSeq):
the mapping, the settings file, and the controller applying bindings and sending feedback."""
import json
import math

import pytest
from PySide6.QtCore import QCoreApplication

from aa_hdspe_mixer.core.alsaseq import CC, NOTE_OFF, NOTE_ON, PITCH_BEND, PROGRAM, Msg, SeqEvent, SeqPort
from aa_hdspe_mixer.core.backend import MockBackend
from aa_hdspe_mixer.core.engine import Engine
from aa_hdspe_mixer.core.midi import (FOLLOW, PRESS_MUTE, PRESS_UNMUTE, Binding, MidiControl, MidiSettings, behaviour_text, describe,
                                      feedback, level_db, level_from, level_pos, matches, message_text, validate)
from aa_hdspe_mixer.core.routing import MIN_DB

PORT = "Mock Controller:Mock Controller MIDI 1"


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QCoreApplication.instance() or QApplication([])


@pytest.fixture
def rig(app, tmp_path):
    e = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e.start()
    mc = MidiControl(e, e.backend.midi, str(tmp_path / "midi.json"))
    mc.set_enabled(True)
    mc.set_port(PORT, True)
    mc.set_port(PORT, False)
    yield e, mc, e.backend.midi
    mc.shutdown()
    e.shutdown()


def test_event_struct_is_alsas():
    assert SeqEvent.data.offset == 16 and SeqEvent.source.offset == 12


def test_level_curves():
    assert level_db(0, -math.inf, 0) == -math.inf and level_db(1, -math.inf, 0) == 0
    assert level_db(0.5, -math.inf, 0) == pytest.approx(-18.06, abs=0.01)        # a desk fader's curve
    assert level_db(0.5, -20, 6) == -7                                           # finite: even in dB
    for lo, hi in ((-math.inf, 0), (-40, 6)):
        for v in (1, 30, 64, 100, 127):
            b = Binding(what="level", message="cc", db_lo=lo, db_hi=hi)
            assert feedback(b, level_from(b, Msg(CC, 0, 0, v))).value == v      # there and back
    inv = Binding(what="level", message="cc", invert=True)
    assert level_from(inv, Msg(CC, 0, 0, 127)) == -math.inf and level_from(inv, Msg(CC, 0, 0, 0)) == 0
    assert level_pos(-100, -40, 0) == 0 and level_pos(+6, -40, 0) == 1


def test_matching_and_texts():
    b = Binding(what="mute", target="in:0", message="note", channel=2, number=60)
    assert matches(b, Msg(NOTE_ON, 1, 60, 100)) and matches(b, Msg(NOTE_OFF, 1, 60))
    assert not matches(b, Msg(NOTE_ON, 0, 60, 100)) and not matches(b, Msg(CC, 1, 60, 127))
    assert matches(Binding(what="preset", target="preset:number", message="program"), Msg(PROGRAM, 9, 17))
    assert not matches(Binding(what="preset", target="preset:3", message="program", number=4), Msg(PROGRAM, 0, 5))
    assert message_text(b) == "CH02: Note 060 (C4)"
    assert message_text(Binding(what="preset", target="preset:2", message="program", number=4)) == \
        "CH--: Program 5"
    assert behaviour_text(Binding(what="mute", message="cc", mode=FOLLOW, invert=True)) == "Muted below 64"
    assert behaviour_text(Binding(what="level", message="cc", db_lo=-40, db_hi=6)) == "−40 dB … +6 dB"
    assert describe(Msg(CC, 0, 7, 100)) == "CH01: CC 7 = 100"
    assert describe(Msg(NOTE_ON, 15, 60, 90)) == "CH16: Note 060 (C4) on"


def test_validation():
    assert "Choose" in validate(Binding(what="mute", target=""))
    assert "controller" in validate(Binding(what="level", target="out:6", message="note"))
    assert "by number" in validate(Binding(what="preset", target="preset:number", message="note"))
    assert "above" in validate(Binding(what="level", target="out:6", message="cc", db_lo=0, db_hi=0))
    assert validate(Binding(what="level", target="in:0>out:6", message="cc")) == ""


def test_settings_file(tmp_path):
    path = str(tmp_path / "midi.json")
    s = MidiSettings(path)
    s.enabled, s.input = True, PORT
    s.bindings = [Binding("b1", "level", "out:6", "cc", 1, 7), Binding("b7", "mute", "in:0", "note", 0, 60)]
    s.save()
    d = json.load(open(path))
    assert d["bindings"][0]["db_lo"] is None                                    # -inf as null
    d["bindings"].append({"id": "b8", "what": "level", "target": "out:6", "message": "note"})   # bad: dropped
    json.dump(d, open(path, "w"))
    t = MidiSettings(path)
    assert (t.enabled, t.input, t.output) == (True, PORT, "")
    assert [b.id for b in t.bindings] == ["b1", "b7"] and t.next_id() == "b8"
    assert t.bindings[0].db_lo == -math.inf


def test_mutes_toggle_follow_and_feedback(rig):
    e, mc, seq = rig
    assert mc.save_binding(Binding(what="mute", target="in:1", message="note", number=60)) == ""   # 1 of pair 0+1
    assert mc.save_binding(Binding(what="mute", target="out:6", message="cc", number=20, mode=FOLLOW,
                                   invert=True)) == ""
    mc.flush()
    assert seq.sent == [Msg(NOTE_ON, 0, 60, 0), Msg(CC, 0, 20, 127)]          # the state, at once: not muted

    seq.inject(Msg(NOTE_ON, 4, 60, 90))
    assert e.unit_muted("in:0")                                               # the stereo pair holding input 1
    seq.inject(Msg(NOTE_OFF, 4, 60))                                          # a toggle's release does nothing
    assert e.unit_muted("in:0")
    seq.inject(Msg(NOTE_ON, 4, 60, 90))
    assert not e.unit_muted("in:0")

    seq.inject(Msg(CC, 0, 20, 0))                                             # follow, inverted: low = muted
    assert e.unit_muted("out:6")
    seq.inject(Msg(CC, 0, 20, 127))
    assert not e.unit_muted("out:6")

    seq.sent.clear()
    e.set_unit_mute("in:0", True)                                             # from the window: the LED follows
    mc.flush()
    assert seq.sent == [Msg(NOTE_ON, 0, 60, 127)]


def test_levels_are_coalesced_and_not_echoed(rig):
    e, mc, seq = rig
    mc.save_binding(Binding(what="level", target="out:6", message="cc", channel=1, number=7, db_lo=-40, db_hi=0))
    mc.flush()
    seq.sent.clear()
    seq.inject(Msg(CC, 0, 7, 10), Msg(CC, 0, 7, 50), Msg(CC, 0, 7, 127))       # a fader moved fast
    assert e.output_level_db(6) == 0.0                                        # not yet: the last one wins
    mc.flush()
    assert e.output_level_db(6) == 0.0 and seq.sent == []                     # 127 = 0 dB; no echo
    seq.inject(Msg(CC, 0, 7, 0))
    mc.flush()
    assert e.output_level_db(6) == e.output_level_db(7) == -40 and seq.sent == []
    mc.save_binding(Binding(what="mute", target="in:0", message="note", number=60))
    mc.flush()
    seq.sent.clear()
    seq.inject(Msg(CC, 0, 7, 64), Msg(NOTE_ON, 0, 60, 100))                  # a mute while a level waits:
    mc._send_feedback()
    assert seq.sent == [Msg(NOTE_ON, 0, 60, 127)]                             # the fader isn't sent back
    mc.flush()
    assert seq.sent == [Msg(NOTE_ON, 0, 60, 127)]
    seq.sent.clear()
    e.set_output_level("out:6", -10)                                          # from the window: the fader moves
    mc.flush()
    assert seq.sent == [Msg(CC, 0, 7, 95)]
    seq.inject(Msg(CC, 1, 7, 0))                                              # another channel: not this binding
    mc.flush()
    assert e.output_level_db(6) == -10


def test_connection_level_never_removes_it(rig):
    e, mc, seq = rig
    mc.save_binding(Binding(what="level", target="in:0>out:6", message="cc", number=1))
    seq.inject(Msg(CC, 0, 1, 0))
    mc.flush()
    assert e.connection_db("in:0", "out:6") == -math.inf                     # no connection: nothing made
    e.set_connection_db("in:0", "out:6", 0)
    seq.inject(Msg(CC, 0, 1, 0))
    mc.flush()
    assert e.connection_db("in:0", "out:6") == pytest.approx(MIN_DB, abs=0.01)


def test_presets(rig):
    e, mc, seq = rig
    e.store_preset(2, "Three")
    msgs = []
    e.message.connect(lambda level, text: msgs.append((level, text)))
    mc.save_binding(Binding(what="preset", target="preset:number", message="program"))
    mc.save_binding(Binding(what="preset", target="preset:2", message="note", number=36))
    seq.inject(Msg(PROGRAM, 0, 2))
    seq.inject(Msg(NOTE_ON, 0, 36, 1))
    seq.inject(Msg(PROGRAM, 0, 5))
    assert msgs == [("info", "Recalled preset 3: Three")] * 2 + [
        ("warning", "MIDI: there is no preset 6 to recall.")]


def test_ports_waiting_plugging_and_off(rig):
    e, mc, seq = rig
    assert mc.port_state(True) == mc.port_state(False) == "connected" and seq.inputs and seq.outputs
    mc.save_binding(Binding(what="mute", target="in:0", message="note", number=60))
    mc.flush()
    seq.unplug(PORT)
    assert mc.port_state(True) == "waiting" and mc.in_port is None
    seq.sent.clear()
    seq.plug(SeqPort(130, 0, "Mock Controller", "Mock Controller MIDI 1", True, True))   # back, new number
    mc.flush()
    assert mc.port_state(True) == "connected" and mc.in_port.client == 130
    assert seq.sent == [Msg(NOTE_ON, 0, 60, 0)]                               # shown everything again

    mc.set_enabled(False)
    assert not seq.is_open and mc.port_state(True) == "off"
    seq.inject(Msg(NOTE_ON, 0, 60, 100))
    assert not e.unit_muted("in:0")
    mc.set_enabled(True)
    assert mc.port_state(True) == "connected"

    seq.close()
    seq.open_ok = False
    mc.set_enabled(False)
    mc.set_enabled(True)
    assert "couldn't open the ALSA sequencer" in mc.error


def test_learning_takes_the_next_message_and_doesnt_act(rig):
    e, mc, seq = rig
    mc.save_binding(Binding(what="mute", target="in:0", message="note", number=60))
    got = []
    mc.learned.connect(got.append)
    mc.set_learning(True)
    seq.inject(Msg(NOTE_OFF, 0, 61), Msg(NOTE_ON, 0, 60, 100), Msg(NOTE_ON, 0, 60, 100))
    assert got == [Msg(NOTE_ON, 0, 60, 100)] and not mc.learning
    assert e.unit_muted("in:0")                                               # the second press acted, once


def test_pitch_bend_mapping():
    lvl = Binding(what="level", target="out:6", message="pitchbend", channel=3, number=0, db_lo=-40, db_hi=6)
    assert matches(lvl, Msg(PITCH_BEND, 2, 0, 100)) and not matches(lvl, Msg(PITCH_BEND, 1, 0, 100))
    assert not matches(lvl, Msg(CC, 2, 0, 100))
    assert level_from(lvl, Msg(PITCH_BEND, 2, 0, 16383)) == 6 and level_from(lvl, Msg(PITCH_BEND, 2, 0, 0)) == -40
    for v in (0, 1000, 8192, 12000, 16383):                                   # 14 bits there and back
        lv = Binding(what="level", message="pitchbend", db_lo=-90, db_hi=6)
        assert abs(feedback(lv, level_from(lv, Msg(PITCH_BEND, 0, 0, v))).value - v) <= 9
    assert feedback(lvl, 6.0) == Msg(PITCH_BEND, 2, 0, 16383)
    assert feedback(Binding(what="mute", message="pitchbend"), True) is None   # nothing to light up
    assert message_text(lvl) == "CH03: Pitch bend"
    assert describe(Msg(PITCH_BEND, 0, 0, 16383)) == "CH01: Pitch bend +8191"
    assert describe(Msg(PITCH_BEND, 9, 0, 0)) == "CH10: Pitch bend −8192"
    assert describe(Msg(PITCH_BEND, 0, 0, 8192)) == "CH01: Pitch bend 0"
    assert "by number" in validate(Binding(what="preset", target="preset:number", message="pitchbend"))
    assert behaviour_text(Binding(what="mute", message="pitchbend", mode=FOLLOW)) == "Muted while bent up"


def test_pitch_bend_mutes_presets_and_levels(rig):
    e, mc, seq = rig
    e.store_preset(1, "Two")
    msgs = []
    e.message.connect(lambda level, text: msgs.append(text))
    assert mc.save_binding(Binding(what="mute", target="in:0", message="pitchbend", channel=1, number=5)) == ""
    assert mc.bindings()[0].number == 0                                       # not used, kept 0
    mc.save_binding(Binding(what="mute", target="out:6", message="pitchbend", channel=2, mode=FOLLOW))
    mc.save_binding(Binding(what="preset", target="preset:1", message="pitchbend", channel=3))
    mc.save_binding(Binding(what="level", target="out:8", message="pitchbend", channel=4, db_lo=-40, db_hi=0))
    mc.flush()
    seq.sent.clear()

    bend = lambda ch, *vs: seq.inject(*(Msg(PITCH_BEND, ch, 0, v) for v in vs))
    bend(0, 8192, 13000, 15000, 16383)                                        # one bend up: one toggle
    assert e.unit_muted("in:0")
    bend(0, 12000, 9000, 8192)                                                # back to the centre: nothing
    assert e.unit_muted("in:0")
    bend(0, 14000, 8192)                                                      # bent up again: toggles back
    assert not e.unit_muted("in:0")

    bend(1, 13000)                                                            # follow: muted while bent up
    assert e.unit_muted("out:6")
    bend(1, 8192)
    assert not e.unit_muted("out:6")

    bend(2, 13000, 16000, 8192, 16383)                                        # two bends up: two recalls
    assert msgs == ["Recalled preset 2: Two"] * 2

    bend(3, 0, 16383)
    mc.flush()
    assert e.output_level_db(8) == 0.0
    seq.sent.clear()
    bend(3, 0)
    mc.flush()
    assert e.output_level_db(8) == -40 and seq.sent == []                     # no echo
    e.set_output_level("out:8", 0)                                            # from the window: the fader moves
    mc.flush()
    assert seq.sent == [Msg(PITCH_BEND, 3, 0, 16383)]


def test_mute_and_unmute_on_each_press(rig):
    e, mc, seq = rig
    mc.save_binding(Binding(what="mute", target="in:0", message="note", number=60, mode=PRESS_MUTE))
    mc.save_binding(Binding(what="mute", target="in:0", message="note", number=61, mode=PRESS_UNMUTE))
    assert behaviour_text(mc.bindings()[0]) == "Each press mutes"
    assert behaviour_text(mc.bindings()[1]) == "Each press unmutes"
    mc.flush()
    seq.inject(Msg(NOTE_ON, 0, 60, 100), Msg(NOTE_OFF, 0, 60))
    assert e.unit_muted("in:0")
    mc.flush()
    seq.sent.clear()
    seq.inject(Msg(NOTE_ON, 0, 60, 100))                                     # again: stays muted,
    assert e.unit_muted("in:0")
    mc.flush()
    assert Msg(NOTE_ON, 0, 60, 127) in seq.sent                               # and its LED is set again
    seq.inject(Msg(NOTE_ON, 0, 61, 100), Msg(NOTE_ON, 0, 61, 100))
    assert not e.unit_muted("in:0")
    assert Binding.from_dict({"mode": PRESS_UNMUTE}).mode == PRESS_UNMUTE and \
        Binding.from_dict({"mode": "nonsense"}).mode == "toggle"
