import errno
import math

import pytest
from PySide6.QtCore import QCoreApplication

from aa_hdspe_mixer.core.backend import MockBackend
from aa_hdspe_mixer.core.channels import IN, OUT, PB, layout
from aa_hdspe_mixer.core.engine import Engine
from aa_hdspe_mixer.core.memory import Memory
from aa_hdspe_mixer.core.routing import (UNITY, Matrix, build_units, db_to_raw, format_db, parse_db, raw_to_db)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication        # widgets app: the tray-menu test needs one
    return QApplication.instance() or QApplication([])


def test_layout_adat_by_speed():
    adat = lambda sp: [c.hw for c in layout(IN, sp) if c.group == "ADAT"]
    assert adat(0) == list(range(12, 20))
    assert adat(1) == [12, 13, 14, 15]
    assert adat(2) == [12, 13]


def test_layout_boards_and_phones():
    assert [c.hw for c in layout(IN, 0, ai4s=False)][:3] == [0, 1, 8]
    assert [c.hw for c in layout(IN, 0, ai4s=True)][:6] == [0, 1, 2, 3, 4, 5]
    outs = [c.hw_name for c in layout(OUT, 0)]
    assert "PH L" in outs and "PH R" in outs
    assert "PH L" not in [c.hw_name for c in layout(IN, 0)]
    assert [c.mixer_src for c in layout(PB, 0)][:2] == [64, 65]


def test_db_conversions():
    assert db_to_raw(0) == UNITY
    assert db_to_raw(-math.inf) == 0
    assert db_to_raw(100) == 65535
    assert abs(raw_to_db(db_to_raw(-6.0)) + 6.0) < 0.01
    assert format_db(0.0) == "0.0" and format_db(-6.04) == "-6.0" and format_db(-math.inf) == "-inf"
    assert parse_db("-6 dB") == -6.0 and parse_db("inf") == -math.inf and parse_db("x") is None
    assert parse_db("+20") == pytest.approx(6.02, abs=0.01)
    assert parse_db("") == 0.0 and parse_db("  ") == 0.0 and parse_db("dB") == 0.0   # empty box = 0 dB, not -inf


def _units(kind, stereo=True, speed=0):
    return build_units(kind, layout(kind, speed), set(range(32)) if stereo else set())


def test_stereo_to_stereo_cells():
    m = Matrix()
    src, dst = _units(IN)[0], _units(OUT)[0]          # AN 1/2 -> AN 1/2
    assert src.hw_name == "AN 1/2" and dst.hw_name == "AN 1/2"
    ch = m.set_connection(src, dst, UNITY)
    assert sorted(ch) == [(0, 0, UNITY), (1, 1, UNITY)]
    conns = m.connections(_units(IN), _units(OUT))
    assert len(conns) == 1 and conns[0].gain == UNITY


def test_mono_to_stereo_and_mode_switch_keeps_routing():
    m = Matrix()
    mono_in = _units(IN, stereo=False)
    src = mono_in[0]                                   # AN 1 (mono)
    dst = _units(OUT)[1]                               # PH L/R
    m.set_connection(src, dst, UNITY)
    assert m.get(0, 6) == UNITY and m.get(0, 7) == UNITY
    # View the same matrix with the outputs in mono: two connections appear, nothing is lost.
    conns = m.connections(mono_in, _units(OUT, stereo=False))
    assert sorted(c.dst.hw_name for c in conns) == ["PH L", "PH R"]


def test_set_connection_clears_cross_terms_and_remove():
    m = Matrix()
    src, dst = _units(PB)[0], _units(OUT)[0]
    m.g[1][64] = 1000                                  # foreign cross term L->R
    m.set_connection(src, dst, UNITY)
    assert m.get(64, 1) == 0
    m.set_connection(src, dst, 0)
    assert m.connections(_units(PB), _units(OUT)) == []


def test_memory_roundtrip():
    mem = Memory(card_key="AIO:x")
    mem.set_name(IN, 0, "Mic")
    mem.stereo[OUT].discard(3)
    mem.matrix.g[6][64] = 1234
    mem.store_preset(5, "Live")
    back = Memory.from_dict(mem.to_dict())
    assert back.name_of(IN, 0) == "Mic"
    assert 3 not in back.stereo[OUT]
    assert back.matrix.get(64, 6) == 1234
    assert back.presets[5].name == "Live" and back.presets[5].matrix.get(64, 6) == 1234
    assert back.presets[0] is None


def test_engine_writes_and_presets(qapp, tmp_path):
    be = MockBackend()
    eng = Engine(be, memory_file=str(tmp_path / "mem.json"))
    eng.start()
    assert be.writes == 64 * 128                       # full state written at start
    eng.set_connection_db("in:0", "out:0", -6.0)
    assert be.g[0][0] == db_to_raw(-6.0) and be.g[1][1] == db_to_raw(-6.0)
    eng.store_preset(0, "A")
    eng.clear_routing()
    assert be.g[0][0] == 0 and eng.connections() == []
    assert eng.recall_preset(0)
    assert be.g[0][0] == db_to_raw(-6.0)
    eng.shutdown()
    eng2 = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    eng2.start()
    assert eng2.presets()[0].name == "A" and len(eng2.connections()) == 1


def test_engine_retries_busy_writes(qapp, tmp_path):
    be = MockBackend()
    eng = Engine(be, memory_file=str(tmp_path / "mem.json"))
    eng.start()
    be.fail_with = OSError(errno.EBUSY, "busy")
    eng.set_connection_db("pb:0", "out:6", 0.0)
    assert eng.pending and "busy" in eng.last_error.lower()
    be.fail_with = None
    eng._status_tick()
    assert not eng.pending and eng.last_error == ""
    assert be.g[6][64] == UNITY and be.g[7][65] == UNITY


def test_files(qapp, tmp_path):
    eng = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    eng.start()
    eng.set_connection_db("in:8", "out:8", 0.0)
    eng.rename(IN, 8, "AES mic")
    eng.save_preset_file(str(tmp_path / "p.json"), "AES thru")
    eng.export_memory(str(tmp_path / "m.json"))
    eng.clear_routing()
    assert eng.load_preset_file(str(tmp_path / "p.json")) == "AES thru"
    assert len(eng.connections()) == 1
    eng.rename(IN, 8, "")
    eng.import_memory(str(tmp_path / "m.json"))
    assert eng.memory.name_of(IN, 8) == "AES mic"
    with pytest.raises(ValueError):
        eng.load_preset_file(str(tmp_path / "m.json"))


def test_bridge_sees_loaded_memory(qapp, tmp_path):
    from aa_hdspe_mixer.ui.bridge import Bridge
    e1 = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e1.start()
    e1.store_preset(3, "Night")
    e1.memory.settings["meters"] = False
    e1.shutdown()
    e2 = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    b = Bridge(e2)
    seen = []
    b.presetsChanged.connect(lambda: seen.append("p"))
    e2.start()
    assert "p" in seen and b.presetNames[3] == "Night" and b.metersEnabled is False


def test_meter_rate_setting(qapp, tmp_path):
    e = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e.start()
    assert e.meter_hz == 20 and e._meter_timer.interval() == 50
    e.set_meter_hz(30)
    assert e._meter_timer.interval() == 33 and e.memory.settings["meter_hz"] == 30
    e.set_meter_hz(7)                      # not offered: ignored
    assert e.meter_hz == 30


