"""Channel test for playback channels: three sine bursts (880, 440, then 220 Hz) at -6 dBFS, 500 ms each with
500 ms between them. On a stereo pair the left channel goes first, then after 500 ms the right one.

The sound goes to the card's playback channel, so it is heard wherever the mixer routes that channel.
- Through PipeWire when the card is one of its sinks (the usual desktop case): pw-cat plays to that sink only
  (never falling back to another device), with the sink's own channel layout, so channel n of the stream is
  ALSA channel n. The sink's volume applies.
- Otherwise straight to the ALSA device (aplay -D hw:N), which fails while something else holds the card.

ALSA's playback channel order (both drivers) is not the mixer's hardware order: Analog, AES, S/PDIF, ADAT,
Phones, then AEB (only with the AO4S board). pcm_order() maps one to the other.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import threading
import time
from array import array
from dataclasses import dataclass, field
from typing import Callable

from .channels import PB, layout

FREQS = (880.0, 440.0, 220.0)
LEVEL_DB = -6.0
BURST_S = 0.5
GAP_S = 0.5             # between bursts, and between the left and right channel
FADE_S = 0.005          # raised-cosine fade in and out, so the bursts don't click
CHUNK_S = 0.1           # audio is written in pieces this long, so a stop takes effect quickly

PCM_GROUP_ORDER = ("AN", "AES", "SPDIF", "ADAT", "PH", "AEB")


def pcm_order(speed: int, ao4s: bool, kind: str = PB, ai4s: bool = False) -> list[int]:
    """Hardware channel (as in channels.py) of each ALSA PCM channel, in PCM order: playback (PB or OUT, the
    same channels) or capture (IN: Analog, AES, S/PDIF, ADAT, then AEB with the AI4S board)."""
    chans = layout(kind, speed, ai4s=ai4s, ao4s=ao4s)
    return [c.hw for c in sorted(chans, key=lambda c: (PCM_GROUP_ORDER.index(c.group), c.hw))]


def schedule(sides: int) -> list[tuple[float, float, int, float]]:
    """Bursts as (start s, end s, side, frequency)."""
    out, t = [], 0.0
    for side in range(sides):
        if side:
            t += GAP_S
        for i, f in enumerate(FREQS):
            if i:
                t += GAP_S
            out.append((t, t + BURST_S, side, f))
            t += BURST_S
    return out


def duration(sides: int) -> float:
    return schedule(sides)[-1][1]


def _burst(rate: int, freq: float) -> list[float]:
    n = round(BURST_S * rate)
    amp = 10 ** (LEVEL_DB / 20)
    fade = max(1, round(FADE_S * rate))
    w = 2 * math.pi * freq / rate
    out = []
    for i in range(n):
        g = 1.0
        edge = min(i, n - 1 - i)
        if edge < fade:
            g = 0.5 - 0.5 * math.cos(math.pi * edge / fade)
        out.append(amp * g * math.sin(w * i))
    return out


def render(rate: int, channels: int, slots: list[int], fmt: str = "f32"):
    """The test as interleaved raw audio, in chunks of CHUNK_S: float32 ("f32") or int32 ("s32"), native
    byte order. slots: the stream channel of each side."""
    total = round(duration(len(slots)) * rate)
    bursts = [(round(a * rate), round(b * rate), slots[side], _burst(rate, f)) for a, b, side, f in schedule(len(slots))]
    step = max(1, round(CHUNK_S * rate))
    code, scale = ("f", 1.0) if fmt == "f32" else ("i", 2 ** 31 - 1)
    for start in range(0, total, step):
        end = min(total, start + step)
        buf = array(code, bytes(4 * (end - start) * channels))
        for a, b, ch, samples in bursts:
            lo, hi = max(a, start), min(b, end)
            if lo >= hi:
                continue
            part = samples[lo - a:hi - a]
            buf[(lo - start) * channels + ch:(hi - start) * channels:channels] = \
                array(code, part if code == "f" else [round(v * scale) for v in part])
        yield buf.tobytes()


@dataclass
class TonePlan:
    card: int               # ALSA card index
    rate: int               # the card's sample rate
    pcm_channels: int       # ALSA playback channels at this speed and with these boards
    slots: list[int]        # ALSA channel of each side (left, right)
    hw: list[int] = field(default_factory=list)   # the same channels as hardware playback channels


class TonePlayer:
    """Plays a TonePlan in the background. done(error) is called from the player's thread when it ends;
    error is "" when it played to the end or was stopped."""
    def start(self, plan: TonePlan, done: Callable[[str], None]): ...
    def stop(self): ...


def find_pipewire_sink(card: int) -> tuple[str, list[str]] | None:
    """(node name, channel positions) of the PipeWire sink for an ALSA card's PCM device 0, if any."""
    if not shutil.which("pw-dump"):
        return None
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout
        objs = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    for o in objs:
        p = (o.get("info") or {}).get("props") or {}
        if p.get("media.class") != "Audio/Sink":
            continue
        if str(p.get("api.alsa.pcm.card", p.get("alsa.card", ""))) != str(card):
            continue
        if str(p.get("api.alsa.pcm.device", p.get("alsa.device", "0"))) != "0":
            continue
        pos = p.get("audio.position")
        if isinstance(pos, str):
            pos = [s.strip() for s in pos.split(",") if s.strip()]
        if not pos:
            pos = [f"AUX{i}" for i in range(int(p.get("audio.channels") or 0))]
        if p.get("node.name") and pos:
            return p["node.name"], list(pos)
    return None


