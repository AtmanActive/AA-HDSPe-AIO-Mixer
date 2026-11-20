"""Mixer matrix and the routing view derived from it.

The hardware matrix (64 destinations x 128 sources, gain 0..65535, 32768 = 0 dB) is the single source
of truth. "Units" (a mono channel or a stereo pair, per the user's per-pair mode) and "connections"
(a source unit feeding a destination unit) are derived from it on demand, so switching a pair between
mono and stereo never loses routing.

Cell rules for a connection S -> D:
  stereo -> stereo : L->L and R->R (cross terms L->R, R->L are cleared)
  mono   -> stereo : S->L and S->R
  stereo -> mono   : L->D and R->D
  mono   -> mono   : S->D
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .channels import IN, OUT, PB, Channel, pair_name

N_DST = 64
N_SRC = 128
UNITY = 32768
MAX_GAIN = 65535
MAX_DB = 20 * math.log10(MAX_GAIN / UNITY)   # +6.02 dB
MIN_DB = 20 * math.log10(1 / UNITY)           # -90.3 dB: the quietest level that is still a connection


def db_to_raw(db: float) -> int:
    if db is None or db == -math.inf or (isinstance(db, float) and math.isnan(db)):
        return 0
    return max(1, min(MAX_GAIN, round(UNITY * 10 ** (db / 20.0))))


def raw_to_db(raw: int) -> float:
    return -math.inf if raw <= 0 else 20.0 * math.log10(raw / UNITY)


def format_db(db: float) -> str:
    if db == -math.inf:
        return "-inf"
    s = f"{db:+.1f}"
    return "0.0" if s in ("+0.0", "-0.0") else s


def parse_db(text: str) -> float | None:
    """'-6', '-6 dB', '+3.5', '0', 'inf'/'-inf'/'off' -> dB value (or -inf). None if invalid.
    An empty box means 0 dB, never -inf: removing a connection stays an explicit action."""
    t = text.strip().lower().replace("db", "").strip()
    if not t:
        return 0.0
    if t in ("-inf", "inf", "-∞", "∞", "off", "mute"):
        return -math.inf
    try:
        v = float(t)
    except ValueError:
        return None
    return min(v, MAX_DB)


@dataclass(frozen=True)
class Unit:
    kind: str
    channels: tuple[Channel, ...]

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.channels[0].hw}"

    @property
    def stereo(self) -> bool:
        return len(self.channels) == 2

    @property
    def hw_name(self) -> str:
        return pair_name(*self.channels) if self.stereo else self.channels[0].hw_name


@dataclass(frozen=True)
class Connection:
    src: Unit
    dst: Unit
    gain: int

    @property
    def key(self) -> str:
        return f"{self.src.key}>{self.dst.key}"


def build_units(kind: str, channels: list[Channel], stereo_pairs: set[int]) -> list[Unit]:
    out, i = [], 0
    while i < len(channels):
        c = channels[i]
        nxt = channels[i + 1] if i + 1 < len(channels) else None
        if c.pair in stereo_pairs and c.hw % 2 == 0 and nxt is not None and nxt.hw == c.hw + 1:
            out.append(Unit(kind, (c, nxt)))
            i += 2
        else:
            out.append(Unit(kind, (c,)))
            i += 1
    return out


def _canonical(src: Unit, dst: Unit) -> list[tuple[int, int]]:
    s = [c.mixer_src for c in src.channels]
    d = [c.hw for c in dst.channels]
    if len(s) == 2 and len(d) == 2:
        return [(s[0], d[0]), (s[1], d[1])]
    return [(a, b) for a in s for b in d]


def _all_cells(src: Unit, dst: Unit) -> list[tuple[int, int]]:
    return [(c.mixer_src, d.hw) for c in src.channels for d in dst.channels]


connection_cells = _all_cells     # public: every (src, dst) mixer cell between two units


class Matrix:
    def __init__(self, cells: list[list[int]] | None = None):
        self.g = cells if cells is not None else [[0] * N_SRC for _ in range(N_DST)]

    def copy(self) -> "Matrix":
        return Matrix([row[:] for row in self.g])

    def get(self, src: int, dst: int) -> int:
        return self.g[dst][src]

    # --- sparse (de)serialisation: [[dst, src, gain], ...] -------------------------------------
    def to_sparse(self) -> list[list[int]]:
        return [[d, s, v] for d, row in enumerate(self.g) for s, v in enumerate(row) if v]

    @classmethod
    def from_sparse(cls, items) -> "Matrix":
        m = cls()
        for d, s, v in items:
            if 0 <= d < N_DST and 0 <= s < N_SRC:
                m.g[d][s] = max(0, min(MAX_GAIN, int(v)))
        return m

    def diff(self, other: "Matrix") -> list[tuple[int, int, int]]:
        """Cell writes (src, dst, gain) that turn self into other."""
        return [(s, d, other.g[d][s]) for d in range(N_DST) for s in range(N_SRC) if self.g[d][s] != other.g[d][s]]

    def all_cells(self) -> list[tuple[int, int, int]]:
        return [(s, d, self.g[d][s]) for d in range(N_DST) for s in range(N_SRC)]

    # --- routing view ---------------------------------------------------------------------------
    def connections(self, sources: list[Unit], dests: list[Unit]) -> list[Connection]:
        out = []
        for dst in dests:
            for src in sources:
                cells = _all_cells(src, dst)
                if not any(self.g[d][s] for s, d in cells):
                    continue
                canon = [self.g[d][s] for s, d in _canonical(src, dst)]
                gain = max(canon) if any(canon) else max(self.g[d][s] for s, d in cells)
                out.append(Connection(src, dst, gain))
        return out

    def set_connection(self, src: Unit, dst: Unit, gain: int) -> list[tuple[int, int, int]]:
        """Set S -> D to gain (0 removes it). Returns the cell writes needed."""
        canon = set(_canonical(src, dst))
        changes = []
        for s, d in _all_cells(src, dst):
            v = gain if (s, d) in canon else 0
            if self.g[d][s] != v:
                self.g[d][s] = v
                changes.append((s, d, v))
        return changes

    def clear(self) -> list[tuple[int, int, int]]:
        changes = [(s, d, 0) for d in range(N_DST) for s in range(N_SRC) if self.g[d][s]]
        for s, d, _ in changes:
            self.g[d][s] = 0
        return changes


__all__ = ["IN", "PB", "OUT", "Unit", "Connection", "Matrix", "build_units", "db_to_raw", "raw_to_db",
           "format_db", "parse_db", "connection_cells", "UNITY", "MAX_GAIN", "MAX_DB", "MIN_DB"]
