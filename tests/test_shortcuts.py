"""Keyboard shortcuts (core/shortcuts.py) on the mock card, with MockPlasma standing in for Plasma's global
shortcuts service: the keys, the level steps, the settings file, and the controller. Never touches the
desktop's shortcuts."""
import json
import math

import pytest
from PySide6.QtCore import Qt

from aa_hdspe_mixer.core.backend import MockBackend
from aa_hdspe_mixer.core.engine import Engine
from aa_hdspe_mixer.core.remote import FOLLOW, PRESS_UNMUTE
from aa_hdspe_mixer.core.routing import MIN_DB
from aa_hdspe_mixer.core.shortcuts import (DOWN, SET, UP, KeyBinding, KeySettings, MockPlasma, ShortcutControl,
                                           behaviour_text, combination, friendly, key_code, key_text,
                                           message_text, needs_modifier, step_level, validate)

CTRL_ALT = (Qt.ControlModifier | Qt.AltModifier).value


@pytest.fixture(scope="module")
def app():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication
    return QCoreApplication.instance() or QApplication([])


@pytest.fixture
def rig(app, tmp_path):
    e = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e.start()
    plasma = MockPlasma()
    kc = ShortcutControl(e, plasma, str(tmp_path / "shortcuts.json"))
    kc.set_enabled(True)
    yield e, kc, plasma
    kc.shutdown()
    e.shutdown()


def test_the_keys(app):
    assert key_code("Ctrl+Alt+M") == (Qt.Key_M.value | CTRL_ALT) and key_text(key_code("Ctrl+Alt+M")) == "Ctrl+Alt+M"
    assert key_code("") == key_code("Ctrl+M, Ctrl+K") == key_code("nonsense+") == 0
    assert combination(Qt.Key_M.value, CTRL_ALT) == "Ctrl+Alt+M"
    assert combination(Qt.Key_Control.value, Qt.ControlModifier.value) == ""          # only a modifier so far
    assert combination(Qt.Key_Backtab.value, Qt.ShiftModifier.value) == "Shift+Tab"
    assert combination(Qt.Key_VolumeUp.value, 0) == "Volume Up"
    assert needs_modifier(Qt.Key_M.value, 0) and needs_modifier(Qt.Key_M.value, Qt.ShiftModifier.value)
    assert needs_modifier(Qt.Key_Space.value, 0) and needs_modifier(Qt.Key_Left.value, 0)
    assert not needs_modifier(Qt.Key_M.value, Qt.MetaModifier.value)
    assert not needs_modifier(Qt.Key_F5.value, 0) and not needs_modifier(Qt.Key_VolumeMute.value, 0)


def test_level_steps():
    up = KeyBinding(what="level", target="out:6", action=UP, step=3, db_lo=-math.inf, db_hi=0)
    down = KeyBinding(what="level", target="out:6", action=DOWN, step=3, db_lo=-math.inf, db_hi=0)
    assert step_level(up, -math.inf) == -60 and step_level(up, -80) == -60 and step_level(up, -10) == -7
    assert step_level(up, -1) == 0 and step_level(up, 0) == 0 and step_level(up, 4) == 4     # never lowers
    assert step_level(down, -10) == -13 and step_level(down, -59) == -60 and step_level(down, -60) == -math.inf
    assert step_level(down, -math.inf) == -math.inf and step_level(down, 4) == 0
    fin = KeyBinding(what="level", target="out:6", action=DOWN, step=6, db_lo=-20, db_hi=0)
    assert step_level(fin, -18) == -20 and step_level(fin, -20) == -20 and step_level(fin, -30) == -30
    fin.action = UP
    assert step_level(fin, -math.inf) == -20 and step_level(fin, -30) == -20 and step_level(fin, -3) == 0
    s = KeyBinding(what="level", target="out:6", action=SET, level=-12)
    assert step_level(s, 0) == -12
    assert behaviour_text(up) == "Each press: 3 dB up, −∞ dB … 0 dB" and behaviour_text(s) == "Each press sets −12 dB"
    assert friendly(down, "Output AES") == "Level down 3 dB: Output AES"