def test_output_level_scales_hardware_cells(qapp, tmp_path):
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    e.set_connection_db("pb:0", "out:0", 0.0)                 # PB AN 1/2 -> OUT AN 1/2 at 0 dB
    assert be.g[0][64] == UNITY
    n = be.writes
    assert e.set_output_level("out:0", -6.0)
    assert be.g[0][64] == db_to_raw(-6.0) and be.g[1][65] == db_to_raw(-6.0)
    assert be.writes - n == 2                                  # only the changed cells are rewritten
    assert e.memory.matrix.get(64, 0) == UNITY                # stored route gain stays 0 dB
    assert e.connection_db("pb:0", "out:0") == pytest.approx(0.0)
    e.set_connection_db("pb:0", "out:0", -6.0)                 # route edits still get the output level
    assert be.g[0][64] == db_to_raw(-12.0)
    e.set_output_level("out:0", -math.inf)
    assert be.g[0][64] == 0 and e.memory.matrix.get(64, 0) != 0
    e.set_output_level("out:0", 0.0)
    assert 0 not in e.memory.out_levels and be.g[0][64] == db_to_raw(-6.0)


def test_output_level_in_presets_and_memory(qapp, tmp_path):
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    e.set_connection_db("in:0", "out:6", 0.0)
    e.set_output_level("out:6", -10.0)
    e.store_preset(0, "Quiet phones")
    e.set_output_level("out:6", 0.0)
    assert be.g[6][0] == UNITY
    e.recall_preset(0)
    assert e.output_level_db(6) == -10.0 and e.output_level_db(7) == -10.0
    assert be.g[6][0] == db_to_raw(-10.0)
    e.set_output_level("out:0", -math.inf)
    e.shutdown()
    back = Memory.from_dict(__import__("json").load(open(tmp_path / "mem.json")))
    assert back.out_levels[6] == -10.0 and back.out_levels[0] == -math.inf
    assert back.presets[0].out_levels == {6: -10.0, 7: -10.0}


def test_connection_order_and_hue_step(qapp, tmp_path):
    e = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e.start()
    assert e.hue_step == 20
    e.set_connection_db("pb:0", "out:0", 0.0)
    e.set_connection_db("in:0", "out:6", 0.0)
    e.set_connection_db("in:8", "out:0", 0.0)
    keys = {c.key: e.connection_seq(c.key) for c in e.connections()}
    assert keys == {"pb:0>out:0": 0, "in:0>out:6": 1, "in:8>out:0": 2}
    e.remove_connection("in:0", "out:6")                     # others keep their colours
    e.set_connection_db("in:2", "out:6", 0.0)
    assert e.connection_seq("pb:0>out:0") == 0 and e.connection_seq("in:8>out:0") == 2
    assert e.connection_seq("in:2>out:6") == 3
    e.set_hue_step(45)
    e.shutdown()
    back = Memory.from_dict(__import__("json").load(open(tmp_path / "mem.json")))
    assert back.settings["hue_step"] == 45 and back.conn_seq["in:2>out:6"] == 3 and back.next_seq == 4


def _preset_engine(tmp_path):
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    e.set_connection_db("pb:0", "out:0", -6.0)
    e.set_output_level("out:0", -3.0)
    e.rename(OUT, 0, "Monitors")
    e.store_preset(0, "A")
    # change everything afterwards
    e.remove_connection("pb:0", "out:0")
    e.set_connection_db("in:0", "out:0", -20.0)
    e.set_connection_db("pb:0", "out:6", -10.0)
    e.set_output_level("out:0", 0.0)
    e.rename(OUT, 0, "Other")
    return e, be


def test_preset_stores_names(qapp, tmp_path):
    e, _ = _preset_engine(tmp_path)
    p = e.presets()[0]
    assert p.names[OUT][0] == "Monitors"
    assert Memory.from_dict(e.memory.to_dict()).presets[0].names[OUT][0] == "Monitors"


def test_recall_everything(qapp, tmp_path):
    e, be = _preset_engine(tmp_path)
    e.recall_preset(0)
    assert e.memory.name_of(OUT, 0) == "Monitors"
    assert [c.key for c in e.connections()] == ["pb:0>out:0"]
    assert e.output_level_db(0) == -3.0
    assert be.g[0][64] == db_to_raw(-9.0)


def test_recall_names_only(qapp, tmp_path):
    e, be = _preset_engine(tmp_path)
    before = e.memory.matrix.copy()
    e.recall_preset(0, names=True, destinations=False, levels=False)
    assert e.memory.name_of(OUT, 0) == "Monitors"
    assert e.memory.matrix.g == before.g and e.output_level_db(0) == 0.0


def test_recall_destinations_only(qapp, tmp_path):
    e, be = _preset_engine(tmp_path)
    e.recall_preset(0, names=False, destinations=True, levels=False)
    assert [c.key for c in e.connections()] == ["pb:0>out:0"]
    assert e.connection_db("pb:0", "out:0") == pytest.approx(0.0)   # newly routed -> 0 dB
    assert e.output_level_db(0) == 0.0 and e.memory.name_of(OUT, 0) == "Other"


def test_recall_levels_only(qapp, tmp_path):
    e, be = _preset_engine(tmp_path)
    e.set_connection_db("pb:0", "out:0", -1.0)                      # route that the preset also has
    e.recall_preset(0, names=False, destinations=False, levels=True)
    assert e.connection_db("pb:0", "out:0") == pytest.approx(-6.0, abs=0.01)   # preset level
    assert e.connection_db("in:0", "out:0") == pytest.approx(-20.0, abs=0.01)  # not in preset: kept
    assert e.output_level_db(0) == -3.0


def test_old_preset_without_names_keeps_names(qapp, tmp_path):
    e, _ = _preset_engine(tmp_path)
    e.memory.presets[0].names = None
    e.recall_preset(0)
    assert e.memory.name_of(OUT, 0) == "Other"


def test_wheel_nudge_never_removes(qapp, tmp_path):
    from aa_hdspe_mixer.core.routing import MIN_DB
    e = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e.start()
    e.set_connection_db("pb:0", "out:0", -89.5)
    for _ in range(10):
        assert e.nudge_connection("pb:0", "out:0", -0.5)
    assert e.memory.matrix.get(64, 0) == 1                       # quietest possible, still connected
    assert e.connection_db("pb:0", "out:0") == pytest.approx(MIN_DB)
    e.set_connection_db("pb:0", "out:0", 5.8)
    e.nudge_connection("pb:0", "out:0", +0.5)
    assert e.memory.matrix.get(64, 0) == 65535
    assert not e.nudge_connection("in:0", "out:6", +0.5)        # no connection: nothing created
    assert len(e.connections()) == 1


def test_wheel_nudge_never_sticks(qapp, tmp_path):
    """Regression: at low levels a 0.5 dB step rounded back to the same raw gain (stuck at -72.2 dB)."""
    e = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e.start()
    e.set_connection_db("pb:0", "out:0", 0.0)
    raws = [e.memory.matrix.get(64, 0)]
    while raws[-1] > 1:
        e.nudge_connection("pb:0", "out:0", -0.5)
        raws.append(e.memory.matrix.get(64, 0))
        assert raws[-1] < raws[-2] and len(raws) < 400
    e.nudge_connection("pb:0", "out:0", -0.5)
    assert e.memory.matrix.get(64, 0) == 1                       # floor holds, still connected
    while raws[-1] < 65535:
        e.nudge_connection("pb:0", "out:0", +0.5)
        raws.append(e.memory.matrix.get(64, 0))
        assert raws[-1] > raws[-2] and len(raws) < 800


