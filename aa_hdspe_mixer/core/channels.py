"""Hardware channel layout of the HDSPe AIO and AIO Pro (identical on both cards).

Hardware (mixer/DMA) indices, measured on both cards (ADAT through an optical loopback):
  inputs : 0-1 Analog, 2-5 AEB (AI4S board only), 8-9 AES, 10-11 S/PDIF, 12.. ADAT
  outputs: 0-1 Analog, 2-5 AEB (AO4S board only), 6-7 Phones, 8-9 AES, 10-11 S/PDIF, 12.. ADAT
  ADAT   : single speed 12-19, double speed 12-15, quad speed 12-13 (contiguous on both cards)
Playback channels mirror the outputs. Mixer source index = input hw index, or 64 + playback hw index.

Every group starts on an even index, so a stereo pair is (2k, 2k+1) and its pair id is hw // 2.
"""
from __future__ import annotations

from dataclasses import dataclass

IN, PB, OUT = "in", "pb", "out"
KINDS = (IN, PB, OUT)
KIND_LABEL = {IN: "Input", PB: "Playback", OUT: "Output"}
PLAYBACK_OFFSET = 64
ADAT_COUNT = {0: 8, 1: 4, 2: 2}


@dataclass(frozen=True)
class Channel:
    kind: str
    hw: int           # hardware index 0-63 within its kind
    hw_name: str      # e.g. "AN 1", "AES L", "ADAT 3"
    group: str        # e.g. "AN", "AES", "ADAT"
    side: str         # stereo-pair label part, e.g. "1"/"2" or "L"/"R"

    @property
    def pair(self) -> int:
        return self.hw // 2

    @property
    def mixer_src(self) -> int:
        return self.hw + (PLAYBACK_OFFSET if self.kind == PB else 0)


def _group(kind, group, start, sides):
    return [Channel(kind, start + i, f"{group} {s}", group, s) for i, s in enumerate(sides)]


def layout(kind: str, speed: int = 0, ai4s: bool = False, ao4s: bool = False) -> list[Channel]:
    """Channels of one kind present at the given speed, in display order."""
    adat = [str(i + 1) for i in range(ADAT_COUNT.get(speed, 8))]
    if kind == IN:
        chans = _group(IN, "AN", 0, "12")
        if ai4s:
            chans += _group(IN, "AEB", 2, "1234")
    else:
        chans = _group(kind, "AN", 0, "12")
        if ao4s:
            chans += _group(kind, "AEB", 2, "1234")
        chans += _group(kind, "PH", 6, "LR")
    chans += _group(kind, "AES", 8, "LR") + _group(kind, "SPDIF", 10, "LR") + _group(kind, "ADAT", 12, adat)
    return chans


def pair_name(a: Channel, b: Channel) -> str:
    return f"{a.group} {a.side}/{b.side}"
