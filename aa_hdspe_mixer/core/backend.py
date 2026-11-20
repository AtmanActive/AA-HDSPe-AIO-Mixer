"""Hardware access behind one small interface, plus a simulated card for development and tests."""
from __future__ import annotations

import math
import random
import time

from .alsa import ElemId, Ctl
from .alsaseq import MockSeq, SeqClient
from .card import CardInfo, read_sample_rate
from .card_settings import EnumSetting
from .clock import AlsaClock, MockClock
from .meters import MeterFrame, MeterReader
from .routing import N_DST, N_SRC, UNITY
from .pwvirtual import MockVirtualDevices, VirtualDevices
from .tone import MockPlayer, SystemPlayer, TonePlayer


class Backend:
    info: CardInfo

    def write_cell(self, src: int, dst: int, gain: int): ...
    def read_frame(self) -> MeterFrame: ...
    def read_rate(self) -> int | None: ...
    def close(self): ...

    # Card settings (see card_settings.py). setting_choices() is None when this card/driver lacks it.
    def setting_choices(self, s: EnumSetting) -> tuple[str, ...] | None: ...
    def read_setting(self, s: EnumSetting) -> str: ...
    def write_setting(self, s: EnumSetting, value: str): ...

    # Channel test (tone.py): plays a test signal to a playback channel.
    def tone_player(self) -> TonePlayer: ...

    # Virtual PipeWire devices on the card's channels (pwvirtual.py).
    def virtual_devices(self) -> VirtualDevices: ...

    # MIDI remote control (midi.py): the app's ALSA sequencer client, not open yet.
    def midi_client(self) -> SeqClient: ...

    # Keyboard shortcuts (shortcuts.py): KDE Plasma's global shortcuts, or a stand-in.
    def global_shortcuts(self, parent=None): ...


class AlsaBackend(Backend):
    """Real card. Mixer writes use the HWDEP 'Mixer' control, which both drivers provide."""

    def __init__(self, info: CardInfo):
        self.info = info
        self.ctl = Ctl(info.index)
        self.meters = MeterReader(info.index)
        self._enum_cache: dict[str, tuple[ElemId, tuple[str, ...]] | None] = {}
        self.clock = AlsaClock(self.ctl, info.elements, info.driver)
        self._tone = SystemPlayer()
        self._virtual = VirtualDevices()

    def tone_player(self):
        return self._tone

    def virtual_devices(self):
        return self._virtual

    def midi_client(self):
        return SeqClient()

    def global_shortcuts(self, parent=None):
        from .shortcuts import PlasmaShortcuts
        return PlasmaShortcuts(parent)

    def write_cell(self, src, dst, gain):
        self.ctl.write_mixer_cell(src, dst, gain)

    def read_frame(self):
        return self.meters.read()

    def read_rate(self):
        return read_sample_rate(self.info, self.ctl)

    def _enum(self, s: EnumSetting):
        if s.id not in self._enum_cache:
            found = None
            for c in s.controls:
                if (c.iface, c.name) in self.info.elements:
                    eid = ElemId(c.iface, c.name)
                    # ENUM: item names from the control. BOOLEAN switch: (off, on) from the setting.
                    alias = dict(s.aliases)
                    found = (eid, s.choices if s.switch else
                             tuple(alias.get(x, x) for x in self.ctl.info(eid).items))
                    break
            self._enum_cache[s.id] = found
        return self._enum_cache[s.id]

    def setting_choices(self, s):
        e = self._enum(s)
        return e[1] if e else None

    def read_setting(self, s):
        eid, items = self._enum(s)
        idx = self.ctl.read(eid)[0]
        if s.switch:
            idx = (0 if idx else 1) if s.invert else (1 if idx else 0)
        return items[idx] if 0 <= idx < len(items) else str(idx)

    def write_setting(self, s, value):
        eid, items = self._enum(s)
        idx = items.index(value)
        self.ctl.write(eid, [1 - idx if s.switch and s.invert else idx])

    def close(self):
        self.meters.close()
        self.ctl.close()


class MockBackend(Backend):
    """A simulated AIO: inputs carry wobbling test levels, outputs follow the written matrix."""

    def __init__(self, model: str = "AIO", speed: int = 0, ai4s: bool = True, ao4s: bool = False):
        self.info = CardInfo(index=-1, driver="mock", driver_version="simulated", model=model,
                             name=f"RME {model} (simulated)", serial="mock", pci_revision=212,
                             firmware_build=23 if model == "AIO Pro" else 12)
        self.speed = speed
        self.status2 = (0 if ai4s else 0x100) | (0 if ao4s else 0x80)
        self.g = [[0] * N_SRC for _ in range(N_DST)]
        self.writes = 0
        self.fail_with: OSError | None = None      # tests can inject EBUSY etc.
        self.settings: dict[str, str] = {}          # simulated card settings, by id
        self.clock = MockClock()
        self.tone = MockPlayer()                    # plays nothing; its tones show on the playback meters
        self.virtual = MockVirtualDevices()         # kept in memory; touches neither PipeWire nor systemd
        self.midi = MockSeq()                       # one simulated controller; touches no ALSA
        self.plasma = None                          # MockPlasma, made by global_shortcuts()
        self._t0 = time.monotonic()

    def tone_player(self):
        return self.tone

    def virtual_devices(self):
        return self.virtual

    def midi_client(self):
        return self.midi

    def global_shortcuts(self, parent=None):
        """In memory: the mock card never touches the desktop's shortcuts."""
        from .shortcuts import MockPlasma
        if self.plasma is None:
            self.plasma = MockPlasma(parent)
        return self.plasma

    def write_cell(self, src, dst, gain):
        if self.fail_with:
            raise self.fail_with
        self.g[dst][src] = gain
        self.writes += 1

    def _level(self, i: int, t: float) -> float:
        base = 0.05 + 0.4 * (0.5 + 0.5 * math.sin(t * (0.7 + 0.13 * i) + i))
        return min(1.0, base * (0.8 + 0.4 * random.random()))

    def read_frame(self):
        t = time.monotonic() - self._t0
        inputs = [self._level(i, t) if i < 20 else 0.0 for i in range(64)]
        playback = [max(self._level(i + 7, t * 1.3) * 0.6, self.tone.level(i)) if i < 20 else 0.0
                    for i in range(64)]
        outputs = []
        for d in range(64):
            acc = sum(inputs[s] * self.g[d][s] for s in range(64)) + sum(playback[s] * self.g[d][64 + s] for s in range(64))
            outputs.append(min(1.0, acc / UNITY))
        return MeterFrame(inputs, playback, outputs, self.speed, self.status2)

    def read_rate(self):
        return {0: 48000, 1: 96000, 2: 192000}[self.speed]

    def setting_choices(self, s):
        return s.choices

    def read_setting(self, s):
        return self.settings.get(s.id, s.default)

    def write_setting(self, s, value):
        if self.fail_with:
            raise self.fail_with
        if value not in s.choices:
            raise ValueError(value)
        self.settings[s.id] = value

    def close(self):
        pass