def _mute_engine(tmp_path):
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    e.set_connection_db("pb:0", "out:0", -6.0)       # PB AN 1/2 -> OUT AN 1/2 (stereo: cells 64->0, 65->1)
    e.set_connection_db("in:0", "out:6", 0.0)        # IN AN 1/2 -> PH (cells 0->6, 1->7)
    return e, be


def test_connection_mute_keeps_connection_and_level(qapp, tmp_path):
    e, be = _mute_engine(tmp_path)
    assert e.toggle_connection_mute("pb:0", "out:0")
    assert e.connection_muted("pb:0", "out:0") and not e.connection_muted("in:0", "out:6")
    assert be.g[0][64] == 0 and be.g[1][65] == 0                      # hardware: same as -inf
    assert [c.key for c in e.connections()] == ["pb:0>out:0", "in:0>out:6"]   # app: still connected
    assert e.connection_db("pb:0", "out:0") == pytest.approx(-6.0, abs=0.01)
    e.set_connection_db("pb:0", "out:0", -3.0)                         # level change while muted
    assert e.connection_muted("pb:0", "out:0") and be.g[0][64] == 0
    assert e.nudge_connection("pb:0", "out:0", +0.5) and be.g[0][64] == 0
    e.toggle_connection_mute("pb:0", "out:0")
    assert be.g[0][64] == db_to_raw(-2.5) and be.g[1][65] == db_to_raw(-2.5)
    assert not e.set_connection_mute("in:2", "out:6", True)            # no connection: nothing created
    assert len(e.connections()) == 2


def test_connection_mute_survives_stereo_switch_and_dies_with_connection(qapp, tmp_path):
    e, be = _mute_engine(tmp_path)
    e.set_connection_mute("pb:0", "out:0", True)
    e.set_stereo(PB, 0, False)                                         # PB 1 and PB 2 become mono
    assert e.connection_muted("pb:0", "out:0") and e.connection_muted("pb:1", "out:0")
    e.set_stereo(PB, 0, True)
    assert e.connection_muted("pb:0", "out:0")
    e.remove_connection("pb:0", "out:0")
    assert e.memory.muted_cells == set()
    e.set_connection_db("pb:0", "out:0", 0.0)                          # re-created: unmuted
    assert not e.connection_muted("pb:0", "out:0") and be.g[0][64] == UNITY


def test_channel_mutes(qapp, tmp_path):
    e, be = _mute_engine(tmp_path)
    e.set_connection_db("pb:0", "out:6", 0.0)
    assert e.toggle_unit_mute("pb:0")                                  # source mute: all its routes
    assert e.unit_muted("pb:0") and e.memory.mutes[PB] == {0, 1}
    assert be.g[0][64] == 0 and be.g[6][64] == 0 and be.g[6][0] == UNITY
    assert e.connection_silenced("pb:0", "out:6") and not e.connection_muted("pb:0", "out:6")
    e.toggle_unit_mute("pb:0")
    assert be.g[0][64] == db_to_raw(-6.0) and be.g[6][64] == UNITY
    e.set_unit_mute("out:6", True)                                     # output mute: the whole row
    assert be.g[6][0] == 0 and be.g[7][1] == 0 and be.g[6][64] == 0 and be.g[0][64] != 0
    e.set_connection_mute("in:0", "out:6", True)
    e.set_unit_mute("out:6", False)                                    # the connection's own mute stays
    assert be.g[6][0] == 0 and be.g[6][64] == UNITY


def test_mutes_in_memory_and_presets(qapp, tmp_path):
    e, be = _mute_engine(tmp_path)
    e.set_unit_mute("in:0", True)
    e.set_connection_mute("pb:0", "out:0", True)
    e.store_preset(0, "Muted")
    e.set_unit_mute("in:0", False)
    e.set_connection_mute("pb:0", "out:0", False)
    e.recall_preset(0, names=False, destinations=True, levels=True, mutes=False)   # mutes are separate
    assert not e.unit_muted("in:0") and not e.connection_muted("pb:0", "out:0")
    e.set_connection_db("pb:0", "out:0", -1.0)
    e.recall_preset(0, names=False, destinations=False, levels=False, mutes=True)
    assert e.unit_muted("in:0") and e.connection_muted("pb:0", "out:0")
    assert e.connection_db("pb:0", "out:0") == pytest.approx(-1.0, abs=0.01)          # level untouched
    assert be.g[6][0] == 0 and be.g[0][64] == 0
    e.set_connection_db("in:2", "out:6", 0.0)                                         # not in the preset
    e.set_connection_mute("in:2", "out:6", True)
    e.recall_preset(0, names=False, destinations=False, levels=True, mutes=True)
    assert e.connection_muted("in:2", "out:6")                                        # keeps its own mute
    e.remove_connection("in:2", "out:6")
    e.shutdown()
    back = Memory.from_dict(__import__("json").load(open(tmp_path / "mem.json")))
    assert back.mutes[IN] == {0, 1} and back.muted_cells == {(64, 0), (65, 1)}
    assert back.presets[0].mutes[IN] == {0, 1} and back.presets[0].muted_cells == {(64, 0), (65, 1)}
    old = back.presets[0].to_dict()
    del old["mutes"], old["muted_cells"]                               # presets from before mutes
    p = __import__("aa_hdspe_mixer.core.memory", fromlist=["Preset"]).Preset.from_dict(old)
    assert p.mutes[IN] == set() and p.muted_cells == set()


def test_card_settings_registry():
    from aa_hdspe_mixer.core.card_settings import settings_for
    ids = ["analog_in_level", "analog_out_level", "phones_level", "spdif_in", "spdif_out", "spdif_pro",
           "adat_in_source", "optical_in", "optical_out"]
    assert [s.id for s in settings_for("AIO")] == ids + ["breakout_cable", "tms", "ss_wordclock_out"]
    assert [s.id for s in settings_for("AIO Pro")] == ids + ["tms", "ss_wordclock_out"]
    assert settings_for("AIO")[0].choices == ("Lo Gain", "+4 dBu", "-10 dBV")
    assert settings_for("AIO Pro")[0].choices == ("+4 dBu", "+13 dBu", "+19 dBu", "+24 dBu")
    assert settings_for("Some other card") == ()


def test_card_setting_spdif_input(qapp, tmp_path):
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    assert e.card_setting("spdif_in") == "Coaxial"                 # empty memory: adopt the card's value
    assert e.setting_choices("spdif_in") == ("Optical", "Coaxial", "Internal")
    spdif = e.unit("in:10")
    assert [s.id for s in e.unit_settings(spdif)] == ["spdif_in"] and e.unit_settings(e.unit("in:2")) == []
    assert e.set_card_setting("spdif_in", "Optical") and be.settings["spdif_in"] == "Optical"
    assert not e.set_card_setting("spdif_in", "Bogus") and e.card_setting("spdif_in") == "Optical"
    e.set_stereo(IN, 5, False)                                       # S/PDIF L and R as mono: both show it
    assert e.unit_settings(e.unit("in:10")) and e.unit_settings(e.unit("in:11"))
    be.settings["spdif_in"] = "Internal"                              # changed outside the app (alsamixer)
    e._status_tick()
    assert e.card_setting("spdif_in") == "Internal"
    e.shutdown()
    be2 = MockBackend()                                               # e.g. after a reboot: card at default
    e2 = Engine(be2, memory_file=str(tmp_path / "mem.json"))
    e2.start()
    assert be2.settings["spdif_in"] == "Internal"                     # memory written to the card at start


