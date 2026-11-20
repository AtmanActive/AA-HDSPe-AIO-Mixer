"""Peak/RMS meters through the hwdep ioctl shared by snd-hdspm and snd-hdspe.

Measured on both cards, and shaping this module:
- peaks are linear, full scale 0x7fffffff, low byte = flags (masked off here);
- index n (0-31) = peak since the previous read (reset on read), n+32 = live value;
- so there must be ONE reader per card, polling at a steady rate (see Engine);
- RMS (64-bit) ~ mean square * 2^63; on the AIO Pro it read a constant 8191, so treat it as optional.
"""
from __future__ import annotations

import fcntl
import math
import os
import struct
from dataclasses import dataclass

IOCTL_GET_PEAK_RMS = 0x89084842
STRUCT_SIZE = 2312
FULL_SCALE = 0x7FFFFFFF
SPEED_NAMES = ("Single Speed", "Double Speed", "Quad Speed")


@dataclass
class MeterFrame:
    inputs: list[float]      # 64 peak values since the last read, linear 0..1
    playback: list[float]
    outputs: list[float]
    speed: int               # 0 single, 1 double, 2 quad
    status2: int

    @property
    def ai4s_present(self) -> bool:   # active-low bit
        return not (self.status2 & 0x100)

    @property
    def ao4s_present(self) -> bool:
        return not (self.status2 & 0x80)


def peak_to_linear(raw: int) -> float:
    return (raw & 0xFFFFFF00) / FULL_SCALE


def linear_to_db(v: float) -> float:
    return -math.inf if v <= 0 else 20.0 * math.log10(v)


def parse(buf: bytes | bytearray) -> MeterFrame:
    peaks = struct.unpack_from("<192I", buf, 0)
    lin = [peak_to_linear(p) for p in peaks]
    return MeterFrame(lin[0:64], lin[64:128], lin[128:192], buf[2304], struct.unpack_from("<i", buf, 2308)[0])


class MeterReader:
    """Keeps /dev/snd/hwC<N>D0 open and reads one frame per call."""

    def __init__(self, card: int):
        self.path = f"/dev/snd/hwC{card}D0"
        self._fd: int | None = None
        self._buf = bytearray(STRUCT_SIZE)

    def read(self) -> MeterFrame:
        if self._fd is None:
            self._fd = os.open(self.path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            fcntl.ioctl(self._fd, IOCTL_GET_PEAK_RMS, self._buf, True)
        except OSError:
            self.close()
            raise
        return parse(self._buf)

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