def test_bindings_and_settings(tmp_path):
    assert "Choose a channel" in validate(KeyBinding(what="mute"))
    assert validate(KeyBinding(what="mute", target="in:0")) == ""                       # no keys yet is fine
    assert "isn't a key" in validate(KeyBinding(what="mute", target="in:0", key="Ctrl+M, Ctrl+K"))
    assert "Choose a preset" in validate(KeyBinding(what="preset", target="preset:number"))
    assert message_text(KeyBinding()) == "No keys yet" and message_text(KeyBinding(key="Ctrl+Alt+M")) == "Ctrl+Alt+M"
    mute = KeyBinding(what="mute", target="in:0", mode=FOLLOW, invert=True)
    assert behaviour_text(mute) == "Sounding while held" and friendly(mute, "In 1") == "Sound while held: In 1"
    path = str(tmp_path / "shortcuts.json")
    s = KeySettings(path)
    s.enabled = True
    s.bindings = [KeyBinding("k1", "level", "out:6", "Ctrl+Alt+Up", action=SET, level=-math.inf)]
    s.save()
    d = json.load(open(path))
    assert d["bindings"][0]["level"] is None
    d["bindings"].append({"id": "k2", "what": "mute", "target": ""})                 # bad: dropped
    d["bindings"].append({"id": "k3", "what": "level", "target": "out:6", "step": 99, "key": "junk+"})
    json.dump(d, open(path, "w"))
    t = KeySettings(path)
    assert t.enabled and [b.id for b in t.bindings] == ["k1", "k3"]
    assert t.bindings[0].level == -math.inf and t.bindings[1].step == 24 and t.bindings[1].key == ""
    assert t.next_id() == "k4"


def test_mutes_levels_presets(rig):
    e, kc, plasma = rig
    assert kc.save_binding(KeyBinding(what="mute", target="in:1", key="Ctrl+Alt+M")) == ""       # 1 of pair 0+1
    assert kc.save_binding(KeyBinding(what="mute", target="in:2", key="Ctrl+Alt+H", mode=FOLLOW)) == ""
    assert kc.save_binding(KeyBinding(what="level", target="out:6", key="Ctrl+Alt+Down", action=DOWN, step=6)) == ""
    assert kc.save_binding(KeyBinding(what="preset", target="preset:1", key="Ctrl+Alt+2")) == ""
    m, h, lv, p = (b.id for b in kc.bindings())
    assert plasma.keys[m] == key_code("Ctrl+Alt+M") and plasma.active == {m, h, lv, p}
    assert plasma.names[m] == "Mute or unmute: in:1"                     # (the window gives the channel's name)

    plasma.press(m)
    assert e.unit_muted("in:0") and kc.last == "Ctrl+Alt+M: pressed"
    plasma.press(m)                                     # no release seen yet: every press counts
    assert not e.unit_muted("in:0")
    plasma.release(m)
    plasma.press(m)
    plasma.press(m)                                     # releases are told now: this one is a key repeat
    assert e.unit_muted("in:0")
    plasma.release(m)

    plasma.press(h)
    assert e.unit_muted("in:2")
    plasma.release(h)
    assert not e.unit_muted("in:2") and kc.last == "Ctrl+Alt+H: let go"

    e.set_output_level("out:6", 0)
    plasma.press(lv)
    plasma.press(lv)                                    # held: levels keep stepping
    plasma.release(lv)
    assert e.output_level_db(6) == -12

    e.store_preset(1, "Two")
    msgs = []
    e.message.connect(lambda level, text: msgs.append(text))
    plasma.press(p)
    assert msgs == ["Recalled preset 2: Two"]


