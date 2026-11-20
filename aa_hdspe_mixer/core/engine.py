"""The mixer engine: owns the card, the memory and all timers. UI-independent (QtCore only).

Visible vs hidden:
- The status timer (1 Hz: sample rate, speed, expansion boards, retrying failed writes) runs ALWAYS.
- The meter timer runs only while someone wants meters (the visible window). It is the only meter
  reader, because peak index n resets on every read (meters.py); when it is off, the status timer
  reads one frame per second just for speed/board detection.
- Future MIDI/OSC front-ends should call the same public methods as the UI (from the GUI thread, or
  via queued signals from their own threads); they then work whether or not the window is shown.
"""
from __future__ import annotations

import errno
import math
import os

from PySide6.QtCore import QObject, QTimer, Signal

from .alsa import AlsaError
from .backend import Backend
from .card_settings import EnumSetting, settings_for
from .clock import SOURCES, TUNE_SPAN, ClockState
from .channels import IN, KINDS, OUT, PB, layout
from .memory import (MAX_PRESETS, Memory, Preset, atomic_write_json, load_preset_file, memory_path, read_json,
                     save_preset_file)
from .meters import MeterFrame
from .routing import (MAX_DB, MAX_GAIN, MIN_DB, N_DST, N_SRC, UNITY, Connection, Matrix, Unit, build_units,
                      connection_cells, db_to_raw, raw_to_db)
from .tone import TonePlan, pcm_order

METER_RATES_HZ = (10, 20, 30)
DEFAULT_HUE_STEP = 20     # degrees of hue between consecutive connections' wires
DEFAULT_METER_HZ = 20      # each meter ioctl costs ~1.2 ms of kernel time on the AIO (measured)
STATUS_INTERVAL_MS = 1000


