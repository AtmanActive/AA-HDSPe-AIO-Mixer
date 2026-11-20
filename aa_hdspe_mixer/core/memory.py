"""Everything the app remembers about one card: names, stereo modes, current routing, presets.

Files are JSON:
  memory  : {"format": "aa-hdspe-aio-mixer/memory", "version": 1, ...}  (auto-saved, export/import)
  preset  : {"format": "aa-hdspe-aio-mixer/preset", "version": 1, "name": ..., "matrix": [...], "stereo": {...}}
A preset is a complete snapshot (routing, levels, mutes, mono/stereo modes, loopback, channel names, card
settings); recall can restore any combination of names / destinations / levels / mutes / card settings (see
Engine.apply_preset).

Card settings (card_settings.py) are stored as {setting id: choice name}, e.g. {"spdif_in": "Optical"}.

Mutes are kept apart from levels. A muted channel or connection sends -inf to the card, but the app keeps
the connection and its level, so unmuting restores it exactly:
  mutes       : {kind: {hw, ...}}   muted channels (inputs, playback, outputs), per hardware channel
  muted_cells : {(src, dst), ...}   muted connections, as the matrix cells they consist of. Cells survive a
                                    mono/stereo switch, so a muted connection stays muted. Only cells that
                                    are routed (non-zero in the matrix) are kept: removing a connection
                                    drops its mute.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field

from .channels import IN, KINDS, OUT, PB
from .routing import MAX_DB, Matrix

FORMAT_MEMORY = "aa-hdspe-aio-mixer/memory"
FORMAT_PRESET = "aa-hdspe-aio-mixer/preset"
VERSION = 1
MAX_PRESETS = 64


def no_mutes() -> dict[str, set[int]]:
    return {k: set() for k in KINDS}


def mutes_to_json(mutes: dict[str, set[int]]) -> dict[str, list[int]]:
    return {k: sorted(v) for k, v in mutes.items() if v}


def mutes_from_json(d) -> dict[str, set[int]]:
    out = no_mutes()
    if isinstance(d, dict):
        for k in KINDS:
            out[k] = {int(x) for x in (d.get(k) or []) if 0 <= int(x) < 64}
    return out


def cells_to_json(cells: set[tuple[int, int]]) -> list[list[int]]:
    return [[d, s] for s, d in sorted(cells, key=lambda c: (c[1], c[0]))]     # [dst, src] like the matrix


def cells_from_json(items) -> set[tuple[int, int]]:
    out = set()
    for it in items or []:
        d, s = int(it[0]), int(it[1])
        if 0 <= d < 64 and 0 <= s < 128:
            out.add((s, d))
    return out


def default_stereo() -> dict[str, set[int]]:
    # All pairs stereo by default, like TotalMix.
    return {k: set(range(32)) for k in KINDS}


@dataclass
class Preset:
    """A complete snapshot: routing, levels, modes and channel names. Recall can pick parts of it."""
    name: str
    matrix: Matrix
    stereo: dict[str, set[int]]
    out_levels: dict[int, float] = field(default_factory=dict)
    names: dict[str, dict[int, str]] | None = None    # None = preset predates stored names
    loopback: set[int] = field(default_factory=set)
    mutes: dict[str, set[int]] = field(default_factory=no_mutes)
    muted_cells: set[tuple[int, int]] = field(default_factory=set)
    card_settings: dict[str, str] = field(default_factory=dict)     # absent in older presets: unchanged

    def to_dict(self) -> dict:
        d = {"name": self.name, "matrix": self.matrix.to_sparse(),
             "stereo": {k: sorted(v) for k, v in self.stereo.items()},
             "out_levels": levels_to_json(self.out_levels),
             "loopback": sorted(self.loopback),
             "mutes": mutes_to_json(self.mutes),
             "muted_cells": cells_to_json(self.muted_cells),
             "card_settings": dict(sorted(self.card_settings.items()))}
        if self.names is not None:
            d["names"] = names_to_json(self.names)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Preset":
        return cls(name=str(d.get("name", "Preset")), matrix=Matrix.from_sparse(d.get("matrix", [])),
                   stereo=_stereo_from(d.get("stereo")), out_levels=levels_from_json(d.get("out_levels")),
                   names=names_from_json(d["names"]) if isinstance(d.get("names"), dict) else None,
                   loopback={int(x) for x in d.get("loopback", []) if 0 <= int(x) < 64},
                   mutes=mutes_from_json(d.get("mutes")),          # absent in older presets: nothing muted
                   muted_cells=cells_from_json(d.get("muted_cells")),
                   card_settings=_card_settings_from(d.get("card_settings")))


def names_to_json(names: dict[str, dict[int, str]]) -> dict[str, dict[str, str]]:
    return {k: {str(hw): n for hw, n in sorted(v.items())} for k, v in names.items()}


def names_from_json(d: dict) -> dict[str, dict[int, str]]:
    out = {k: {} for k in KINDS}
    for k in KINDS:
        for hw, n in (d.get(k) or {}).items():
            if str(n).strip():
                out[k][int(hw)] = str(n).strip()
    return out


def levels_to_json(levels: dict[int, float]) -> dict[str, float | None]:
    """{hw: dB}; -inf is stored as null (JSON has no infinity). 0 dB entries are omitted."""
    return {str(hw): (None if db == -math.inf else round(db, 2)) for hw, db in sorted(levels.items()) if db != 0}


def levels_from_json(d) -> dict[int, float]:
    out = {}
    if isinstance(d, dict):
        for hw, db in d.items():
            hw = int(hw)
            if 0 <= hw < 64:
                v = -math.inf if db is None else min(MAX_DB, float(db))
                if v != 0:
                    out[hw] = v
    return out


def _card_settings_from(d) -> dict[str, str]:
    return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}


def _stereo_from(d) -> dict[str, set[int]]:
    st = default_stereo()
    if isinstance(d, dict):
        for k in KINDS:
            if k in d:
                st[k] = {int(p) for p in d[k] if 0 <= int(p) < 32}
    return st


@dataclass
class Memory:
    card_key: str = ""
    names: dict[str, dict[int, str]] = field(default_factory=lambda: {k: {} for k in KINDS})
    stereo: dict[str, set[int]] = field(default_factory=default_stereo)
    loopback: set[int] = field(default_factory=set)   # outputs with loopback on (kept; no driver support yet)
    out_levels: dict[int, float] = field(default_factory=dict)   # output hw -> level dB (absent = 0 dB)
    mutes: dict[str, set[int]] = field(default_factory=no_mutes)   # muted channels per kind (see top)
    muted_cells: set[tuple[int, int]] = field(default_factory=set)  # muted connections' cells (src, dst)
    card_settings: dict[str, str] = field(default_factory=dict)     # setting id -> choice (card_settings.py)
    conn_seq: dict[str, int] = field(default_factory=dict)       # connection key -> creation order (wire colour)
    next_seq: int = 0
    matrix: Matrix = field(default_factory=Matrix)
    presets: list[Preset | None] = field(default_factory=lambda: [None] * MAX_PRESETS)
    settings: dict = field(default_factory=dict)

    # --- names ----------------------------------------------------------------------------------
    def name_of(self, kind: str, hw: int) -> str:
        return self.names.get(kind, {}).get(hw, "")

    def set_name(self, kind: str, hw: int, name: str):
        name = name.strip()
        if name:
            self.names.setdefault(kind, {})[hw] = name
        else:
            self.names.setdefault(kind, {}).pop(hw, None)

    # --- presets --------------------------------------------------------------------------------
    def snapshot(self, name: str, exclude_settings=()) -> Preset:
        """Everything: routing, connection and output levels, mutes, mono/stereo modes, loopback, channel names,
        card settings."""
        return Preset(name=name, matrix=self.matrix.copy(), stereo={k: set(v) for k, v in self.stereo.items()},
                      out_levels=dict(self.out_levels), names={k: dict(v) for k, v in self.names.items()},
                      loopback=set(self.loopback), mutes={k: set(v) for k, v in self.mutes.items()},
                      muted_cells=set(self.muted_cells),
                      card_settings={k: v for k, v in self.card_settings.items() if k not in exclude_settings})

    def store_preset(self, slot: int, name: str, exclude_settings=()) -> Preset:
        p = self.snapshot(name.strip() or f"Preset {slot + 1}", exclude_settings)
        self.presets[slot] = p
        return p

    # --- (de)serialisation ------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "format": FORMAT_MEMORY, "version": VERSION, "card": self.card_key,
            "names": names_to_json(self.names),
            "stereo": {k: sorted(v) for k, v in self.stereo.items()},
            "loopback": sorted(self.loopback),
            "out_levels": levels_to_json(self.out_levels),
            "mutes": mutes_to_json(self.mutes),
            "muted_cells": cells_to_json(self.muted_cells),
            "card_settings": dict(sorted(self.card_settings.items())),
            "conn_seq": dict(sorted(self.conn_seq.items(), key=lambda kv: kv[1])),
            "next_seq": self.next_seq,
            "matrix": self.matrix.to_sparse(),
            "presets": [p.to_dict() if p else None for p in self.presets],
            "settings": self.settings,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Memory":
        if d.get("format") != FORMAT_MEMORY:
            raise ValueError("not an AA-HDSPe-AIO-Mixer memory file")
        if int(d.get("version", 0)) > VERSION:
            raise ValueError(f"memory file version {d.get('version')} is newer than this app supports")
        m = cls(card_key=str(d.get("card", "")))
        for k in KINDS:
            for hw, n in (d.get("names", {}).get(k) or {}).items():
                m.set_name(k, int(hw), str(n))
        m.stereo = _stereo_from(d.get("stereo"))
        m.loopback = {int(x) for x in d.get("loopback", []) if 0 <= int(x) < 64}
        m.out_levels = levels_from_json(d.get("out_levels"))
        m.mutes = mutes_from_json(d.get("mutes"))
        m.muted_cells = cells_from_json(d.get("muted_cells"))
        m.card_settings = _card_settings_from(d.get("card_settings"))
        m.conn_seq = {str(k): int(v) for k, v in (d.get("conn_seq") or {}).items()}
        m.next_seq = max([int(d.get("next_seq", 0))] + [v + 1 for v in m.conn_seq.values()])
        m.matrix = Matrix.from_sparse(d.get("matrix", []))
        presets = d.get("presets", [])
        for i in range(MAX_PRESETS):
            if i < len(presets) and presets[i]:
                m.presets[i] = Preset.from_dict(presets[i])
        m.settings = dict(d.get("settings") or {})
        return m


def atomic_write_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def save_preset_file(path: str, preset: Preset):
    atomic_write_json(path, {"format": FORMAT_PRESET, "version": VERSION, **preset.to_dict()})


def load_preset_file(path: str) -> Preset:
    d = read_json(path)
    if d.get("format") != FORMAT_PRESET:
        raise ValueError("not an AA-HDSPe-AIO-Mixer preset file")
    return Preset.from_dict(d)


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "aa-hdspe-aio-mixer")


def memory_path(card_key: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in card_key) or "default"
    return os.path.join(config_dir(), f"memory-{safe}.json")