def test_plasma_side(rig, tmp_path):
    e, kc, plasma = rig
    assert kc.save_binding(KeyBinding(what="mute", target="in:0", key="Ctrl+Alt+M")) == ""
    bid = kc.bindings()[0].id
    plasma.others[key_code("Ctrl+Alt+K")] = "KWin: Kill Window"
    err = kc.save_binding(KeyBinding(what="mute", target="in:2", key="Ctrl+Alt+K"))
    assert err == "Ctrl+Alt+K is already a Plasma shortcut (KWin: Kill Window)."
    assert "already a shortcut here" in kc.save_binding(KeyBinding(what="mute", target="in:2", key="Ctrl+Alt+M"))

    plasma.change(bid, key_code("Meta+M"))              # in System Settings
    assert kc.binding(bid).key == "Meta+M"
    assert json.load(open(kc.settings.path))["bindings"][0]["key"] == "Meta+M"

    kc.set_recording(True)
    assert plasma.blocked
    kc.set_recording(False)
    assert not plasma.blocked

    kc.set_enabled(False)                               # off: the keys are free
    assert plasma.active == set() and plasma.keys[bid] == key_code("Meta+M")
    plasma.active.add(bid)
    plasma.press(bid)
    assert not e.unit_muted("in:0")                     # off: nothing happens
    kc.set_enabled(True)
    assert plasma.active == {bid}

    # At start, Plasma's key wins (changed in System Settings while the app wasn't running).
    plasma.keys[bid] = key_code("Meta+N")
    again = ShortcutControl(e, plasma, kc.settings.path)
    assert again.binding(bid).key == "Meta+N"
    again.shutdown()
    assert plasma.active == set()
    plasma.keyChanged.disconnect(again._on_key_changed)     # one controller per file, as in the app

    kc.remove_binding(bid)
    assert bid not in plasma.keys

    # Keys removed in System Settings delete the binding; one saved here without keys stays.
    msgs = []
    e.message.connect(lambda level, text: msgs.append(text))
    assert kc.save_binding(KeyBinding(what="mute", target="in:0", key="Ctrl+Alt+R")) == ""
    assert kc.save_binding(KeyBinding(what="mute", target="in:2")) == ""
    gone, kept = (b.id for b in kc.bindings())
    plasma.change(kept, 0)                              # (no keys here either: nothing to remove)
    plasma.change(gone, 0)
    assert [b.id for b in kc.bindings()] == [kept] and gone not in plasma.keys
    assert [b["id"] for b in json.load(open(kc.settings.path))["bindings"]] == [kept]
    assert msgs == ["Removed the keyboard shortcut “Mute or unmute: in:0”: its keys were removed in System Settings."]
    # ... also while the app wasn't running, unless something else has taken the keys meanwhile.
    for key in ("Ctrl+Alt+R", "Ctrl+Alt+T"):
        assert kc.save_binding(KeyBinding(what="mute", target="in:4", key=key)) == ""
    r, t = (b.id for b in kc.bindings() if b.key)
    plasma.keys[r] = plasma.keys[t] = 0
    plasma.others[key_code("Ctrl+Alt+T")] = "Other App: Do It"
    again = ShortcutControl(e, plasma, kc.settings.path)
    assert sorted(b.id for b in again.bindings()) == sorted([kept, t]) and again.binding(t).key == "Ctrl+Alt+T"
    again.shutdown()
    plasma.keyChanged.disconnect(again._on_key_changed)
    del plasma.others[key_code("Ctrl+Alt+T")]
    kc.remove_binding(r)
    kc.remove_binding(t)
    kc.remove_binding(kept)

    plasma.set_available(False)
    assert "isn't running" in kc.error and not kc._is_open()
    assert kc.save_binding(KeyBinding(what="mute", target="in:0", mode=PRESS_UNMUTE, key="Ctrl+Alt+U")) == ""
    plasma.set_available(True)                          # Plasma back: everything is given to it again
    assert kc.error == "" and plasma.keys[kc.bindings()[0].id] == key_code("Ctrl+Alt+U")


def test_connection_levels_never_remove(rig):
    e, kc, plasma = rig
    e.set_connection_db("in:0", "out:6", 0)
    assert kc.save_binding(KeyBinding(what="level", target="in:0>out:6", key="Ctrl+Alt+D", action=SET,
                                      level=-math.inf)) == ""
    plasma.press(kc.bindings()[0].id)
    assert e.connection_db("in:0", "out:6") == pytest.approx(MIN_DB, abs=0.01)
