"""Channel test signal (core/tone.py) and the engine's channel test, on the simulated card."""
import json
import math
import subprocess
import threading
from array import array

import pytest
from PySide6.QtCore import QCoreApplication

from aa_hdspe_mixer.core import tone
from aa_hdspe_mixer.core.backend import MockBackend
from aa_hdspe_mixer.core.engine import Engine
from aa_hdspe_mixer.core.tone import TonePlan, duration, pcm_order, render, schedule


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QCoreApplication([])


def test_pcm_order_matches_the_drivers_channel_maps():
    # ALSA playback order: Analog, AES, S/PDIF, ADAT, Phones, AEB (channel_map_aio_out_* in both drivers)
    assert pcm_order(0, False) == [0, 1, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 6, 7]
    assert pcm_order(0, True)[-4:] == [2, 3, 4, 5] and len(pcm_order(0, True)) == 20
    assert pcm_order(1, False) == [0, 1, 8, 9, 10, 11, 12, 13, 14, 15, 6, 7]
    assert pcm_order(2, False) == [0, 1, 8, 9, 10, 11, 12, 13, 6, 7]


def test_schedule():
    assert schedule(1) == [(0.0, 0.5, 0, 880.0), (1.0, 1.5, 0, 440.0), (2.0, 2.5, 0, 220.0)]
    assert schedule(2)[3:] == [(3.0, 3.5, 1, 880.0), (4.0, 4.5, 1, 440.0), (5.0, 5.5, 1, 220.0)]
    assert duration(1) == 2.5 and duration(2) == 5.5


def test_render_puts_bursts_on_their_channels_only():
    rate, n = 8000, 4
    a = array("f")
    for chunk in render(rate, n, [1, 3]):
        a.frombytes(chunk)
    assert len(a) == round(5.5 * rate) * n
    ch = [a[c::n] for c in range(n)]
    assert max(map(abs, ch[0])) == 0 and max(map(abs, ch[2])) == 0

    def peak(c, t0, t1):
        return max(abs(v) for v in ch[c][round(t0 * rate):round(t1 * rate)])
    level = 10 ** (-6 / 20)
    assert peak(1, 0, 0.5) == pytest.approx(level, rel=1e-3)
    assert peak(1, 0.5, 1.0) == 0 and peak(1, 2.5, 5.5) == 0         # pause; then the right side's turn
    assert peak(3, 0, 3.0) == 0 and peak(3, 5.0, 5.5) == pytest.approx(level, rel=1e-3)
    assert abs(ch[1][0]) < 1e-6                                       # faded in: no click

    # 220 Hz in the third burst: count zero crossings over its 0.5 s
    seg = ch[1][round(2.1 * rate):round(2.4 * rate)]
    crossings = sum(1 for x, y in zip(seg, seg[1:]) if (x < 0) != (y < 0))
    assert crossings == pytest.approx(2 * 220 * 0.3, abs=2)


def test_render_s32_for_alsa():
    a = array("i")
    for chunk in render(8000, 2, [0], "s32"):
        a.frombytes(chunk)
    assert max(a[0::2]) == pytest.approx((2 ** 31 - 1) * 10 ** (-6 / 20), rel=1e-3) and max(a[1::2]) == 0


def test_engine_channel_test(qapp, tmp_path):
    be = MockBackend()
    e = Engine(be, memory_file=str(tmp_path / "mem.json"))
    e.start()
    seen = []
    e.testChanged.connect(lambda: seen.append(e.testing))

    assert not e.test_channel("in:0")                       # playback channels only
    assert e.test_channel("pb:0")                           # AN 1/2 (stereo by default)
    plan = be.tone.plans[-1]
    assert plan.slots == [0, 1] and plan.hw == [0, 1] and plan.rate == 48000 and plan.pcm_channels == 16
    assert e.unit_testing("pb:0") and not e.unit_testing("pb:6")

    phones = next(u for u in e.units("pb") if u.channels[0].hw == 6)
    e.test_channel(phones.key)                              # another channel: replaces the running test
    assert be.tone.plans[-1].slots == [14, 15] and e.unit_testing(phones.key) and not e.unit_testing("pb:0")
    e.test_channel(phones.key)                              # the same one again: stops
    assert e.testing is None

    # The end, reported from the player's thread, clears it; a stale report (stopped run) is ignored
    msgs = []
    e.message.connect(lambda lvl, text: msgs.append((lvl, text)))
    e.test_channel("pb:0")
    run = e._test_run
    e._testEnded.emit(run - 1, "old")
    assert e.unit_testing("pb:0")
    t = threading.Thread(target=lambda: e._testEnded.emit(run, "aplay failed: busy"))
    t.start(); t.join()
    QCoreApplication.processEvents()
    assert e.testing is None and msgs == [("error", "Channel test: aplay failed: busy")]

    # Mono channel on the right: its own ALSA channel; a speed change stops a running test
    e.set_stereo("pb", 0, False)
    right = next(u for u in e.units("pb") if u.channels[0].hw == 1)
    e.test_channel(right.key)
    assert be.tone.plans[-1].slots == [1] and be.tone.level(1) > 0.4 and be.tone.level(0) == 0
    be.speed = 1
    e._read_status()
    assert e.testing is None and be.tone.level(1) == 0
    e.shutdown()


def test_find_pipewire_sink(monkeypatch):
    objs = [
        {"info": {"props": {"media.class": "Audio/Sink", "api.alsa.pcm.card": "1", "node.name": "other",
                            "audio.position": "FL,FR"}}},
        {"info": {"props": {"media.class": "Audio/Source", "api.alsa.pcm.card": "0", "node.name": "in",
                            "audio.position": "AUX0"}}},
        {"info": {"props": {"media.class": "Audio/Sink", "api.alsa.pcm.card": "0", "api.alsa.pcm.device": "0",
                            "node.name": "rme", "audio.position": "AUX0,AUX1,AUX2"}}},
    ]
    monkeypatch.setattr(tone.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(tone.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=json.dumps(objs)))
    assert tone.find_pipewire_sink(0) == ("rme", ["AUX0", "AUX1", "AUX2"])
    assert tone.find_pipewire_sink(1) == ("other", ["FL", "FR"])
    assert tone.find_pipewire_sink(2) is None


def test_player_command(monkeypatch):
    p = tone.SystemPlayer()
    plan = TonePlan(card=3, rate=96000, pcm_channels=12, slots=[10, 11], hw=[6, 7])
    monkeypatch.setattr(tone, "find_pipewire_sink", lambda card: ("rme", [f"AUX{i}" for i in range(12)]))
    cmd, n, fmt = p._command(plan)
    assert cmd[:2] == ["pw-cat", "--playback"] and cmd[cmd.index("--target") + 1] == "rme"
    assert n == 12 and fmt == "f32" and "node.dont-fallback = true" in cmd[cmd.index("-P") + 1]
    assert cmd[cmd.index("--rate") + 1] == "96000"

    monkeypatch.setattr(tone, "find_pipewire_sink", lambda card: ("rme", ["AUX0", "AUX1"]))
    with pytest.raises(RuntimeError, match="Pro Audio"):
        p._command(plan)

    monkeypatch.setattr(tone, "find_pipewire_sink", lambda card: None)
    monkeypatch.setattr(tone.shutil, "which", lambda name: "/usr/bin/" + name)
    cmd, n, fmt = p._command(plan)
    assert cmd[0] == "aplay" and "hw:3,0" in cmd and "S32_LE" in cmd and n == 12 and fmt == "s32"