def test_card_settings_in_presets(qapp, tmp_path):
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    e.set_card_setting("spdif_in", "Optical")
    e.store_preset(0, "Optical in")
    e.set_card_setting("spdif_in", "Coaxial")
    e.recall_preset(0, card=False)
    assert be.settings["spdif_in"] == "Coaxial"
    e.recall_preset(0)
    assert be.settings["spdif_in"] == "Optical" and e.card_setting("spdif_in") == "Optical"
    d = e.presets()[0].to_dict()
    assert d["card_settings"] == {"analog_in_level": "+4 dBu", "analog_out_level": "+4 dBu",
                                  "phones_level": "+4 dBu", "spdif_in": "Optical", "spdif_out": "Coaxial",
                                  "spdif_pro": "Consumer", "adat_in_source": "Optical"}
    del d["card_settings"]                                            # preset from before card settings
    e.set_card_setting("spdif_in", "Internal")
    from aa_hdspe_mixer.core.memory import Preset
    e.apply_preset(Preset.from_dict(d))
    assert e.card_setting("spdif_in") == "Internal"                   # left alone


def test_spdif_output_switches(qapp, tmp_path):
    from aa_hdspe_mixer.ui.bridge import Bridge
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    assert e.card_setting("spdif_out") == "Coaxial" and e.card_setting("spdif_pro") == "Consumer"
    out = e.unit("out:10")
    assert [s.id for s in e.unit_settings(out)] == ["spdif_out", "spdif_pro"]
    assert e.unit_settings(e.unit("in:10"))[0].id == "spdif_in"
    assert e.set_card_setting("spdif_out", "Optical") and e.set_card_setting("spdif_pro", "Professional")
    assert be.settings == {"spdif_out": "Optical", "spdif_pro": "Professional"}
    b = Bridge(e)
    u = next(x for x in b.outputs if x["key"] == "out:10")
    assert u["settingTags"] == ["COAX+OPT · P"]              # coaxial always runs; optical is a copy
    (menu,) = u["settingMenus"]
    assert menu["title"] == "S/PDIF Output"
    assert [(x["text"], x["header"], x["checked"]) for x in menu["entries"]] == [
        ("Connectors", True, False), ("Coaxial only", False, False), ("Coaxial + Optical (ADAT output off)", False, True),
        ("Format", True, False), ("Consumer", False, False), ("Professional", False, True)]
    assert b.cardSettingLabels == ["Analog Input Level", "Analog Output Level", "Phones Level", "S/PDIF Input",
                                 "S/PDIF Output", "ADAT Input"]
    i = next(x for x in b.inputs if x["key"] == "in:10")
    assert i["settingTags"] == ["COAXIAL"] and [x["text"] for x in i["settingMenus"][0]["entries"]] == [
        "Optical (ADAT input off unless internal)", "Coaxial", "Internal"]           # single section: no heading


def test_optical_port_shared_by_adat_and_spdif(qapp, tmp_path):
    from aa_hdspe_mixer.ui.bridge import Bridge
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e)
    tags = lambda units, key: next(x for x in units if x["key"] == key)["settingTags"]
    assert e.card_setting("optical_in") == "ADAT" and e.card_setting("optical_out") == "ADAT"
    assert [s.id for s in e.unit_settings(e.unit("in:12"))] == ["adat_in_source", "optical_in"]
    assert [s.id for s in e.unit_settings(e.unit("out:18"))] == ["optical_out"]
    assert tags(b.inputs, "in:12") == ["OPTICAL"] and tags(b.outputs, "out:12") == ["ADAT"]
    # S/PDIF input to Optical: the ADAT input is off, the ADAT output is not
    e.set_card_setting("spdif_in", "Internal")
    e.set_card_setting("spdif_in", "Optical")
    assert e.card_setting("optical_in") == "S/PDIF" and e.card_setting("optical_out") == "ADAT"
    assert tags(b.inputs, "in:12") == ["OFF"] and tags(b.inputs, "in:18") == ["OFF"]
    # From the ADAT side: back to ADAT restores the S/PDIF input's previous connector (Internal)
    assert e.set_card_setting("optical_in", "ADAT")
    assert be.settings["spdif_in"] == "Internal" and tags(b.inputs, "in:12") == ["OPTICAL"]
    # Output: choosing S/PDIF on an ADAT output pill puts S/PDIF on the optical port
    assert e.set_card_setting("optical_out", "S/PDIF")
    assert be.settings["spdif_out"] == "Optical" and tags(b.outputs, "out:12") == ["OFF"]
    assert next(x for x in b.outputs if x["key"] == "out:10")["settingTags"] == ["COAX+OPT · C"]
    e.set_card_setting("optical_out", "ADAT")
    assert be.settings["spdif_out"] == "Coaxial"
    # Presets store the real settings only; the ADAT side comes back with them
    e.set_card_setting("spdif_in", "Optical")
    e.store_preset(0, "S/PDIF optical in")
    assert set(e.presets()[0].card_settings) == {"analog_in_level", "analog_out_level", "phones_level",
                                                 "spdif_in", "spdif_out", "spdif_pro", "adat_in_source"}
    e.set_card_setting("optical_in", "ADAT")
    e.recall_preset(0)
    assert e.card_setting("optical_in") == "S/PDIF" and be.settings["spdif_in"] == "Optical"
    assert b.cardSettingLabels == ["Analog Input Level", "Analog Output Level", "Phones Level", "S/PDIF Input",
                                 "S/PDIF Output", "ADAT Input"]
    # Double speed: four ADAT slots, still linked
    be.speed = 1
    e._status_tick()
    assert [u.key for u in e.units(IN) if e.unit_settings(u) and u.key not in ("in:0", "in:10")] == \
        ["in:12", "in:14"]


def test_analog_input_level(qapp, tmp_path):
    from aa_hdspe_mixer.ui.bridge import Bridge
    from aa_hdspe_mixer.core.backend import AlsaBackend
    from aa_hdspe_mixer.core.card_settings import INPUT_LEVEL
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e)
    assert e.card_setting("analog_in_level") == "+4 dBu"
    assert [s.id for s in e.unit_settings(e.unit("in:0"))] == ["analog_in_level"]
    assert [s.id for s in e.unit_settings(e.unit("out:0"))] == ["analog_out_level"]   # not the input one
    assert e.set_card_setting("analog_in_level", "Lo Gain") and be.settings["analog_in_level"] == "Lo Gain"
    an = next(x for x in b.inputs if x["key"] == "in:0")
    assert an["settingTags"] == ["LO GAIN"] and an["settingMenus"][0]["title"] == "Analog Input Level"
    e.set_card_setting("analog_in_level", "-10 dBV")
    assert next(x for x in b.inputs if x["key"] == "in:0")["settingTags"] == ["-10 dBV"]
    # snd-hdspm calls item 0 "Hi Gain": shown and stored as "Lo Gain", written back as item 0
    class FakeCtl:
        value = 0
        def info(self, eid): return type("I", (), {"items": ("Hi Gain", "+4 dBu", "-10 dBV")})()
        def read(self, eid): return [self.value]
        def write(self, eid, v): self.value = v[0]
    ab = AlsaBackend.__new__(AlsaBackend)
    ab.info = type("Info", (), {"elements": {(INPUT_LEVEL.controls[0].iface, "Input Level")}})()
    ab.ctl, ab._enum_cache = FakeCtl(), {}
    assert ab.setting_choices(INPUT_LEVEL) == ("Lo Gain", "+4 dBu", "-10 dBV")
    assert ab.read_setting(INPUT_LEVEL) == "Lo Gain"
    ab.write_setting(INPUT_LEVEL, "-10 dBV")
    assert ab.ctl.value == 2


