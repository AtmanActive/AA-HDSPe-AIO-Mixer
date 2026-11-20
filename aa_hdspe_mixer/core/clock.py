"""Clock: sample rate, clock source (internal or a preferred AutoSync reference), input clock status and
sample-rate fine tuning, for both drivers.

The two drivers expose this very differently:

                       snd-hdspm (MIXER iface)          snd-hdspe (CARD iface)
  internal / autosync  'System Clock Mode'              'Clock Mode'
                       (Master, AutoSync)               (AutoSync, Master)
  preferred source     'Preferred Sync Reference'       'Preferred AutoSync Reference'
                       (Word Clock, ADAT, AES, SPDIF,   (WordClk, AES, S/PDIF, ADAT, TCO, SyncIn)
                        Sync In [, TCO])
  internal rate        'Internal Clock' (32 kHz..)      'Internal Frequency' (32 KHz..)
  source status        '<X> SyncCheck' + '<X> Frequency' 'AutoSync Status' + 'AutoSync Frequency',
                       per source (X = WC, AES, ...)     arrays in preferred-source order
  current reference    -                                'Current AutoSync Reference'
  fine tuning          'System Sample Rate' (Hz)        HWDEP 'DDS': rate = FCONST * speed / DDS

None of this is stored in presets or in the card's memory: the card (and whatever program plays audio)
owns the clock. File > Export Memory saves the source and rates, and Import can set them again.
snd-hdspm makes the AIO disappear at double/quad speed (seen on the real card), so under that
driver only single-speed rates are offered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .alsa import IFACE_CARD, IFACE_HWDEP, IFACE_MIXER, ElemId

# Clock sources shown in the app, in this order. "internal" = the card is the clock master.
SOURCES = ("internal", "adat", "aes", "spdif", "tco", "syncin")
SOURCE_LABELS = {"internal": "Internal", "adat": "ADAT", "aes": "AES", "spdif": "S/PDIF", "tco": "TCO",
                 "syncin": "Sync In", "word": "Word Clock"}
_NORMALISED = {"wordclock": "word", "wordclk": "word", "wc": "word", "adat": "adat", "aes": "aes",
               "spdif": "spdif", "syncin": "syncin", "tco": "tco", "intern": "internal", "internal": "internal"}
HDSPM_STATUS_PREFIX = {"word": "WC", "adat": "ADAT", "aes": "AES", "spdif": "SPDIF", "syncin": "SYNC IN",
                       "tco": "TCO"}
SINGLE_SPEED_MAX = 51750          # Hz; above this a rate is double or quad speed
DDS_FCONST = 104857600000000      # snd-hdspe PLL constant for the AIO and AIO Pro
TUNE_SPAN = 0.05                  # fine tuning: nominal rate +/- 5 %


def source_key(item: str) -> str | None:
    return _NORMALISED.get(re.sub(r"[^a-z]", "", item.lower()))


def rate_hz(item: str) -> int | None:
    """'48 kHz' / '44.1 KHz' -> 48000 / 44100; 'No Lock' / '' -> None."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*k", item, re.I)
    return round(float(m.group(1)) * 1000) if m else None


def speed_factor(rate: int) -> int:
    return 4 if rate > 2 * SINGLE_SPEED_MAX else 2 if rate > SINGLE_SPEED_MAX else 1


@dataclass
class SourceStatus:
    sync: str = "N/A"                 # "No Lock" | "Lock" | "Sync" | "N/A"
    rate: int | None = None           # Hz, when locked


@dataclass
class ClockState:
    master: bool = True                                   # True = internal clock
    preferred: str | None = None                          # source key for AutoSync
    sources: tuple[str, ...] = ()                         # selectable AutoSync sources on this card
    internal_rate: int | None = None                      # Hz
    rates: tuple[int, ...] = ()                           # internal rates offered
    status: dict[str, SourceStatus] = field(default_factory=dict)
    current: str | None = None                            # source the card is locked to (snd-hdspe)
    tuned_rate: float | None = None                       # Hz, effective internal rate incl. fine tuning
    tune_min: int = 27000
    tune_max: int = 207000

    @property
    def selected(self) -> str:
        return "internal" if self.master else (self.preferred or "")