class Engine(QObject):
    layoutChanged = Signal()        # units changed (speed, boards, mono/stereo)
    routingChanged = Signal()
    namesChanged = Signal()
    presetsChanged = Signal()
    settingsChanged = Signal()      # memory.settings replaced (load/import)
    outputLevelsChanged = Signal()
    mutesChanged = Signal()         # channel or connection mutes changed
    cardSettingsChanged = Signal()  # a card setting changed (here, or outside the app)
    clockChanged = Signal()         # clock state re-read and different
    statusChanged = Signal()        # rate / speed / write state
    metersUpdated = Signal(object)  # MeterFrame
    message = Signal(str, str)      # (level: "info"|"warning"|"error", text)
    testChanged = Signal()          # a channel test started or ended
    _testEnded = Signal(int, str)   # (run, error), from the player's thread

    def __init__(self, backend: Backend, memory_file: str | None = None, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.info = backend.info
        self.memory_file = memory_file or memory_path(self.info.key)
        self.memory = Memory(card_key=self.info.key)
        self.rate: int | None = None
        self.speed = 0
        self.ai4s = False
        self.ao4s = False
        self.pending: dict[tuple[int, int], int] = {}   # cell writes that failed and will be retried
        self._eff: list[list[int]] | None = None          # effective matrix last queued to the card
        self.last_error = ""
        self._units: dict[str, list[Unit]] = {}
        self._frame: MeterFrame | None = None
        self._settings: list[EnumSetting] = []            # card settings this card/driver has
        self.clock: ClockState | None = None              # last read; None = not read / not available
        self._clock_active = False                        # clock section open and window shown: re-read every second
        self._poll_settings = True                        # window shown: re-read card settings every second
        self._choices: dict[str, tuple[str, ...]] = {}
        self.testing: tuple[str, tuple[int, ...]] | None = None   # (kind, hw channels) under test
        self._test_run = 0
        self._testEnded.connect(self._on_test_ended)

        self._status_timer = QTimer(self, interval=STATUS_INTERVAL_MS, timeout=self._status_tick)
        self._meter_timer = QTimer(self, interval=1000 // DEFAULT_METER_HZ, timeout=self._meter_tick)
        self._save_timer = QTimer(self, interval=1000, singleShot=True, timeout=self.save_memory)

    # ================================================================== lifecycle
    def start(self):
        self.load_memory()
        self._read_status(force_layout=True)
        # The hardware mixer is write-only and its state after driver load depends on the driver and
        # distro (snd-hdspm zeroes it, Debian's alsactl restore then rewrites 'Chn'; snd-hdspe develop
        # leaves it untouched). The app's memory is the source of truth, so write all of it once.
        self._sync(full=True)
        self._init_card_settings()
        self._status_timer.start()

    def shutdown(self):
        self.stop_test()
        self._meter_timer.stop()
        self._status_timer.stop()
        if self._save_timer.isActive():
            self._save_timer.stop()
        self.save_memory()
        self.backend.close()

    def set_meters_active(self, on: bool):
        if on and not self._meter_timer.isActive():
            self._meter_timer.start()
        elif not on and self._meter_timer.isActive():
            self._meter_timer.stop()

    @property
    def meter_hz(self) -> int:
        hz = int(self.memory.settings.get("meter_hz", DEFAULT_METER_HZ))
        return hz if hz in METER_RATES_HZ else DEFAULT_METER_HZ

    def set_meter_hz(self, hz: int):
        if hz in METER_RATES_HZ:
            self.memory.settings["meter_hz"] = hz
            self._meter_timer.setInterval(1000 // hz)
            self.settingsChanged.emit()
            self._changed()

    @property
    def meters_active(self) -> bool:
        return self._meter_timer.isActive()

    # ================================================================== memory
    def load_memory(self):
        if os.path.exists(self.memory_file):
            try:
                self.memory = Memory.from_dict(read_json(self.memory_file))
                self.memory.card_key = self.info.key
            except (OSError, ValueError) as e:
                self.message.emit("warning", f"Could not read {self.memory_file}: {e}. Starting empty.")
                self.memory = Memory(card_key=self.info.key)
        self._prune_mutes()
        self._rebuild_units()
        self._meter_timer.setInterval(1000 // self.meter_hz)
        self.namesChanged.emit()
        self.presetsChanged.emit()
        self.settingsChanged.emit()

    def save_memory(self):
        try:
            atomic_write_json(self.memory_file, self.memory.to_dict())
        except OSError as e:
            self.message.emit("error", f"Could not save memory: {e}")

    def _changed(self):
        self._save_timer.start()

    # ================================================================== status & meters
    def _read_status(self, force_layout=False):
        try:
            frame = self.backend.read_frame() if not self.meters_active else self._frame
            rate = self.backend.read_rate()
        except OSError as e:
            self._set_error(f"Card not reachable: {e}")
            return
        if frame is not None:
            self._apply_frame_status(frame, force_layout)
        if rate != self.rate:
            self.rate = rate
            self.statusChanged.emit()

    def _apply_frame_status(self, frame: MeterFrame, force_layout=False):
        changed = (frame.speed, frame.ai4s_present, frame.ao4s_present) != (self.speed, self.ai4s, self.ao4s)
        if changed or force_layout:
            self.speed, self.ai4s, self.ao4s = frame.speed, frame.ai4s_present, frame.ao4s_present
            self.stop_test()        # its channel numbers were for the old layout
            self._rebuild_units()
            self.statusChanged.emit()

    def _status_tick(self):
        self._read_status()
        if self._poll_settings:
            self._poll_card_settings()
        if self._clock_active:
            self.refresh_clock()
        if self.pending:
            self._retry_pending()

    def _meter_tick(self):
        try:
            frame = self.backend.read_frame()
        except OSError as e:
            self._set_error(f"Meter read failed: {e}")
            return
        self._frame = frame
        self._apply_frame_status(frame)
        self.metersUpdated.emit(frame)

    # ================================================================== units & connections
    def _rebuild_units(self):
        self._units = {k: build_units(k, layout(k, self.speed, self.ai4s, self.ao4s), self.memory.stereo[k])
                       for k in KINDS}
        self.layoutChanged.emit()
        self.routingChanged.emit()

    def units(self, kind: str) -> list[Unit]:
        return self._units.get(kind, [])

    def can_pair(self, kind: str, hw: int) -> bool:
        """Whether the channel and its pair partner (1+2, 3+4, ...) are both present, so they can be joined
        as a stereo pair, from either channel."""
        present = {c.hw for u in self.units(kind) for c in u.channels}
        return (hw & ~1) in present and (hw | 1) in present

    def unit(self, key: str) -> Unit | None:
        kind = key.split(":", 1)[0]
        return next((u for u in self.units(kind) if u.key == key), None)

    def connections(self) -> list[Connection]:
        conns = self.memory.matrix.connections(self.units(IN) + self.units(PB), self.units(OUT))
        # Number connections in the order they appear, so wire colours stay put when others are added.
        # Connections that come from a preset, an import or a mono/stereo switch are numbered on first sight.
        seq = self.memory.conn_seq
        for c in conns:
            if c.key not in seq:
                seq[c.key] = self.memory.next_seq
                self.memory.next_seq += 1
                self._changed()
        return conns

    def connection_seq(self, key: str) -> int:
        return self.memory.conn_seq.get(key, 0)

    # Wire colours: hue rotates by this many degrees per connection (0 = colour by source kind).
    @property
    def hue_step(self) -> int:
        try:
            return max(0, min(180, int(self.memory.settings.get("hue_step", DEFAULT_HUE_STEP))))
        except (TypeError, ValueError):
            return DEFAULT_HUE_STEP

    def set_hue_step(self, deg: int):
        deg = max(0, min(180, int(deg)))
        if deg != self.hue_step:
            self.memory.settings["hue_step"] = deg
            self.settingsChanged.emit()
            self._changed()

    def unit_level(self, frame: MeterFrame, unit: Unit) -> float:
        arr = {IN: frame.inputs, PB: frame.playback, OUT: frame.outputs}[unit.kind]
        return max(arr[c.hw] for c in unit.channels)

    # ================================================================== routing edits
    def set_connection(self, src_key: str, dst_key: str, raw: int) -> bool:
        src, dst = self.unit(src_key), self.unit(dst_key)
        if src is None or dst is None or src.kind not in (IN, PB) or dst.kind != OUT:
            return False
        was_muted = self._connection_muted(src, dst)
        changes = self.memory.matrix.set_connection(src, dst, raw)
        if raw and was_muted:                          # a level change keeps the connection muted
            self.memory.muted_cells |= set(self._routed_cells(src, dst))
        self._prune_mutes()                            # removing a connection drops its mute
        key = f"{src.key}>{dst.key}"
        if raw == 0:
            self.memory.conn_seq.pop(key, None)
        elif key not in self.memory.conn_seq:          # number it now: creation order = colour order
            self.memory.conn_seq[key] = self.memory.next_seq
            self.memory.next_seq += 1
        if changes:
            self._sync()
            self.routingChanged.emit()
            self._changed()
        return True

    def set_connection_db(self, src_key: str, dst_key: str, db: float) -> bool:
        return self.set_connection(src_key, dst_key, db_to_raw(db))

    def nudge_connection(self, src_key: str, dst_key: str, delta_db: float) -> bool:
        """Relative change (mouse wheel). Clamped to MIN_DB..MAX_DB, so it never removes the connection."""
        cur = self.connection_db(src_key, dst_key)
        if cur == -math.inf or delta_db == 0:
            return False                      # not a connection: nudging must not create one either
        cur_raw = db_to_raw(cur)
        raw = max(1, db_to_raw(max(MIN_DB, min(MAX_DB, cur + delta_db))))
        # Below about -66 dB one gain step is larger than 0.5 dB, so rounding can give back the same
        # value and the level would stick. Always move at least one step in the requested direction.
        if raw == cur_raw:
            raw = cur_raw + (1 if delta_db > 0 else -1)
        raw = max(1, min(MAX_GAIN, raw))
        return self.set_connection(src_key, dst_key, raw)

    def remove_connection(self, src_key: str, dst_key: str) -> bool:
        return self.set_connection(src_key, dst_key, 0)

    def connection_db(self, src_key: str, dst_key: str) -> float:
        for c in self.connections():
            if c.src.key == src_key and c.dst.key == dst_key:
                return raw_to_db(c.gain)
        return -math.inf

    def clear_routing(self):
        changes = self.memory.matrix.clear()
        self.memory.conn_seq.clear()
        self.memory.muted_cells.clear()
        if changes:
            self._sync()
            self.routingChanged.emit()
            self._changed()

    def apply_matrix(self, matrix: Matrix, stereo: dict[str, set[int]] | None = None,
                     out_levels: dict[int, float] | None = None):
        self.memory.matrix = matrix.copy()
        self._prune_mutes()
        if stereo is not None:
            self.memory.stereo = {k: set(v) for k, v in stereo.items()}
            self._rebuild_units()
        if out_levels is not None:
            self.memory.out_levels = dict(out_levels)
            self.outputLevelsChanged.emit()
        self._sync()
        self.routingChanged.emit()
        self._changed()

    # ================================================================== output levels
    # Like TotalMix/hdspmixer, the output level is not a separate hardware control: it scales every
    # matrix cell feeding that output. The stored matrix keeps the route gains; the card gets
    # route gain x output level (see _effective()).
    def output_level_db(self, hw: int) -> float:
        return self.memory.out_levels.get(hw, 0.0)

    def set_output_level(self, dst_key: str, db: float) -> bool:
        dst = self.unit(dst_key)
        if dst is None or dst.kind != OUT:
            return False
        db = min(MAX_DB, db)
        for c in dst.channels:
            if db == 0:
                self.memory.out_levels.pop(c.hw, None)
            else:
                self.memory.out_levels[c.hw] = db
        self._sync()
        self.outputLevelsChanged.emit()
        self._changed()
        return True

    # ================================================================== mutes
    # A mute is not a level: the card gets -inf for everything a mute covers, but the app keeps the
    # connection and its level, so unmuting restores it exactly (see memory.py for the registers).
    def unit_muted(self, key: str) -> bool:
        u = self.unit(key)
        return u is not None and any(c.hw in self.memory.mutes[u.kind] for c in u.channels)

    def set_unit_mute(self, key: str, on: bool) -> bool:
        u = self.unit(key)
        if u is None:
            return False
        m = self.memory.mutes[u.kind]
        for c in u.channels:
            (m.add if on else m.discard)(c.hw)
        self._mutes_changed()
        return True

    def toggle_unit_mute(self, key: str) -> bool:
        return self.set_unit_mute(key, not self.unit_muted(key))

    def _routed_cells(self, src: Unit, dst: Unit) -> list[tuple[int, int]]:
        g = self.memory.matrix.g
        return [(s, d) for s, d in connection_cells(src, dst) if g[d][s]]

    def _connection_muted(self, src: Unit, dst: Unit) -> bool:
        return any(c in self.memory.muted_cells for c in self._routed_cells(src, dst))

    def connection_muted(self, src_key: str, dst_key: str) -> bool:
        src, dst = self.unit(src_key), self.unit(dst_key)
        return src is not None and dst is not None and self._connection_muted(src, dst)

    def connection_silenced(self, src_key: str, dst_key: str) -> bool:
        """Muted by its source or its output (the connection's own mute is separate)."""
        return self.unit_muted(src_key) or self.unit_muted(dst_key)

    def set_connection_mute(self, src_key: str, dst_key: str, on: bool) -> bool:
        src, dst = self.unit(src_key), self.unit(dst_key)
        cells = self._routed_cells(src, dst) if src is not None and dst is not None else []
        if not cells:
            return False                       # not a connection: muting must not create one
        if on:
            self.memory.muted_cells |= set(cells)
        else:
            self.memory.muted_cells -= set(cells)
        self._mutes_changed()
        return True

    def toggle_connection_mute(self, src_key: str, dst_key: str) -> bool:
        return self.set_connection_mute(src_key, dst_key, not self.connection_muted(src_key, dst_key))

    def _prune_mutes(self):
        g = self.memory.matrix.g
        self.memory.muted_cells = {(s, d) for s, d in self.memory.muted_cells if g[d][s]}

    def _mutes_changed(self):
        self._sync()
        self.mutesChanged.emit()
        self._changed()

    # ================================================================== card settings
    # Hardware controls other than the matrix (card_settings.py), e.g. the AIO's S/PDIF input connector.
    # Like the matrix, the app's memory is the source of truth at start: saved values are written to
    # the card. Afterwards the card is re-read every second, so a change made elsewhere (alsamixer)
    # is adopted rather than fought.
    def _init_card_settings(self):
        self._settings, self._choices = [], {}
        for s in settings_for(self.info.model):
            if s.linked:
                continue
            try:
                choices = self.backend.setting_choices(s)
            except OSError:
                choices = None
            if choices:
                self._settings.append(s)
                self._choices[s.id] = tuple(choices)
        for s in settings_for(self.info.model):      # linked settings exist when what they follow exists
            if s.linked and s.follows in self._choices:
                self._settings.append(s)
                self._choices[s.id] = s.choices
        self._apply_card_settings(self.memory.card_settings)

    def _real_settings(self) -> list[EnumSetting]:
        return [s for s in self._settings if not s.linked]

    def _store_setting(self, sid: str, value: str):
        old = self.memory.card_settings.get(sid)
        if old is not None and old != value:           # remembered, so a linked setting can switch back
            self.memory.settings.setdefault("prev_card_settings", {})[sid] = old
        self.memory.card_settings[sid] = value

    def _apply_card_settings(self, wanted: dict[str, str]):
        for s in self._real_settings():
            want = wanted.get(s.id)
            try:
                cur = self.backend.read_setting(s)
                if want in self._choices[s.id] and want != cur:
                    self.backend.write_setting(s, want)
                    cur = want
            except (OSError, ValueError) as e:
                self._set_error(f"{s.label}: {e}")
                continue
            self._store_setting(s.id, cur)
        self.cardSettingsChanged.emit()
        self._changed()

    def set_settings_polling(self, on: bool):
        """Re-read card settings every second (for changes made outside the app) only while the window is
        shown. Switching it back on re-reads them at once, so the window never shows stale values."""
        on = bool(on)
        if on and not self._poll_settings:
            self._poll_card_settings()
        self._poll_settings = on

    def _poll_card_settings(self):
        changed = False
        for s in self._real_settings():
            try:
                cur = self.backend.read_setting(s)
            except OSError:
                continue
            if self.memory.card_settings.get(s.id) != cur:
                self._store_setting(s.id, cur)
                changed = True
        if changed:
            self.cardSettingsChanged.emit()
            self._changed()

    def card_settings(self) -> list[EnumSetting]:
        return list(self._settings)

    def panel_settings(self) -> list[EnumSetting]:
        """Card settings for the side panel (no channel of their own) that this card/driver has."""
        return [s for s in self._settings if s.panel]

    def panel_settings_missing(self) -> list[EnumSetting]:
        """Panel settings this card model has but the current driver doesn't expose."""
        have = {s.id for s in self._settings}
        return [s for s in settings_for(self.info.model) if s.panel and s.id not in have]

    def _not_in_presets(self) -> set[str]:
        return {s.id for s in settings_for(self.info.model) if not s.in_presets}

    def unit_settings(self, unit: Unit) -> list[EnumSetting]:
        hws = [c.hw for c in unit.channels]
        return [s for s in self._settings if s.belongs_to(unit.kind, hws)]

    def _setting(self, sid: str) -> EnumSetting | None:
        return next((x for x in self._settings if x.id == sid), None)

    def card_setting(self, sid: str) -> str | None:
        s = self._setting(sid)
        if s is not None and s.linked:
            src = self.memory.card_settings.get(s.follows)
            return None if src is None else s.choices[1] if src == s.follow_value else s.choices[0]
        return self.memory.card_settings.get(sid)

    def setting_choices(self, sid: str) -> tuple[str, ...]:
        return self._choices.get(sid, ())

    def set_card_setting(self, sid: str, value: str) -> bool:
        s = self._setting(sid)
        if s is None or value not in self._choices[sid]:
            return False
        if s.linked:
            if value == s.choices[1]:
                return self.set_card_setting(s.follows, s.follow_value)
            if self.memory.card_settings.get(s.follows) != s.follow_value:
                return True                            # already off that value
            # Back to the followed setting's previous value (e.g. Coaxial or Internal), else its first
            # other choice.
            prev = self.memory.settings.get("prev_card_settings", {}).get(s.follows)
            others = [c for c in self._choices[s.follows] if c != s.follow_value]
            return self.set_card_setting(s.follows, prev if prev in others else others[0])
        try:
            self.backend.write_setting(s, value)
        except (OSError, ValueError) as e:
            self._set_error(f"{s.label}: could not set {value}: {e}")
            return False
        self._store_setting(sid, value)
        self.cardSettingsChanged.emit()
        self._changed()
        return True

    # ================================================================== clock
    # Rate, source, input status and fine tuning (clock.py). Not stored anywhere: the card owns it.
    def set_clock_active(self, on: bool):
        self._clock_active = bool(on)
        if on:
            self.refresh_clock()

    def refresh_clock(self):
        clk = getattr(self.backend, "clock", None)
        if clk is None or not clk.available():
            return
        try:
            st = clk.read(self.speed)
        except (OSError, StopIteration, IndexError) as e:
            self._set_error(f"Clock status not readable: {e}")
            return
        if st != self.clock:
            self.clock = st
            self.clockChanged.emit()

    def _clock_write(self, what: str, fn, *args) -> bool:
        try:
            fn(*args)
        except (OSError, StopIteration) as e:
            self._set_error(f"Could not set {what}: {e}")
            return False
        self._read_status()
        self.refresh_clock()
        return True

    def set_clock_source(self, key: str) -> bool:
        """'internal' = clock master; any other source = AutoSync with that source preferred."""
        self.refresh_clock()
        st, clk = self.clock, getattr(self.backend, "clock", None)
        if st is None or clk is None or key not in SOURCES:
            return False
        if key == "internal":
            return self._clock_write("the clock source", clk.write_master, True)
        if key not in st.sources:
            return False
        return (self._clock_write("the preferred clock source", clk.write_preferred, key)
                and self._clock_write("the clock mode", clk.write_master, False))

    def set_internal_rate(self, hz: int) -> bool:
        self.refresh_clock()
        st = self.clock
        if st is None or hz not in st.rates:
            return False
        return self._clock_write(f"the sample rate to {hz} Hz", self.backend.clock.write_internal_rate, hz)

    def tune_range(self) -> tuple[int, int] | None:
        """Fine-tuning range around the internal rate (+/- TUNE_SPAN), within what the driver allows."""
        st = self.clock
        if st is None or not st.internal_rate or st.tuned_rate is None:
            return None
        lo = max(st.tune_min, round(st.internal_rate * (1 - TUNE_SPAN)))
        hi = min(st.tune_max, round(st.internal_rate * (1 + TUNE_SPAN)))
        return (lo, hi) if lo < hi else None

    def set_tuned_rate(self, hz: float) -> bool:
        """Fine-tune the internal clock (only while the card is clock master). Clamped to tune_range()."""
        self.refresh_clock()
        st, rng = self.clock, self.tune_range()
        if st is None or rng is None or not st.master:
            return False
        hz = max(rng[0], min(rng[1], float(hz)))
        return self._clock_write("the sample rate tuning", self.backend.clock.write_tuned_rate, hz, self.speed)

    # ================================================================== names & modes
    def rename(self, kind: str, hw: int, name: str):
        self.memory.set_name(kind, hw, name)
        self.namesChanged.emit()
        self._changed()

    def set_stereo(self, kind: str, pair: int, on: bool):
        s = self.memory.stereo[kind]
        if (pair in s) == on:
            return
        (s.add if on else s.discard)(pair)
        self._rebuild_units()
        self._changed()

    # ================================================================== channel test
    def unit_testing(self, key: str) -> bool:
        u = self.unit(key)
        return bool(u and self.testing and self.testing[0] == u.kind
                    and any(c.hw in self.testing[1] for c in u.channels))

    def test_channel(self, key: str) -> bool:
        """Play the test signal (tone.py) to a playback channel, or stop it if that channel is being tested.
        Another channel's test stops first."""
        u = self.unit(key)
        if not u or u.kind != PB:
            return False
        if self.unit_testing(key):
            self.stop_test()
            return True
        self.stop_test()
        order = pcm_order(self.speed, self.ao4s)
        hw = [c.hw for c in u.channels]
        if any(h not in order for h in hw):
            self.message.emit("error", f"Channel test: {u.hw_name} has no ALSA playback channel at this speed.")
            return False
        plan = TonePlan(card=self.info.index, rate=self.rate or 48000, pcm_channels=len(order),
                        slots=[order.index(h) for h in hw], hw=hw)
        self._test_run += 1
        run = self._test_run
        self.testing = (u.kind, tuple(hw))
        self.testChanged.emit()
        self.backend.tone_player().start(plan, lambda err: self._testEnded.emit(run, err))
        return True

    def stop_test(self):
        if self.testing is None:
            return
        self._test_run += 1                 # whatever the stopped run reports is ignored
        self.testing = None
        self.backend.tone_player().stop()
        self.testChanged.emit()

    def _on_test_ended(self, run: int, err: str):
        if run != self._test_run:
            return
        self.testing = None
        self.testChanged.emit()
        if err:
            self.message.emit("error", f"Channel test: {err}")

    def set_loopback(self, out_hw: int, on: bool):
        # Kept in memory and presets; no driver exposes loopback yet, so nothing is written.
        (self.memory.loopback.add if on else self.memory.loopback.discard)(out_hw)
        self._changed()

    @property
    def loopback_supported(self) -> bool:
        return False

    # ================================================================== presets
    def presets(self) -> list[Preset | None]:
        return self.memory.presets

    def store_preset(self, slot: int, name: str):
        if 0 <= slot < MAX_PRESETS:
            self.memory.store_preset(slot, name, self._not_in_presets())
            self.presetsChanged.emit()
            self._changed()

    def recall_preset(self, slot: int, names: bool = True, destinations: bool = True, levels: bool = True,
                      mutes: bool = True, card: bool = True) -> bool:
        p = self.memory.presets[slot] if 0 <= slot < MAX_PRESETS else None
        if p is None:
            return False
        self.apply_preset(p, names, destinations, levels, mutes, card)
        self.message.emit("info", f"Recalled preset {slot + 1}: {p.name}")
        return True

    def apply_preset(self, p: Preset, names: bool = True, destinations: bool = True, levels: bool = True,
                     mutes: bool = True, card: bool = True):
        """Restore any combination of the preset's parts.

        names        : channel names (replaced as a whole).
        destinations : which source feeds which output, plus mono/stereo modes and loopback.
        levels       : connection levels and output levels.
        mutes        : muted channels and connections.
        card         : card settings (e.g. S/PDIF input); settings the preset lacks stay as they are.
        Cell rules when only one of destinations/levels is chosen:
          destinations only: the preset's connections; a cell that is already routed keeps its
                             current level, a newly routed cell gets 0 dB.
          levels only      : the current connections; each takes the preset's level where the preset
                             routes that cell too, otherwise keeps its current level.
        Connection mutes follow the same idea: with destinations, the preset's connections carry the
        preset's mutes; without, a current connection takes the preset's mute where the preset routes
        it too, otherwise keeps its own. Without mutes, connections that stay keep their mute.
        """
        cur, pm = self.memory.matrix, p.matrix
        if destinations and levels:
            new = pm.copy()
        elif destinations:
            new = Matrix([[(c if c else UNITY) if v else 0 for c, v in zip(cr, pr)] for cr, pr in zip(cur.g, pm.g)])
        elif levels:
            new = Matrix([[(v if v else c) if c else 0 for c, v in zip(cr, pr)] for cr, pr in zip(cur.g, pm.g)])
        else:
            new = cur.copy()
        if destinations:
            self.memory.loopback = set(p.loopback)
        if mutes:
            self.memory.mutes = {k: set(p.mutes.get(k, set())) for k in KINDS}
            if destinations:
                self.memory.muted_cells = set(p.muted_cells)
            else:     # the preset decides for cells it routes, the others keep their mute
                self.memory.muted_cells = set(p.muted_cells) | {(s, d) for s, d in self.memory.muted_cells
                                                                if not pm.g[d][s]}
        self.apply_matrix(new, p.stereo if destinations else None, p.out_levels if levels else None)
        self.mutesChanged.emit()
        if card:
            skip = self._not_in_presets()
            for sid, value in p.card_settings.items():
                if sid in self._choices and sid not in skip and value != self.card_setting(sid):
                    self.set_card_setting(sid, value)
        if names and p.names is not None:
            self.memory.names = {k: dict(p.names.get(k, {})) for k in KINDS}
            self.namesChanged.emit()
            self._changed()

    def delete_preset(self, slot: int):
        if 0 <= slot < MAX_PRESETS and self.memory.presets[slot]:
            self.memory.presets[slot] = None
            self.presetsChanged.emit()
            self._changed()

    def save_preset_file(self, path: str, name: str):
        save_preset_file(path, self.memory.snapshot(name, self._not_in_presets()))

    def read_preset_file(self, path: str) -> Preset:
        return load_preset_file(path)

    def load_preset_file(self, path: str, names: bool = True, destinations: bool = True, levels: bool = True,
                         mutes: bool = True, card: bool = True) -> str:
        p = load_preset_file(path)
        self.apply_preset(p, names, destinations, levels, mutes, card)
        return p.name

    # The memory's parts for File > Import Memory: the routing (all but the card settings and the presets), the
    # card settings, and the presets.
    ROUTING_FIELDS = ("names", "stereo", "loopback", "out_levels", "mutes", "muted_cells", "conn_seq", "next_seq",
                      "matrix")

    def export_memory(self, path: str):
        atomic_write_json(path, self.memory.to_dict())

    def import_memory(self, path: str):
        self.import_memory_parts(read_json(path))

    def import_memory_parts(self, data: dict, routing: bool = True, card: bool = True, presets: bool = True,
                            preset_from: int = -1, preset_to: int = -1):
        """From a memory file (export_memory), any of: the routing (channel names, mono/stereo, connections,
        levels, mutes, and the display settings kept per card), the card settings, the presets: all of the
        file's (preset_from -1), or only its preset preset_from, into slot preset_to (the other slots stay).
        What isn't picked stays as it is. Raises ValueError for a file that isn't one, or a preset that isn't
        in it."""
        new = Memory.from_dict(data)
        mem = self.memory
        if presets and preset_from >= 0:
            in_range = 0 <= preset_from < MAX_PRESETS and 0 <= preset_to < MAX_PRESETS
            if not in_range or new.presets[preset_from] is None:
                raise ValueError(f"the file has no preset {preset_from + 1}")
        if presets:
            if preset_from < 0:
                mem.presets = new.presets
            else:
                mem.presets[preset_to] = new.presets[preset_from]
            self.presetsChanged.emit()
        if routing:
            for f in self.ROUTING_FIELDS:
                setattr(mem, f, getattr(new, f))
            prev = mem.settings.get("prev_card_settings")
            mem.settings = {k: v for k, v in new.settings.items() if k != "prev_card_settings"}
            if prev is not None:
                mem.settings["prev_card_settings"] = prev
            self._meter_timer.setInterval(1000 // self.meter_hz)
        if card:
            prev = new.settings.get("prev_card_settings")
            if prev is not None:
                mem.settings["prev_card_settings"] = prev
            self._apply_card_settings(dict(new.card_settings))
        if routing:
            self._prune_mutes()
            self._rebuild_units()
            self._sync()
            self.mutesChanged.emit()
            self.outputLevelsChanged.emit()
            self.namesChanged.emit()
            self.routingChanged.emit()
        self.settingsChanged.emit()
        self._changed()

    # ------------------------------------------------------------------ the clock, for export / import
    def clock_export(self) -> dict | None:
        """The clock settings an import can set again: source, internal rate, fine-tuned rate. None when the
        card's clock can't be read."""
        self.refresh_clock()
        st = self.clock
        if st is None:
            return None
        d = {"source": st.selected or "internal"}
        if st.internal_rate:
            d["internal_rate"] = int(st.internal_rate)
        if st.master and st.tuned_rate:
            d["tuned_rate"] = round(float(st.tuned_rate), 3)
        return d

    def clock_import(self, d: dict) -> list[str]:
        """Sets what clock_export() gave, as far as this card and driver allow. Returns what couldn't be set."""
        problems = []
        if not isinstance(d, dict):
            return ["no clock settings in the file"]
        self.refresh_clock()
        st = self.clock
        if st is None:
            return ["the card's clock can't be read"]
        rate = d.get("internal_rate")
        if isinstance(rate, int) and rate != st.internal_rate:
            if rate not in st.rates or not self.set_internal_rate(rate):
                problems.append(f"sample rate {rate} Hz")
        src = str(d.get("source") or "")
        if src and src != (self.clock.selected if self.clock else ""):
            if not self.set_clock_source(src):
                problems.append(f"clock source {src}")
        tuned = d.get("tuned_rate")
        if isinstance(tuned, (int, float)) and self.clock is not None and self.clock.master:
            if self.clock.tuned_rate is None or abs(float(tuned) - self.clock.tuned_rate) > 0.01:
                if not self.set_tuned_rate(float(tuned)):
                    problems.append("sample rate tuning")
        return problems

    # ================================================================== hardware writes
    def _effective(self) -> list[list[int]]:
        """What the card should hold, per cell: route gain x output level, or 0 where a mute applies
        (muted output, muted input/playback channel, muted connection)."""
        mem = self.memory
        muted_src = sorted(mem.mutes[IN]) + [64 + hw for hw in mem.mutes[PB]]
        cells_by_dst: dict[int, list[int]] = {}
        for s, d in mem.muted_cells:
            cells_by_dst.setdefault(d, []).append(s)
        out = []
        for d, row in enumerate(mem.matrix.g):
            db = mem.out_levels.get(d, 0.0)
            if db == -math.inf or d in mem.mutes[OUT]:
                out.append([0] * N_SRC)
                continue
            if db == 0:
                row = row[:]
            else:
                f = 10 ** (db / 20.0)
                row = [min(MAX_GAIN, max(1, round(v * f))) if v else 0 for v in row]
            for s in muted_src:
                row[s] = 0
            for s in cells_by_dst.get(d, ()):
                row[s] = 0
            out.append(row)
        return out

    def _sync(self, full: bool = False):
        """Queue writes for every cell whose effective value changed (all cells if full) and send them."""
        new = self._effective()
        old = self._eff
        for d in range(N_DST):
            nr = new[d]
            if full or old is None:
                for s in range(N_SRC):
                    self.pending[(s, d)] = nr[s]
            elif old[d] != nr:
                orow = old[d]
                for s in range(N_SRC):
                    if orow[s] != nr[s]:
                        self.pending[(s, d)] = nr[s]
        self._eff = new
        self._retry_pending()

    def resend_all(self):
        self._sync(full=True)

    def _retry_pending(self):
        failed = None
        for (s, d), g in list(self.pending.items()):
            try:
                self.backend.write_cell(s, d, g)
            except OSError as e:
                failed = e
                break
            del self.pending[(s, d)]
        if failed is not None:
            if getattr(failed, "errno", None) == errno.EBUSY:
                self._set_error("Mixer busy: playback and capture are held by different programs. "
                                f"{len(self.pending)} change(s) will be retried.")
            else:
                self._set_error(f"Mixer write failed: {failed}. {len(self.pending)} change(s) will be retried.")
        elif self.last_error:
            self.last_error = ""
            self.statusChanged.emit()

    def _set_error(self, text: str):
        if text != self.last_error:
            self.last_error = text
            self.statusChanged.emit()
            self.message.emit("error", text)