def test_output_and_phones_levels(qapp, tmp_path):
    from aa_hdspe_mixer.ui.bridge import Bridge
    from aa_hdspe_mixer.core.card_settings import settings_for
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e)
    assert [s.id for s in e.unit_settings(e.unit("out:0"))] == ["analog_out_level"]
    assert [s.id for s in e.unit_settings(e.unit("out:6"))] == ["phones_level"]
    assert e.unit_settings(e.unit("out:8")) == []                           # AES: none
    assert e.set_card_setting("analog_out_level", "Hi Gain") and e.set_card_setting("phones_level", "-10 dBV")
    assert be.settings["analog_out_level"] == "Hi Gain" and be.settings["phones_level"] == "-10 dBV"
    tags = {x["key"]: x["settingTags"] for x in b.outputs}
    assert tags["out:10"] == ["COAX · C"]                          # default: coaxial only, consumer
    assert tags["out:0"] == ["HI GAIN"] and tags["out:6"] == ["-10 dBV"]
    # the app's own Output Level (matrix scaling) is separate from the card's analog output level
    e.set_output_level("out:0", -6.0)
    assert e.card_setting("analog_out_level") == "Hi Gain" and e.output_level_db(0) == -6.0
    pro = {s.id: s for s in settings_for("AIO Pro")}
    assert len(pro["analog_out_level"].choices) == 8 and pro["phones_level"].choices == ("Lo Power", "Hi Power")
    mp = MockBackend("AIO Pro")
    ep = Engine(mp, memory_file=str(tmp_path / "pro.json"))
    ep.start()
    assert ep.card_setting("analog_out_level") == "+4 dBu XLR" and ep.card_setting("phones_level") == "Lo Power"


def test_breakout_cable_panel_setting_not_in_presets(qapp, tmp_path):
    from aa_hdspe_mixer.ui.bridge import Bridge
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e)
    assert [s.id for s in e.panel_settings()] == ["breakout_cable", "tms", "ss_wordclock_out"]
    assert all(s.id != "breakout_cable" for u in e.units(OUT) + e.units(IN) for s in e.unit_settings(u))
    ps = b.panelSettings[0]
    assert ps["label"] == "Analog Breakout Cable" and ps["choices"] == ["RCA", "XLR"] and ps["value"] == "RCA"
    assert e.set_card_setting("breakout_cable", "XLR") and be.settings["breakout_cable"] == "XLR"
    assert b.panelSettings[0]["value"] == "XLR"
    e.store_preset(0, "P")
    assert "breakout_cable" not in e.presets()[0].card_settings          # not stored in presets
    e.save_preset_file(str(tmp_path / "p.json"), "P")
    assert "breakout_cable" not in __import__("json").load(open(tmp_path / "p.json"))["card_settings"]
    e.set_card_setting("breakout_cable", "RCA")
    p = e.presets()[0]
    p.card_settings["breakout_cable"] = "XLR"                            # even if a file had it
    e.recall_preset(0)
    assert be.settings["breakout_cable"] == "RCA"                        # recall leaves it alone
    assert "Analog Breakout Cable" not in b.cardSettingLabels
    assert not b.settingsPanelOpen
    b.settingsPanelOpen = True
    assert b.settingsPanelOpen
    e.shutdown()
    e2 = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))    # kept in the card's memory
    e2.start()
    assert e2.card_setting("breakout_cable") == "RCA" and e2.backend.settings.get("breakout_cable", "RCA") == "RCA"
    e2.set_card_setting("breakout_cable", "XLR")
    e2.shutdown()
    e3 = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e3.start()
    assert e3.backend.settings["breakout_cable"] == "XLR"               # written to the card at start
    pro = Engine(MockBackend("AIO Pro"), memory_file=str(tmp_path / "pro.json"))
    pro.start()
    assert [s.id for s in pro.panel_settings()] == ["tms", "ss_wordclock_out"]


def test_tms_inverted_and_missing_driver(qapp, tmp_path):
    from aa_hdspe_mixer.ui.bridge import Bridge
    from aa_hdspe_mixer.core.backend import AlsaBackend
    from aa_hdspe_mixer.core.card_settings import TMS
    # snd-hdspe 'Clear TMS' is inverted: control on = TMS off
    class FakeCtl:
        value = 1
        def info(self, eid): return type("I", (), {"items": ()})()
        def read(self, eid): return [self.value]
        def write(self, eid, v): self.value = v[0]
    ab = AlsaBackend.__new__(AlsaBackend)
    ab.info = type("Info", (), {"elements": {(TMS.controls[0].iface, "Clear TMS")}})()
    ab.ctl, ab._enum_cache = FakeCtl(), {}
    assert ab.setting_choices(TMS) == ("Off", "On") and ab.read_setting(TMS) == "Off"
    ab.write_setting(TMS, "On")
    assert ab.ctl.value == 0 and ab.read_setting(TMS) == "On"
    ab.write_setting(TMS, "Off")
    assert ab.ctl.value == 1
    # a driver without the control (snd-hdspm): shown disabled in the panel, with a note
    class NoTms(MockBackend):
        def setting_choices(self, s):
            return None if s.id == "tms" else super().setting_choices(s)
    be = NoTms()
    be.info.driver = "snd_hdspm"
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e)
    assert [s.id for s in e.panel_settings()] == ["breakout_cable", "ss_wordclock_out"]
    last = b.panelSettings[-1]
    assert last["id"] == "tms" and not last["available"] and last["note"] == "Not available with snd-hdspm."
    assert not e.set_card_setting("tms", "On")
    assert b.panelSettings[0]["available"] and b.panelSettings[0]["description"] == ""
    assert "Track Marker" in TMS.label and TMS.description


def test_adat_input_connector(qapp, tmp_path):
    from aa_hdspe_mixer.ui.bridge import Bridge
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e)
    unit = lambda key: next(x for x in b.inputs if x["key"] == key)
    assert e.card_setting("adat_in_source") == "Optical"
    (menu,) = unit("in:12")["settingMenus"]
    assert menu["title"] == "ADAT Input"
    assert [(x["text"], x["header"], x["checked"]) for x in menu["entries"]] == [
        ("Connector", True, False), ("Optical", False, True), ("Internal (AEB / TEB header)", False, False),
        ("Optical Port", True, False), ("ADAT", False, True), ("S/PDIF (ADAT input off unless internal)", False, False)]
    assert e.set_card_setting("adat_in_source", "Internal") and be.settings["adat_in_source"] == "Internal"
    assert unit("in:12")["settingTags"] == ["INTERNAL"] and unit("in:18")["settingTags"] == ["INTERNAL"]
    e.set_card_setting("spdif_in", "Optical")                   # optical connector free: ADAT stays on
    assert unit("in:12")["settingTags"] == ["INTERNAL"]
    e.set_card_setting("adat_in_source", "Optical")             # both want the optical connector
    assert unit("in:12")["settingTags"] == ["OFF"]
    e.set_card_setting("adat_in_source", "Internal")
    e.store_preset(0, "AEB")
    e.set_card_setting("adat_in_source", "Optical")
    e.recall_preset(0, card=False)
    assert be.settings["adat_in_source"] == "Optical"
    e.recall_preset(0)                                           # stored in presets
    assert be.settings["adat_in_source"] == "Internal" and e.presets()[0].card_settings["adat_in_source"] == "Internal"
    assert e.unit_settings(e.unit("out:12"))[0].id == "optical_out"     # outputs: unchanged