class AlsaClock:
    """Clock access through the card's ALSA controls, for snd-hdspm or snd-hdspe."""

    def __init__(self, ctl, elements: set, driver: str):
        self.ctl = ctl
        self.el = elements
        self.hdspm = driver == "snd_hdspm" or (IFACE_MIXER, "System Clock Mode") in elements
        i = IFACE_MIXER if self.hdspm else IFACE_CARD
        self.e_mode = ElemId(i, "System Clock Mode" if self.hdspm else "Clock Mode")
        self.e_pref = ElemId(i, "Preferred Sync Reference" if self.hdspm else "Preferred AutoSync Reference")
        self.e_rate = ElemId(i, "Internal Clock" if self.hdspm else "Internal Frequency")
        self._items: dict[ElemId, tuple[str, ...]] = {}

    def available(self) -> bool:
        return all((e.iface, e.name) in self.el for e in (self.e_mode, self.e_pref, self.e_rate))

    def _enum_items(self, eid: ElemId) -> tuple[str, ...]:
        if eid not in self._items:
            self._items[eid] = self.ctl.info(eid).items
        return self._items[eid]

    def _read_enum(self, eid: ElemId) -> str:
        items = self._enum_items(eid)
        idx = self.ctl.read(eid)[0]
        return items[idx] if 0 <= idx < len(items) else ""

    def _write_enum(self, eid: ElemId, pick) -> None:
        items = self._enum_items(eid)
        idx = next(i for i, it in enumerate(items) if pick(it))
        self.ctl.write(eid, [idx])

    def read(self, speed: int) -> ClockState:
        st = ClockState()
        st.master = self._read_enum(self.e_mode).lower().startswith("master")
        pref_items = self._enum_items(self.e_pref)
        st.sources = tuple(k for k in (source_key(x) for x in pref_items) if k)
        st.preferred = source_key(self._read_enum(self.e_pref))
        rates = [rate_hz(x) for x in self._enum_items(self.e_rate)]
        st.internal_rate = rate_hz(self._read_enum(self.e_rate))
        offered = [r for r in rates if r]
        if self.hdspm:
            offered = [r for r in offered if r <= SINGLE_SPEED_MAX]
        st.rates = tuple(offered)
        if self.hdspm:
            for key, prefix in HDSPM_STATUS_PREFIX.items():
                e_sync, e_freq = ElemId(IFACE_MIXER, f"{prefix} SyncCheck"), ElemId(IFACE_MIXER, f"{prefix} Frequency")
                if (e_sync.iface, e_sync.name) in self.el:
                    st.status[key] = SourceStatus(self._read_enum(e_sync),
                                                  rate_hz(self._read_enum(e_freq))
                                                  if (e_freq.iface, e_freq.name) in self.el else None)
            e_sys = ElemId(IFACE_MIXER, "System Sample Rate")
            if (e_sys.iface, e_sys.name) in self.el:
                st.tuned_rate = float(self.ctl.read(e_sys)[0])
                st.tune_min, st.tune_max = 27000, SINGLE_SPEED_MAX       # single speed only
        else:
            e_sync, e_freq = ElemId(IFACE_CARD, "AutoSync Status"), ElemId(IFACE_CARD, "AutoSync Frequency")
            keys = [source_key(x) for x in pref_items]
            if (e_sync.iface, e_sync.name) in self.el:
                s_items, f_items = self._enum_items(e_sync), self._enum_items(e_freq)
                syncs, freqs = self.ctl.read(e_sync), self.ctl.read(e_freq)
                for n, key in enumerate(keys):
                    if key and n < len(syncs):
                        f = f_items[freqs[n]] if n < len(freqs) and 0 <= freqs[n] < len(f_items) else ""
                        st.status[key] = SourceStatus(s_items[syncs[n]] if 0 <= syncs[n] < len(s_items) else "N/A",
                                                      rate_hz(f))
            e_cur = ElemId(IFACE_CARD, "Current AutoSync Reference")
            if (e_cur.iface, e_cur.name) in self.el:
                st.current = source_key(self._read_enum(e_cur))
            e_dds = ElemId(IFACE_HWDEP, "DDS")
            if (e_dds.iface, e_dds.name) in self.el:
                dds = self.ctl.read(e_dds)[0]
                if dds:
                    st.tuned_rate = DDS_FCONST * (1 << speed) / dds
                st.tune_min, st.tune_max = 27000 * (1 << speed), SINGLE_SPEED_MAX * (1 << speed)
        return st

    def write_master(self, master: bool):
        self._write_enum(self.e_mode, lambda it: it.lower().startswith("master" if master else "auto"))

    def write_preferred(self, key: str):
        self._write_enum(self.e_pref, lambda it: source_key(it) == key)

    def write_internal_rate(self, hz: int):
        self._write_enum(self.e_rate, lambda it: rate_hz(it) == hz)

    def write_tuned_rate(self, hz: float, speed: int):
        if self.hdspm:
            self.ctl.write(ElemId(IFACE_MIXER, "System Sample Rate"), [round(hz)])
        else:
            single = hz / (1 << speed)
            self.ctl.write(ElemId(IFACE_HWDEP, "DDS"), [round(DDS_FCONST / single)])


class MockClock:
    """Simulated clock for --mock and tests."""

    def __init__(self):
        self.master = True
        self.preferred = "aes"
        self.internal_rate = 48000
        self.tuned = 48000.0
        self.status = {"adat": SourceStatus("Sync", 48000), "aes": SourceStatus("Lock", 48000),
                       "spdif": SourceStatus("No Lock", None), "tco": SourceStatus("N/A", None),
                       "syncin": SourceStatus("No Lock", None), "word": SourceStatus("No Lock", None)}
        self.fail_with: OSError | None = None

    def available(self) -> bool:
        return True

    def read(self, speed: int) -> ClockState:
        rates = (32000, 44100, 48000, 64000, 88200, 96000, 128000, 176400, 192000)
        current = None if self.master else (self.preferred if self.status[self.preferred].sync in ("Lock", "Sync")
                                            else "internal")
        f = speed_factor(self.internal_rate)
        return ClockState(master=self.master, preferred=self.preferred,
                          sources=("word", "aes", "spdif", "adat", "tco", "syncin"),
                          internal_rate=self.internal_rate, rates=rates, status=dict(self.status),
                          current=current, tuned_rate=self.tuned,
                          tune_min=27000 * f, tune_max=SINGLE_SPEED_MAX * f)

    def _check(self):
        if self.fail_with:
            raise self.fail_with

    def write_master(self, master: bool):
        self._check()
        self.master = master

    def write_preferred(self, key: str):
        self._check()
        self.preferred = key

    def write_internal_rate(self, hz: int):
        self._check()
        self.internal_rate = hz
        self.tuned = float(hz)

    def write_tuned_rate(self, hz: float, speed: int):
        self._check()
        self.tuned = float(hz)