class SystemPlayer(TonePlayer):
    """The real thing: PipeWire's pw-cat when the card is a PipeWire sink, else ALSA's aplay."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, plan, done):
        self.stop()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(plan, done, self._stop), daemon=True,
                                        name="channel-test")
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.kill()

    def _command(self, plan: TonePlan) -> tuple[list[str], int, str]:
        sink = find_pipewire_sink(plan.card)
        if sink:
            name, positions = sink
            if max(plan.slots) >= len(positions):
                raise RuntimeError(f"the PipeWire sink has {len(positions)} channels; this channel isn't one of "
                                   "them (try the Pro Audio profile)")
            props = ('{ node.dont-fallback = true node.dont-reconnect = true '
                     'media.name = "Channel test" node.description = "AA-HDSPe-AIO-Mixer channel test" }')
            return (["pw-cat", "--playback", "--raw", "--target", name, "--rate", str(plan.rate),
                     "--channels", str(len(positions)), "--channel-map", ",".join(positions), "--format", "f32",
                     "-P", props, "-"], len(positions), "f32")
        if not shutil.which("aplay"):
            raise RuntimeError("neither PipeWire (pw-cat) nor ALSA's aplay is available")
        return (["aplay", "-q", "-D", f"hw:{plan.card},0", "-t", "raw", "-f", "S32_LE", "-r", str(plan.rate),
                 "-c", str(plan.pcm_channels), "-"], plan.pcm_channels, "s32")

    def _run(self, plan: TonePlan, done, stop: threading.Event):
        err = ""
        proc = None
        try:
            cmd, channels, fmt = self._command(plan)
            with self._lock:
                if stop.is_set():
                    return
                proc = self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                                     stderr=subprocess.PIPE)
            for chunk in render(plan.rate, channels, plan.slots, fmt):
                if stop.is_set():
                    break
                proc.stdin.write(chunk)
            proc.stdin.close()
            if stop.is_set():
                proc.kill()
            rc = proc.wait(timeout=duration(len(plan.slots)) + 10)
            if rc and not stop.is_set():
                msg = (proc.stderr.read() or b"").decode(errors="replace").strip().splitlines()
                err = f"{cmd[0]} failed: " + (msg[-1] if msg else f"exit code {rc}")
        except BrokenPipeError:
            if not stop.is_set():
                msg = (proc.stderr.read() or b"").decode(errors="replace").strip().splitlines() if proc else []
                err = f"{proc.args[0] if proc else 'player'} stopped: " + (msg[-1] if msg else "broken pipe")
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            err = str(e)
        finally:
            if proc and proc.poll() is None:
                proc.kill()
            with self._lock:
                if self._proc is proc:
                    self._proc = None
            done(err)


class MockPlayer(TonePlayer):
    """For the simulated card: plays nothing, but knows which hardware channel would carry a tone when,
    so the simulated meters can show it. Ends after the test's real duration."""

    def __init__(self):
        self.plans: list[TonePlan] = []
        self._t0 = 0.0
        self._timer: threading.Timer | None = None

    def start(self, plan, done):
        self.stop()
        self.plans.append(plan)
        self._t0 = time.monotonic()
        self._timer = threading.Timer(duration(len(plan.slots)), lambda: (self._clear(), done("")))
        self._timer.daemon = True
        self._timer.start()

    def _clear(self):
        self._timer = None

    def stop(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def level(self, hw: int) -> float:
        """Peak the simulated card would meter on a hardware playback channel now."""
        if not self._timer or not self.plans:
            return 0.0
        plan, t = self.plans[-1], time.monotonic() - self._t0
        for a, b, side, _f in schedule(len(plan.slots)):
            if a <= t < b and side < len(plan.hw) and plan.hw[side] == hw:
                return 10 ** (LEVEL_DB / 20)
        return 0.0