def test_single_speed_word_clock_out(qapp, tmp_path):
    from aa_hdspe_mixer.ui.bridge import Bridge
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e)
    wc = next(x for x in b.panelSettings if x["id"] == "ss_wordclock_out")
    assert wc["label"] == "Single Speed Word Clock Out" and wc["choices"] == ["Off", "On"] and wc["value"] == "Off"
    assert e.set_card_setting("ss_wordclock_out", "On") and be.settings["ss_wordclock_out"] == "On"
    e.store_preset(0, "P")
    assert "ss_wordclock_out" not in e.presets()[0].card_settings       # panel setting: not in presets


def test_clock_engine_and_bridge(qapp, tmp_path):
    from aa_hdspe_mixer.ui.bridge import Bridge
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e)
    assert e.clock is None                                    # read only while the clock section is open
    b.clockPanelOpen = True
    assert e.clock is not None and b.clockAvailable
    rows = {r["key"]: r for r in b.clockSources}
    assert list(rows) == ["internal", "adat", "aes", "spdif", "tco", "syncin"]
    assert rows["internal"]["checked"] and rows["internal"]["status"] == "48 kHz"
    assert rows["adat"]["status"] == "Sync · 48 kHz" and rows["aes"]["status"] == "Lock · 48 kHz"
    assert rows["spdif"]["status"] == "No signal" and rows["tco"]["status"] == "Not available"
    assert e.set_clock_source("adat") and not be.clock.master and be.clock.preferred == "adat"
    rows = {r["key"]: r for r in b.clockSources}
    assert rows["adat"]["checked"] and rows["adat"]["inUse"] and not rows["internal"]["checked"]
    assert not b.clockTune["enabled"]                         # tuning only with the internal clock
    assert not e.set_tuned_rate(48010)
    assert e.set_clock_source("internal") and be.clock.master
    assert e.set_internal_rate(44100) and be.clock.internal_rate == 44100 and b.clockRate == 44100
    assert not e.set_internal_rate(12345)
    assert e.tune_range() == (41895, 46305)                   # +/- 5 %
    assert b.setTunedRate("44 110.5 Hz") and be.clock.tuned == 44110.5
    assert b.setTunedRate("99999") and be.clock.tuned == 46305  # clamped
    assert not b.setTunedRate("fast")
    assert b.resetTune() and be.clock.tuned == 44100
    be.clock.fail_with = OSError(16, "Device or resource busy")
    assert not e.set_internal_rate(48000) and "Could not set" in e.last_error
    b.set_window_shown(False)                                 # hidden to tray / minimised: no polling
    assert not e._clock_active
    b.set_window_shown(True)
    assert e._clock_active
    b.clockPanelOpen = False
    assert not e._clock_active
    b.set_window_shown(False); b.set_window_shown(True)
    assert not e._clock_active                                # section closed: stays off


def test_clock_driver_mappings():
    from aa_hdspe_mixer.core.alsa import IFACE_CARD, IFACE_HWDEP, IFACE_MIXER
    from aa_hdspe_mixer.core.clock import DDS_FCONST, AlsaClock, rate_hz, source_key
    assert rate_hz("44.1 KHz") == 44100 and rate_hz("48 kHz") == 48000 and rate_hz("No Lock") is None
    assert source_key("Word Clock") == source_key("WordClk") == "word" and source_key("S/PDIF") == "spdif"
    assert source_key("Sync In") == source_key("SyncIn") == "syncin"
    freq = ("No Lock", "32 kHz", "44.1 kHz", "48 kHz", "64 kHz", "88.2 kHz", "96 kHz", "128 kHz", "176.4 kHz",
            "192 kHz")

    class Ctl:
        def __init__(self, items, values):
            self.items, self.values, self.writes = items, values, []
        def info(self, eid): return type("I", (), {"items": self.items.get(eid.name, ())})()
        def read(self, eid): return list(self.values[eid.name])
        def write(self, eid, v):
            self.writes.append((eid.name, v[0]))
            self.values[eid.name] = [v[0]]

    # snd-hdspm: per-source controls, single speed only, tuning in Hz
    items = {"System Clock Mode": ("Master", "AutoSync"),
             "Preferred Sync Reference": ("Word Clock", "ADAT", "AES", "SPDIF", "Sync In"),
             "Internal Clock": freq[1:]}
    values = {"System Clock Mode": [0], "Preferred Sync Reference": [2], "Internal Clock": [2],
              "System Sample Rate": [48000]}
    for p in ("WC", "ADAT", "AES", "SPDIF", "SYNC IN", "TCO"):
        items[f"{p} SyncCheck"] = ("No Lock", "Lock", "Sync", "N/A")
        items[f"{p} Frequency"] = freq
        values[f"{p} SyncCheck"] = [2 if p == "AES" else 3 if p == "TCO" else 0]
        values[f"{p} Frequency"] = [3 if p == "AES" else 0]
    el = {(IFACE_MIXER, n) for n in values}
    c = Ctl(items, values)
    k = AlsaClock(c, el, "snd_hdspm")
    st = k.read(0)
    assert st.master and st.preferred == "aes" and st.sources == ("word", "adat", "aes", "spdif", "syncin")
    assert st.rates == (32000, 44100, 48000)                  # no double/quad speed under snd-hdspm
    assert st.status["aes"].sync == "Sync" and st.status["aes"].rate == 48000
    assert st.tuned_rate == 48000 and st.tune_max == 51750
    k.write_master(False); k.write_preferred("spdif"); k.write_internal_rate(44100); k.write_tuned_rate(44105.4, 0)
    assert c.writes == [("System Clock Mode", 1), ("Preferred Sync Reference", 3), ("Internal Clock", 1),
                        ("System Sample Rate", 44105)]

    # snd-hdspe: AutoSync arrays, all rates, DDS register for tuning
    items = {"Clock Mode": ("AutoSync", "Master"),
             "Preferred AutoSync Reference": ("WordClk", "AES", "S/PDIF", "ADAT", "TCO", "SyncIn"),
             "Internal Frequency": tuple(f.replace("kHz", "KHz") for f in freq[1:]),
             "AutoSync Status": ("No Lock", "Lock", "Sync", "N/A"), "AutoSync Frequency": ("",) + freq[1:],
             "Current AutoSync Reference": ("WordClk", "AES", "S/PDIF", "ADAT", "TCO", "SyncIn", "Intern")}
    values = {"Clock Mode": [0], "Preferred AutoSync Reference": [3], "Internal Frequency": [5],
              "AutoSync Status": [0, 1, 0, 2, 3, 0], "AutoSync Frequency": [0, 3, 0, 6, 0, 0],
              "Current AutoSync Reference": [3], "DDS": [round(DDS_FCONST / 48000)]}
    el = {(IFACE_CARD, n) for n in values if n != "DDS"} | {(IFACE_HWDEP, "DDS")}
    c = Ctl(items, values)
    k = AlsaClock(c, el, "snd_hdspe")
    st = k.read(1)                                            # double speed
    assert not st.master and st.preferred == "adat" and st.current == "adat" and st.internal_rate == 96000
    assert len(st.rates) == 9 and st.status["adat"].sync == "Sync" and st.status["adat"].rate == 96000
    assert st.status["aes"].sync == "Lock" and st.status["tco"].sync == "N/A"
    assert abs(st.tuned_rate - 96000) < 1 and st.tune_max == 103500
    k.write_tuned_rate(96100, 1)
    assert c.writes[-1] == ("DDS", round(DDS_FCONST / 48050))
    k.write_master(True)
    assert c.writes[-1] == ("Clock Mode", 1)


def test_card_settings_polling_paused_while_hidden(qapp, tmp_path):
    from aa_hdspe_mixer.ui.bridge import Bridge
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e)
    b.set_window_shown(False)
    be.settings["spdif_in"] = "Internal"                     # changed in alsamixer while hidden
    e._status_tick()
    assert e.card_setting("spdif_in") == "Coaxial"           # not polled while hidden
    b.set_window_shown(True)
    assert e.card_setting("spdif_in") == "Internal"          # re-read at once when shown
    be.settings["spdif_in"] = "Optical"
    e._status_tick()
    assert e.card_setting("spdif_in") == "Optical"
    assert e.set_card_setting("spdif_in", "Coaxial")          # writes still work while hidden
    b.set_window_shown(False)
    assert e.set_card_setting("spdif_in", "Internal") and be.settings["spdif_in"] == "Internal"


def test_tray_menu_survives_rebuilds(qapp, tmp_path):
    from PySide6.QtWidgets import QApplication, QMenu
    from aa_hdspe_mixer.ui.app import Shell
    from aa_hdspe_mixer.ui.bridge import Bridge
    app = QApplication.instance() or QApplication([])
    e = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e)
    sh = Shell(app, e, b)
    sh.tray, sh._menu = object(), QMenu()              # no system tray offscreen: just the menu
    for _ in range(3):
        sh._rebuild_tray_menu()
        e.store_preset(0, "P")                            # presetsChanged rebuilds it again
    texts = [a.text() for a in sh._menu.actions() if not a.isSeparator()]
    assert texts[0] == "Show" and texts[-1] == "Exit" and "Recall Preset" in texts and "Show Meters" in texts


def test_model_from_pci_vendor():
    from aa_hdspe_mixer.core.card import model_of
    assert model_of("RME AIO", 0x1d18) == "AIO Pro"             # snd-hdspm on an AIO Pro (Linux 7.3+)
    assert model_of("RME AIO Pro", 0x1d18) == "AIO Pro"         # snd-hdspe
    assert model_of("RME AIO", 0x10ee) == "AIO"
    assert model_of("RME AIO Pro", None) == "AIO Pro" and model_of("RME AIO", None) == "AIO"   # no sysfs


def test_aio_pro_on_snd_hdspm_settings(qapp, tmp_path):
    """An AIO Pro under snd-hdspm: the AIO Pro's level lists are snd-hdspe controls, so they are not
    offered (snd-hdspm's AIO level names may not match the Pro's hardware); the shared ones are."""
    from aa_hdspe_mixer.core.alsa import IFACE_MIXER
    from aa_hdspe_mixer.core.card_settings import settings_for

    class HdspmPro(MockBackend):
        hdspm = {"S/PDIF Input", "S/PDIF Out Optical", "S/PDIF Out Professional", "ADAT internal (AEB/TEB)",
                 "Single Speed WordClock Out", "Input Level", "Output Level", "Phones Level", "XLR Breakout Cable"}
        def setting_choices(self, s):
            ok = any(c.iface == IFACE_MIXER and c.name in self.hdspm for c in s.controls)
            return super().setting_choices(s) if ok else None

    e = Engine(HdspmPro("AIO Pro"), memory_file=str(tmp_path / "mem.json"))
    e.start()
    ids = {s.id for s in e.card_settings()}
    assert {"spdif_in", "spdif_out", "spdif_pro", "adat_in_source", "ss_wordclock_out",
            "optical_in", "optical_out"} <= ids
    assert not ids & {"analog_in_level", "analog_out_level", "phones_level", "tms", "breakout_cable"}
    assert [s.id for s in e.panel_settings_missing()] == ["tms"]


def test_window_geometry_fit_and_files(qapp, tmp_path):
    from PySide6.QtCore import QRect
    from aa_hdspe_mixer.ui import window_geometry as wg
    screens = [QRect(0, 0, 1920, 1050), QRect(1920, 0, 2560, 1410)]
    r, keep = wg.fit({"x": 2100, "y": 100, "width": 1400, "height": 900}, screens, 520, 420)
    assert keep and (r.x(), r.y(), r.width(), r.height()) == (2100, 100, 1400, 900)
    r, keep = wg.fit({"x": 9000, "y": 100, "width": 3000, "height": 300}, screens, 520, 420)
    assert not keep and r.width() == 2560 and r.height() == 420      # off-screen: size only, clamped
    p = str(tmp_path / "window.json")
    assert wg.load(p) is None
    from aa_hdspe_mixer.core.memory import atomic_write_json
    atomic_write_json(p, {"x": 10, "y": 20, "width": 900, "height": 700, "maximized": True})
    assert wg.load(p)["maximized"]

    class W:                                   # stand-in for the QML window
        def __init__(self, maximized, visible=True): self.m, self.v = maximized, visible
        def isVisible(self): return self.v
        def windowStates(self):
            from PySide6.QtCore import Qt
            return Qt.WindowState.WindowMaximized if self.m else Qt.WindowState.WindowNoState
        def x(self): return 50
        def y(self): return 60
        def width(self): return 1000
        def height(self): return 800
    g = wg.capture(W(True), {"x": 10, "y": 20, "width": 900, "height": 700})
    assert g == {"x": 10, "y": 20, "width": 900, "height": 700, "maximized": True,
                 "hidden": False}                                                  # keeps the normal size
    assert wg.capture(W(False)) == {"x": 50, "y": 60, "width": 1000, "height": 800, "maximized": False,
                                    "hidden": False, "zoom": 1.0}
    # Saved at 100 % zoom, restored at 125 %: the same size on screen = fewer logical pixels.
    r, keep = wg.fit({"x": 2500, "y": 100, "width": 1000, "height": 800}, screens, 400, 300, 1.25)
    assert (r.x(), r.y(), r.width(), r.height()) == (2000, 80, 800, 640)
    r, _ = wg.fit({"x": 0, "y": 0, "width": 800, "height": 640, "zoom": 1.25}, screens, 400, 300, 1.0)
    assert (r.width(), r.height()) == (1000, 800)
    assert wg.capture(W(False, visible=False))["hidden"]                           # quit from the tray


def test_start_window_setting(qapp, tmp_path):
    from aa_hdspe_mixer.core.app_settings import AppSettings, start_mode
    from aa_hdspe_mixer.ui.bridge import Bridge
    p = str(tmp_path / "settings.json")
    a = AppSettings(p)
    assert a.start_window == "window"                                    # default: as before
    assert a.set_start_window("last") and not a.set_start_window("sideways")
    assert AppSettings(p).start_window == "last"                         # saved app-wide
    assert start_mode("minimized", None) == "tray"
    assert start_mode("maximized", {"maximized": False}) == "maximized"
    assert start_mode("window", {"maximized": True, "hidden": True}) == "normal"
    assert start_mode("last", {"maximized": True, "hidden": True}) == "tray"
    assert start_mode("last", {"maximized": True, "hidden": False}) == "maximized"
    assert start_mode("last", {"maximized": False, "hidden": False}) == "normal"
    assert start_mode("last", None) == "normal"                          # nothing remembered yet
    e = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e, AppSettings(str(tmp_path / "s2.json")))
    assert [c["value"] for c in b.startWindowChoices] == ["minimized", "maximized", "window", "last"]
    assert b.startWindow == "window" and b.setStartWindow("minimized") and b.startWindow == "minimized"
    assert not b.appSettingsOpen
    b.appSettingsOpen = True
    assert b.appSettingsOpen


def test_side_panel_sections_are_exclusive(qapp, tmp_path):
    from aa_hdspe_mixer.core.app_settings import AppSettings
    from aa_hdspe_mixer.ui.bridge import Bridge
    e = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e, AppSettings(str(tmp_path / "s.json")))
    def shown():
        return (b.settingsPanelOpen, b.clockPanelOpen, b.appSettingsOpen)
    assert shown() == (False, False, False)
    b.settingsPanelOpen = True
    assert shown() == (True, False, False)
    b.clockPanelOpen = True                     # switches, never adds
    assert shown() == (False, True, False)
    b.appSettingsOpen = True
    assert shown() == (False, False, True)
    b.settingsPanelOpen = False                 # closing a section that isn't shown changes nothing
    assert shown() == (False, False, True)
    b.appSettingsOpen = False                   # the lit button again: panel empty
    assert shown() == (False, False, False)


def test_color_mode_setting(qapp, tmp_path):
    from aa_hdspe_mixer.core.app_settings import AppSettings, is_dark
    from aa_hdspe_mixer.ui.bridge import Bridge, system_is_dark
    p = str(tmp_path / "settings.json")
    a = AppSettings(p)
    assert a.color_mode == "system" and a.start_window == "window"       # defaults
    assert a.set_color_mode("light") and not a.set_color_mode("purple")
    a.set_start_window("last")
    b2 = AppSettings(p)
    assert (b2.color_mode, b2.start_window) == ("light", "last")         # both saved, independently
    assert is_dark("dark", False) and not is_dark("light", True)
    assert is_dark("system", True) and not is_dark("system", False)
    e = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e, AppSettings(str(tmp_path / "s2.json")))
    assert [c["value"] for c in b.colorModeChoices] == ["system", "light", "dark"]
    assert b.colorMode == "system" and b.darkMode == system_is_dark()
    assert b.setColorMode("light") and not b.darkMode
    assert b.setColorMode("dark") and b.darkMode


def test_zoom_setting(qapp, tmp_path):
    from aa_hdspe_mixer.core.app_settings import ZOOM_CHOICES, AppSettings, zoom_factor, zoom_label
    from aa_hdspe_mixer.ui.bridge import Bridge
    assert ZOOM_CHOICES == tuple(range(-7, 8))
    assert zoom_factor(0) == 1.0 and zoom_factor(-7) == 0.25 and zoom_factor(7) == 4.0
    assert [zoom_label(v) for v in ZOOM_CHOICES] == ["25%", "30%", "40%", "50%", "60%", "75%", "80%", "100%",
                                                     "125%", "150%", "175%", "200%", "250%", "300%", "400%"]
    p = str(tmp_path / "settings.json")
    a = AppSettings(p)
    assert a.zoom == 0                                                   # default
    assert a.set_zoom(3) and not a.set_zoom(8) and not a.set_zoom(True) and not a.set_zoom("2")
    assert AppSettings(p).zoom == 3
    from aa_hdspe_mixer.core.memory import atomic_write_json
    atomic_write_json(p, {"zoom": "big", "color_mode": "dark"})
    assert AppSettings(p).zoom == 0 and AppSettings(p).color_mode == "dark"     # bad value: default
    e = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e, AppSettings(str(tmp_path / "s2.json")))
    assert len(b.zoomChoices) == 15 and b.zoom == 0 and not b.zoomPending
    assert b.setZoom(-2) and b.zoom == -2 and b.zoomPending                      # applies after a restart
    assert b.setZoom(0) and not b.zoomPending
    fired = []
    b.restartRequested.connect(lambda: fired.append(1))
    b.restartApp()
    assert fired


def test_channel_section(qapp, tmp_path):
    from aa_hdspe_mixer.core.app_settings import AppSettings
    from aa_hdspe_mixer.core.meters import MeterFrame
    from aa_hdspe_mixer.ui.bridge import Bridge
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    b = Bridge(e, AppSettings(str(tmp_path / "s.json")))
    assert not b.channelOpen and b.channel == {} and b.channelKey == ""
    b.addConnections("in:0", ["out:6"], "-3")
    b.addConnections("pb:0", ["out:6"], "0")

    b.settingsPanelOpen = True
    b.showChannel("out:6")                      # a pill clicked: replaces the hammer section
    assert b.channelOpen and not b.settingsPanelOpen and b.channelKey == "out:6"
    ch = b.channel
    assert ch["kind"] == "out" and ch["outLevelDb"] == 0.0 and len(ch["sides"]) == len(e.unit("out:6").channels)
    assert sorted(c["otherKey"] for c in ch["connections"]) == ["in:0", "pb:0"]
    assert {c["otherKey"]: c["gainText"] for c in ch["connections"]}["in:0"] == "-3.0"
    b.setOutputLevel("out:6", "-inf")
    assert b.channel["outLevelDb"] == -1000.0

    # Per-channel peaks of the shown channel only, in dBFS
    n = 64
    outs = [0.0] * n
    outs[6] = 0.5
    b._on_frame(MeterFrame([0.0] * n, [0.0] * n, outs, 0, 0))
    assert b.channelLevels[0] == round(20 * math.log10(0.5), 1)
    assert all(v == -1000.0 for v in b.channelLevels[1:])

    # Follows the channel through a stereo change (its unit's key may change)
    u = e.unit("in:0")
    b.showChannel("in:0")
    stereo = u.stereo
    b.setStereo("in", 0, not stereo)
    assert b.channelOpen and b.channel["hw"] == 0 and b.channel["stereo"] == (not stereo)

    # A split pair can be joined again from its right channel too (and from the pill menu, same flag)
    b.setStereo("in", 0, False)
    right = next(u for u in e.units("in") if u.channels[0].hw == 1)
    b.showChannel(right.key)
    assert b.channel["pairable"] and not b.channel["stereo"]
    b.setStereo("in", b.channel["pair"], True)
    assert b.channel["stereo"] and b.channel["hw"] == 0 and b.channelOpen

    # Clicking the shown pill again closes; a toolbar section replaces it and drops its levels
    b.showChannel(b.channelKey)
    assert not b.channelOpen
    b.showChannel("out:6")
    b.clockPanelOpen = True
    assert not b.channelOpen and b.channelLevels == []

    # A channel that disappears with a rate change (ADAT at double speed) closes the section
    adat7 = next(u for u in e.units("in") if any(c.hw == 18 for c in u.channels))     # ADAT 7: single speed only
    b.showChannel(adat7.key)
    assert b.channelOpen
    be.speed = 1
    e._read_status()
    assert not b.channelOpen
