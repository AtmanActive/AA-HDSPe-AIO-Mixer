"""QML-facing facade over the Engine. Plain lists/maps only, rebuilt when the engine signals a change."""
from __future__ import annotations

import math
import os
import re
import threading

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

from .. import APP_NAME, __version__
from ..core.app_settings import (COLOR_MODE_CHOICES, COLOR_MODE_LABELS, START_WINDOW_CHOICES,
                                 START_WINDOW_LABELS, ZOOM_CHOICES, AppSettings, is_dark, zoom_label)
from ..core.channels import IN, KIND_LABEL, OUT, PB, layout
from ..core.engine import METER_RATES_HZ, Engine
from ..core.card_settings import settings_for
from ..core.memory import MAX_PRESETS, Memory, atomic_write_json
from ..core.midi import (DB_FLOOR, FOLLOW, LEVEL, MESSAGES, MODES, MUTE, PRESET, PRESET_BY_NUMBER, PRESS_MUTE,
                         PRESS_UNMUTE, TOGGLE, WHATS, Binding,
                         MidiControl, behaviour_text, message_text)
from ..core import osc as oscmod
from ..core.osc import OscBinding, OscControl, UdpTransport
from ..core import shortcuts as keysmod
from ..core.shortcuts import KeyBinding, ShortcutControl
from ..core.meters import SPEED_NAMES, linear_to_db
from ..core.pwvirtual import DIRECTIONS, INPUT, OUTPUT
from ..core.routing import Unit, format_db, parse_db, raw_to_db
from ..core.tone import pcm_order

METER_FLOOR_DB = -60.0


def _sorted_by_text(items: list) -> list:
    """Drop-down items ({value, text}) A to Z by text, ignoring case, numbers by value ("ADAT 9" before "ADAT 10")."""
    return sorted(items, key=lambda t: [(0, int(x), "") if x.isdigit() else (1, 0, x.casefold())
                                        for x in re.split(r"(\d+)", t["text"])])


class Bridge(QObject):
    infoChanged = Signal()
    unitsChanged = Signal()
    routingChanged = Signal()
    presetsChanged = Signal()
    levelsChanged = Signal()
    metersEnabledChanged = Signal()
    meterRateChanged = Signal()
    hueStepChanged = Signal()
    settingsPanelChanged = Signal()
    channelChanged = Signal()          # the channel shown in the side panel, or its data
    channelLevelsChanged = Signal()
    clockChanged = Signal()
    appSettingsChanged = Signal()
    virtualChanged = Signal()          # virtual PipeWire devices: the list or their state
    virtualTargetsChanged = Signal()   # the card's PipeWire channels (only when they change: drop-downs keep)
    _virtualRefreshed = Signal(object, object)    # (snapshot, states), from the refresh thread
    midiChanged = Signal()             # MIDI on/off, ports, connections
    midiBindingsChanged = Signal()     # the bindings or their texts (only when they change: editors keep)
    midiTargetsChanged = Signal()      # what bindings can point at (only when it changes: drop-downs keep)
    midiActivityChanged = Signal()
    midiLearned = Signal("QVariantMap")   # {message, channel, number}: heard while learning
    oscChanged = Signal()              # OSC on/off, ports, the feedback host, errors
    oscBindingsChanged = Signal()
    oscTargetsChanged = Signal()
    oscActivityChanged = Signal()
    keysChanged = Signal()             # keyboard shortcuts on/off, Plasma there or not, errors
    keysBindingsChanged = Signal()
    keysTargetsChanged = Signal()
    keysActivityChanged = Signal()
    oscLearned = Signal("QVariantMap")    # {address, number}: heard while learning (number: its value or null)
    notify = Signal(str, str)          # (level, text) for the status/toast area
    requestShow = Signal()
    restartRequested = Signal()        # the shell restarts the app (a new zoom applies at start)
    recallRequested = Signal(int, str, bool)   # slot (-1 = preset file), preset name, preset has names
    # File > Import Memory: file name, its parts (IMPORT_PARTS), its presets ([{slot, text}], the stored ones)
    importRequested = Signal(str, "QVariantList", "QVariantList")

    def __init__(self, engine: Engine, app_settings: AppSettings | None = None, parent=None):
        super().__init__(parent)
        self.e = engine
        self.app_settings = app_settings or AppSettings()
        self._levels: dict[str, float] = {}
        # Which section the side panel shows, this session: "" (none), "card" (hammer button), "clock" (clock
        # button), "app" (cog button) or "channel" (a pill clicked). One at a time, like radio buttons.
        self._panel = ""
        # The channel shown by the "channel" section, as (kind, hw): it follows the channel when it is
        # joined into or split from a stereo pair, which changes its unit's key.
        self._channel: tuple[str, int] | None = None
        self._channel_levels: list[float] = []
        self.running_zoom = self.app_settings.zoom   # the zoom this run started with (set by the shell)
        self._window_shown = True           # set by the shell: hidden/minimised window = no clock polling
        app = QGuiApplication.instance()
        if app is not None:                 # "System" colour mode follows the desktop live
            app.styleHints().colorSchemeChanged.connect(self.appSettingsChanged)
            app.paletteChanged.connect(self.appSettingsChanged)
        engine.statusChanged.connect(self.infoChanged)
        engine.layoutChanged.connect(self.unitsChanged)
        engine.namesChanged.connect(self.unitsChanged)
        engine.namesChanged.connect(self.routingChanged)
        engine.routingChanged.connect(self.routingChanged)
        engine.presetsChanged.connect(self.presetsChanged)
        engine.outputLevelsChanged.connect(self.unitsChanged)
        engine.outputLevelsChanged.connect(self.routingChanged)
        engine.mutesChanged.connect(self.unitsChanged)
        engine.testChanged.connect(self.unitsChanged)
        engine.mutesChanged.connect(self.routingChanged)
        engine.cardSettingsChanged.connect(self.unitsChanged)
        engine.cardSettingsChanged.connect(self.routingChanged)
        engine.clockChanged.connect(self.clockChanged)
        engine.statusChanged.connect(self.clockChanged)
        engine.metersUpdated.connect(self._on_frame)
        engine.message.connect(self.notify)
        engine.settingsChanged.connect(self.metersEnabledChanged)
        engine.settingsChanged.connect(self.meterRateChanged)
        engine.settingsChanged.connect(self.hueStepChanged)
        for sig in (self.unitsChanged, self.routingChanged, self.settingsPanelChanged, self.hueStepChanged):
            sig.connect(self.channelChanged)
        engine.layoutChanged.connect(self._check_channel)
        # Virtual PipeWire devices (plug button): their state is read every 2 s while the section is shown,
        # and once at start, which also puts back links that went missing (pwvirtual.py).
        self.vd = engine.backend.virtual_devices()
        self._vd_snap = None
        self._vd_states: dict[str, tuple[str, str]] = {}
        self._vd_busy = False
        self._vd_timer = QTimer(self, interval=2000, timeout=self.refreshVirtual)
        self._virtualRefreshed.connect(self._on_virtual_refreshed)
        for sig in (engine.layoutChanged, engine.namesChanged):    # channel names follow speed, boards, names
            sig.connect(self.virtualChanged)
            sig.connect(self.virtualTargetsChanged)
        QTimer.singleShot(0, self.refreshVirtual)
        # MIDI remote control (MIDI button): app-wide, next to the app's settings file (core/midi.py).
        midi_path = os.path.join(os.path.dirname(os.path.abspath(self.app_settings.path)), "midi.json")
        self.midi = MidiControl(engine, engine.backend.midi_client(), midi_path, self)
        self.midi.stateChanged.connect(self.midiChanged)
        self.midi.activityChanged.connect(self.midiActivityChanged)
        self.midi.bindingsChanged.connect(self.midiChanged)          # the status says "off" only with bindings
        self.midi.learned.connect(lambda m: self.midiLearned.emit(
            {"message": {"note_on": "note", "cc": "cc", "program": "program", "pitchbend": "pitchbend"}.get(m.kind, ""),
             "channel": m.channel + 1, "number": m.number}))
        # OSC remote control (OSC button): the same, over UDP (core/osc.py).
        osc_path = os.path.join(os.path.dirname(os.path.abspath(self.app_settings.path)), "osc.json")
        self.osc = OscControl(engine, UdpTransport(self), osc_path, self)
        self.osc.stateChanged.connect(self.oscChanged)
        self.osc.activityChanged.connect(self.oscActivityChanged)
        self.osc.bindingsChanged.connect(self.oscChanged)
        self.osc.learned.connect(lambda m: self.oscLearned.emit({"address": m.address, "number": oscmod.number(m)}))
        self._midi_targets: dict = {}
        self._midi_bindings: list = []
        self._osc_targets: dict = {}
        self._osc_bindings: list = []
        self._keys_targets: dict = {}
        self._keys_bindings: list = []
        # Keyboard shortcuts (keyboard button): KDE Plasma's global shortcuts (core/shortcuts.py); no feedback.
        keys_path = os.path.join(os.path.dirname(os.path.abspath(self.app_settings.path)), "shortcuts.json")
        self.keys = ShortcutControl(engine, engine.backend.global_shortcuts(self), keys_path, names=self._key_name,
                                    parent=self)
        self.keys.stateChanged.connect(self.keysChanged)
        self.keys.activityChanged.connect(self.keysActivityChanged)
        self.keys.bindingsChanged.connect(self.keysChanged)          # the status says "off" only with bindings
        self._midi_timer = QTimer(self, singleShot=True, interval=0, timeout=self._update_remote_views)
        for sig in (engine.layoutChanged, engine.namesChanged, engine.routingChanged, engine.presetsChanged,
                    self.midi.bindingsChanged, self.osc.bindingsChanged, self.keys.bindingsChanged):
            sig.connect(self._midi_timer.start)
        self.midi.bindingsChanged.connect(self._update_remote_views)   # at once: the panel shows it now
        self.osc.bindingsChanged.connect(self._update_remote_views)
        self.keys.bindingsChanged.connect(self._update_remote_views)
        self._update_remote_views()

    # ------------------------------------------------------------------ info bar
    @Property(str, notify=infoChanged)
    def driverText(self):
        i = self.e.info
        return f"{i.driver_label}  ·  {i.driver_version}"

    @Property(str, notify=infoChanged)
    def cardText(self):
        i = self.e.info
        return f"{i.name}  ·  {i.card_version}  ·  S/N {i.serial}"

    @Property(str, notify=infoChanged)
    def rateText(self):
        return f"{self.rateName}  ·  {self.rateTip}"

    # The info bar shows the names only; the rest is in each one's tooltip.
    @Property(str, notify=infoChanged)
    def driverName(self):
        return self.e.info.driver_label

    @Property(str, notify=infoChanged)
    def driverTip(self):
        return f"Version: {self.e.info.driver_version}"

    @Property(str, notify=infoChanged)
    def cardName(self):
        return self.e.info.name

    @Property(str, notify=infoChanged)
    def cardTip(self):
        i = self.e.info
        unknown = lambda v: "unknown" if v is None else v
        return (f"Revision: {unknown(i.pci_revision)}\nFirmware build: {unknown(i.firmware_build)}"
                f"\nSerial number: {i.serial or 'unknown'}")

    @Property(str, notify=infoChanged)
    def rateName(self):
        return f"{self.e.rate:,} Hz".replace(",", " ") if self.e.rate else "rate unknown"

    @Property(str, notify=infoChanged)
    def rateTip(self):
        return SPEED_NAMES[self.e.speed] if self.e.speed < 3 else "Speed unknown"

    @Property(str, notify=infoChanged)
    def errorText(self):
        return self.e.last_error

    @Property(str, constant=True)
    def appName(self):
        return APP_NAME

    @Property(str, constant=True)
    def appVersion(self):
        return __version__

    # ------------------------------------------------------------------ units
    def _unit_dict(self, u: Unit) -> dict:
        first = u.channels[0]
        return {"key": u.key, "kind": u.kind, "kindLabel": KIND_LABEL[u.kind], "hwName": u.hw_name,
                "userName": self.e.memory.name_of(u.kind, first.hw), "stereo": u.stereo,
                "pairable": u.stereo or self.e.can_pair(u.kind, first.hw), "hw": first.hw, "pair": first.pair,
                "loopback": u.kind == OUT and first.hw in self.e.memory.loopback,
                "outLevelText": format_db(self.e.output_level_db(first.hw)) if u.kind == OUT else "0.0",
                "outLevelSet": u.kind == OUT and self.e.output_level_db(first.hw) != 0,
                "muted": self.e.unit_muted(u.key),
                "testing": u.kind == PB and self.e.unit_testing(u.key),
                **self._unit_settings(u)}

    def _unit_settings(self, u: Unit) -> dict:
        """Card settings of a unit, grouped: one submenu and one edge tag per group (card_settings.py).
        settingMenus: [{title, entries: [{header, text, id, name, checked}]}]; settingTags: [str]."""
        groups: dict[str, list] = {}
        for s in self.e.unit_settings(u):
            groups.setdefault(s.menu_title, []).append(s)
        menus, tags = [], []
        for title, ss in groups.items():
            entries = []
            for s in ss:
                value = self.e.card_setting(s.id)
                if len(ss) > 1:
                    entries.append({"header": True, "text": s.section or s.label, "id": "", "name": "",
                                    "checked": False})
                for c in self.e.setting_choices(s.id):
                    entries.append({"header": False, "text": s.choice_text(c), "id": s.id, "name": c,
                                    "checked": c == value})
            menus.append({"title": title, "entries": entries})
            if ss[0].group_tag is not None:
                tags.append(ss[0].group_tag({s.id: self.e.card_setting(s.id) for s in ss}))
            else:
                tags.append(" · ".join(s.tag(self.e.card_setting(s.id)) for s in ss))
        return {"settingMenus": menus, "settingTags": tags}

    @Property("QVariantList", notify=unitsChanged)
    def inputs(self):
        return [self._unit_dict(u) for u in self.e.units(IN)]

    @Property("QVariantList", notify=unitsChanged)
    def playbacks(self):
        return [self._unit_dict(u) for u in self.e.units(PB)]

    @Property("QVariantList", notify=unitsChanged)
    def outputs(self):
        return [self._unit_dict(u) for u in self.e.units(OUT)]

    # ------------------------------------------------------------------ routing view
    @Property("QVariantList", notify=routingChanged)
    def connections(self):
        return [self._conn_dict(c) for c in self.e.connections()]

    @Property("QVariantList", notify=routingChanged)
    def usedSources(self):
        used = {c.src.key for c in self.e.connections()}
        return [self._unit_dict(u) for u in self.e.units(IN) + self.e.units(PB) if u.key in used]

    @Property("QVariantList", notify=routingChanged)
    def usedOutputs(self):
        used = {c.dst.key for c in self.e.connections()}
        return [self._unit_dict(u) for u in self.e.units(OUT) if u.key in used]

    @Slot(str, str, str, result=bool)
    def setGain(self, src_key, dst_key, text):
        db = parse_db(text)
        if db is None:
            self.notify.emit("warning", f"Not a level: '{text}'. Use dB like -6, 0, +3.5, or -inf to remove.")
            return False
        return self.e.set_connection_db(src_key, dst_key, db)

    @Slot(str, str, float, result=bool)
    def nudgeGain(self, src_key, dst_key, delta_db):
        return self.e.nudge_connection(src_key, dst_key, delta_db)

    @Slot(str, str)
    def removeConnection(self, src_key, dst_key):
        self.e.remove_connection(src_key, dst_key)

    @Slot(str, "QVariantList", str, result=int)
    def addConnections(self, src_key, dst_keys, text):
        db = parse_db(text)
        if db is None or db == -math.inf:
            self.notify.emit("warning", "Pick a level above -inf for a new connection.")
            return 0
        return sum(1 for d in dst_keys if self.e.set_connection_db(src_key, d, db))

    @Slot(str, result=str)
    def gainOf(self, key_pair):
        src, dst = key_pair.split(">")
        return format_db(self.e.connection_db(src, dst))

    @Slot(str, str, result=bool)
    def setOutputLevel(self, dst_key, text):
        db = parse_db(text)
        if db is None:
            self.notify.emit("warning", f"Not a level: '{text}'. Use dB like -6, 0, +3, or -inf to mute.")
            return False
        return self.e.set_output_level(dst_key, db)

    @Slot(str)
    def removeAllTo(self, dst_key):
        for c in list(self.e.connections()):
            if c.dst.key == dst_key:
                self.e.remove_connection(c.src.key, c.dst.key)

    # ------------------------------------------------------------------ mutes
    @Slot(str, result=bool)
    def toggleMute(self, unit_key):
        return self.e.toggle_unit_mute(unit_key)

    # ------------------------------------------------------------------ channel test
    @Slot(str, result=bool)
    def testChannel(self, unit_key):
        """Start the test signal on a playback channel, or stop it if it's playing there."""
        return self.e.test_channel(unit_key)

    @Slot(str, str, result=bool)
    def toggleConnectionMute(self, src_key, dst_key):
        return self.e.toggle_connection_mute(src_key, dst_key)

    # ------------------------------------------------------------------ card settings
    @Slot(str, str, result=bool)
    def setCardSetting(self, sid, value):
        return self.e.set_card_setting(sid, value)

    @Property("QVariantList", notify=unitsChanged)
    def cardSettingLabels(self):
        # Linked settings (ADAT on/off) are stored through what they follow, so they get no entry here;
        # settings kept out of presets (side panel) neither.
        return list(dict.fromkeys(s.menu_title for s in self.e.card_settings() if not s.linked and s.in_presets))

    @Property("QVariantList", notify=unitsChanged)
    def panelSettings(self):
        """Side-panel settings: [{id, label, description, value, choices, texts, available, note}].
        texts = menu text per choice. Settings the driver lacks come last, with available = False."""
        out = []
        for s in self.e.panel_settings():
            choices = list(self.e.setting_choices(s.id))
            out.append({"id": s.id, "label": s.label, "description": s.description,
                        "value": self.e.card_setting(s.id) or "", "choices": choices,
                        "texts": [s.choice_text(c) for c in choices], "available": True, "note": ""})
        for s in self.e.panel_settings_missing():
            out.append({"id": s.id, "label": s.label, "description": s.description, "value": "",
                        "choices": list(s.choices), "texts": list(s.choices), "available": False,
                        "note": f"Not available with {self.e.info.driver_label}."})
        return out

    @Property(str, constant=True)
    def cardModel(self):
        return self.e.info.model

    # ------------------------------------------------------------------ virtual PipeWire devices (plug button)
    @Property(bool, notify=settingsPanelChanged)
    def pipewirePanelOpen(self):
        return self._panel == "pipewire"

    @pipewirePanelOpen.setter
    def pipewirePanelOpen(self, on):
        self._show_panel("pipewire", on)

    @Slot()
    def refreshVirtual(self):
        """Reads the devices' state from PipeWire and systemd, in a thread unless simulated."""
        if self._vd_busy:
            return
        self._vd_busy = True
        card = self.e.info.index

        def work():
            try:
                snap, states = self.vd.status(card)
            except Exception as e:                  # noqa: BLE001 - shown, not fatal
                snap, states = None, {"": ("error", str(e))}
            self._virtualRefreshed.emit(snap, states)
        if isinstance(self.e.info.index, int) and self.e.info.index < 0:
            work()                                  # the simulated card: quick, and tests stay in step
        else:
            threading.Thread(target=work, daemon=True, name="virtual-devices").start()

    def _on_virtual_refreshed(self, snap, states):
        self._vd_busy = False
        old = self._vd_snap.targets if self._vd_snap else None
        if snap is not None:
            self._vd_snap = snap
        self._vd_states = states
        self.virtualChanged.emit()
        if (self._vd_snap.targets if self._vd_snap else None) != old:
            self.virtualTargetsChanged.emit()

    @Property(str, notify=virtualTargetsChanged)
    def virtualNote(self):
        """Why virtual devices can't be made now, or ""."""
        missing = self.vd.available()
        if missing:
            return "Virtual devices need PipeWire and systemd: " + missing
        if self._vd_snap is None:
            return "Reading PipeWire…"
        if not self._vd_snap.targets:
            return ("The card isn't a PipeWire device now. Pick a profile for it in the desktop's sound "
                    "settings (Pro Audio for the AIO Pro).")
        return ""

    def _channel_label(self, direction: str, pos: str, count: int) -> str:
        """e.g. "AES L · Monitors (AUX2)": the card channel behind a PipeWire channel, when its number fits
        the driver's channel order at this speed."""
        kind = PB if direction == OUTPUT else IN
        order = pcm_order(self.e.speed, self.e.ao4s, kind, self.e.ai4s)
        if not (pos.startswith("AUX") and pos[3:].isdigit() and count == len(order)):
            return pos
        hw = order[int(pos[3:])]
        hw_name = next((c.hw_name for c in layout(kind, self.e.speed, self.e.ai4s, self.e.ao4s) if c.hw == hw), pos)
        custom = (self.e.memory.names.get(kind) or {}).get(hw, "")
        return f"{hw_name}{' · ' + custom if custom else ''} ({pos})"

    @Property("QVariantMap", notify=virtualTargetsChanged)
    def virtualTargets(self):
        """{"output"/"input": {"available", "node", "channels": [{"value": "AUX2", "text": label}]}}."""
        out = {}
        for d in DIRECTIONS:
            t = self._vd_snap.targets.get(d) if self._vd_snap else None
            pos = t.positions if t else []
            out[d] = {"available": t is not None, "node": t.node if t else "",
                      "channels": [{"value": p, "text": self._channel_label(d, p, len(pos))} for p in pos]}
        return out

    @Property("QVariantList", notify=virtualChanged)
    def virtualDevices(self):
        """The devices made so far, inputs first, then outputs, each by name."""
        out = []
        for dev in self.vd.devices():
            state, text = self._vd_states.get(dev.id, ("starting", "Checking…"))
            t = self._vd_snap.targets.get(dev.direction) if self._vd_snap else None
            count = len(t.positions) if t and t.node == dev.target else 0
            labels = [self._channel_label(dev.direction, c, count) for c in dev.channels]
            out.append({"id": dev.id, "name": dev.name, "direction": dev.direction,
                        "kindText": "VIRTUAL " + dev.direction.upper(),
                        "left": labels[0], "right": labels[1], "nodeName": dev.node_name,
                        "target": dev.target, "state": state, "stateText": text})
        return out

    @Slot(str, str, str, str, result=str)
    def createVirtualDevice(self, direction, name, left, right):
        """Makes a virtual device and starts it. Returns "" or what's wrong (the device isn't made then)."""
        missing = self.vd.available()
        if missing:
            return "Virtual devices need PipeWire and systemd: " + missing
        target = self._vd_snap.targets.get(direction) if self._vd_snap else None
        try:
            dev = self.vd.create(name, direction, target, [left, right])
        except (ValueError, OSError) as e:
            return str(e)
        self._vd_states[dev.id] = ("starting", "Starting…")
        self.virtualChanged.emit()

        def work():                         # the 2 s refresh shows when it runs
            err = self.vd.start(dev)
            if err:
                self.notify.emit("error", f'Virtual {direction} "{dev.name}": {err}')
        if self.e.info.index < 0:
            work()
            self.refreshVirtual()
        else:
            threading.Thread(target=work, daemon=True, name="virtual-device-start").start()
        return ""

    @Slot(str)
    def removeVirtualDevice(self, dev_id):
        dev = self.vd.get(dev_id)
        if dev is None:
            return
        err = self.vd.remove(dev_id)
        self._vd_states.pop(dev_id, None)
        if err:
            self.notify.emit("warning", f'Virtual {dev.direction} "{dev.name}" removed, but: {err}')
        self.virtualChanged.emit()

    # ------------------------------------------------------------------ MIDI remote control (MIDI button)
    @Property(bool, notify=settingsPanelChanged)
    def midiPanelOpen(self):
        return self._panel == "midi"

    @midiPanelOpen.setter
    def midiPanelOpen(self, on):
        self._show_panel("midi", on)

    @Property(bool, notify=midiChanged)
    def midiEnabled(self):
        return self.midi.enabled

    @midiEnabled.setter
    def midiEnabled(self, on):
        self.midi.set_enabled(on)

    @staticmethod
    def _port_text(name: str) -> str:
        client, _, port = name.partition(":")
        return port if port.startswith(client) else f"{client} · {port}"

    def _midi_ports(self, is_input: bool) -> list:
        chosen = self.midi.settings.input if is_input else self.midi.settings.output
        out = [{"value": p.name, "text": p.text} for p in self.midi.ports if (p.readable if is_input else p.writable)]
        if chosen and all(o["value"] != chosen for o in out):
            out.append({"value": chosen, "text": self._port_text(chosen)
                        + (" (not plugged in)" if self.midi.enabled else "")})
        return [{"value": "", "text": "None"}] + _sorted_by_text(out)    # None first, then A to Z

    @Property("QVariantList", notify=midiChanged)
    def midiInputs(self):
        return self._midi_ports(True)

    @Property("QVariantList", notify=midiChanged)
    def midiOutputs(self):
        return self._midi_ports(False)

    @Property(str, notify=midiChanged)
    def midiInput(self):
        return self.midi.settings.input

    @Property(str, notify=midiChanged)
    def midiOutput(self):
        return self.midi.settings.output

    @Slot(str)
    def setMidiInput(self, name):
        self.midi.set_port(name, True)

    @Slot(str)
    def setMidiOutput(self, name):
        self.midi.set_port(name, False)

    @Property(str, notify=midiChanged)
    def midiStatus(self):
        m = self.midi
        if not m.enabled:                           # with nothing to switch off, it needs no saying
            return "MIDI control is off, the bindings below are inactive." if m.bindings() else ""
        if m.error:
            return m.error
        s = m.settings
        ins = {"none": "No input chosen.", "connected": "",
               "waiting": f"Waiting for {self._port_text(s.input)} to be plugged in.", "off": ""}
        outs = {"none": "No feedback output.",
                "connected": "",
                "waiting": f"Waiting for {self._port_text(s.output)} to be plugged in.", "off": ""}
        return " ".join(t for t in (ins[m.port_state(True)], outs[m.port_state(False)]) if t)

    @Property(str, notify=midiActivityChanged)
    def midiActivity(self):
        """The last message received, for the panel's "Last received:" box."""
        return self.midi.last or "Nothing yet"

    @Property("QVariantMap", constant=True)
    def midiChoices(self):
        """Drop-down entries: whats, messages per what, modes, channels; value + text each."""
        msg_text = {"note": "Note", "cc": "Controller (CC)", "program": "Program change", "pitchbend": "Pitch bend"}
        return {"whats": [{"value": w, "text": {MUTE: "Mute", LEVEL: "Level", PRESET: "Preset recall"}[w]}
                          for w in WHATS],
                "messages": {w: [{"value": m, "text": msg_text[m]} for m in MESSAGES[w]] for w in WHATS},
                "modes": [{"value": TOGGLE, "text": "Toggle on each press"},
                          {"value": PRESS_MUTE, "text": "Mute on each press"},
                          {"value": PRESS_UNMUTE, "text": "Unmute on each press"},
                          {"value": FOLLOW, "text": "Mute while held"}],
                "channels": [{"value": 0, "text": "Any"}] + [{"value": c, "text": str(c)} for c in range(1, 17)],
                "dbFloor": DB_FLOOR}

    def _unit_text(self, u: Unit, kind_label: bool = True) -> str:
        name = self.e.memory.name_of(u.kind, u.channels[0].hw)
        return (f"{KIND_LABEL[u.kind]} " if kind_label else "") + u.hw_name + (f" · {name}" if name else "")

    def _norm_spec(self, spec: str) -> str:
        """A binding's target as the drop-down has it now: "out:7" is "out:6" while 6+7 is a stereo pair."""
        if ">" in spec:
            return ">".join(self._norm_spec(x) for x in spec.split(">", 1))
        key = self.midi._unit_key(spec)
        return key or spec

    def _spec_text(self, spec: str) -> str:
        """A target that isn't present now, by its channels' names."""
        if ">" in spec:
            return " → ".join(self._spec_text(x).split(" ", 1)[-1] for x in spec.split(">", 1))
        kind, _, hw = spec.partition(":")
        ch = next((c for c in layout(kind, 0, True, True) if str(c.hw) == hw), None)
        return f"{KIND_LABEL.get(kind, kind)} {ch.hw_name if ch else hw}"

    def _compute_midi_targets(self, by_number: tuple | None = ("Any preset: the program or CC value picks it (0 = preset 01)",
                                                               "By number (program, or CC value)"),
                              one_preset: str = "One preset: the message always recalls it") -> dict:
        """Each "what"'s drop-down: groups, each under a header item ({header: true, text}) that can't be chosen.
        by_number: the "any preset" group's header and its one item (MIDI's; OSC has its own; None: no group);
        one_preset: the other presets' header."""
        def group(title, items):     # each group sorted by its text
            items = _sorted_by_text(items)
            return ([{"value": "", "text": title, "header": True}] + items) if items else []
        units = {k: [{"value": u.key, "text": self._unit_text(u)} for u in self.e.units(k)] for k in (IN, PB, OUT)}
        conns = [{"value": c.key, "text": f"{self._unit_text(c.src)} → {self._unit_text(c.dst, False)}"}
                 for c in self.e.connections()]
        presets = [{"value": f"preset:{i}", "text": f"Preset {i + 1:02d} · {p.name}" if p else f"Preset {i + 1:02d} (empty)"}
                   for i, p in enumerate(self.e.presets())]
        return {
            MUTE: group("Hardware inputs", units[IN])
            + group("Playback channels (from software)", units[PB])
            + group("Hardware outputs", units[OUT])
            + group("Connections (one source into one output)", conns),
            LEVEL: group("Hardware output levels", units[OUT])
            + group("Connection levels (one source into one output)", conns),
            PRESET: (group(by_number[0], [{"value": PRESET_BY_NUMBER, "text": by_number[1]}]) if by_number else [])
            + group(one_preset, presets),
        }

    def _binding_dict(self, b: Binding, targets: dict) -> dict:
        value = b.target if b.what == PRESET else self._norm_spec(b.target)
        lst = targets.get(b.what, [])
        idx = next((i for i, t in enumerate(lst) if t["value"] == value and not t.get("header")), -1)
        missing = idx < 0
        text = lst[idx]["text"] if idx >= 0 else self._spec_text(b.target)
        return {"id": b.id, "what": b.what, "target": value, "message": b.message, "channel": b.channel,
                "number": b.number, "invert": b.invert, "mode": b.mode,
                "dbLo": DB_FLOOR - 1 if b.db_lo == -math.inf else round(b.db_lo),
                "dbHi": round(b.db_hi),
                "whatText": {MUTE: "MUTE", LEVEL: "LEVEL", PRESET: "PRESET RECALL"}[b.what],
                "targetText": text, "missing": missing,
                "messageText": message_text(b), "behaviourText": behaviour_text(b),
                "_order": (WHATS.index(b.what), idx if idx >= 0 else 10 ** 6, b.message, b.channel, b.number, b.id)}

    def _osc_binding_dict(self, b: OscBinding, targets: dict) -> dict:
        value = b.target if b.what == PRESET else self._norm_spec(b.target)
        lst = targets.get(b.what, [])
        idx = next((i for i, t in enumerate(lst) if t["value"] == value and not t.get("header")), -1)
        text = lst[idx]["text"] if idx >= 0 else self._spec_text(b.target)
        return {"id": b.id, "what": b.what, "target": value, "address": b.address, "value": b.value,
                "invert": b.invert, "mode": b.mode,
                "dbLo": DB_FLOOR - 1 if b.db_lo == -math.inf else round(b.db_lo), "dbHi": round(b.db_hi),
                "whatText": {MUTE: "MUTE", LEVEL: "LEVEL", PRESET: "PRESET RECALL"}[b.what],
                "targetText": text, "missing": idx < 0,
                "messageText": oscmod.message_text(b), "behaviourText": oscmod.behaviour_text(b),
                "_order": (WHATS.index(b.what), idx if idx >= 0 else 10 ** 6, b.address, b.id)}

    def _key_name(self, b: KeyBinding) -> str:
        """A shortcut binding's name in System Settings, e.g. "Mute or unmute: Input AN 1/2"."""
        value = b.target if b.what == PRESET else self._norm_spec(b.target)
        text = next((t["text"] for t in self._keys_targets.get(b.what, [])
                     if not t.get("header") and t["value"] == value), None)
        if text is None:
            text = (f"Preset {int(b.target.split(':')[1]) + 1:02d}" if b.what == PRESET else self._spec_text(b.target))
        return keysmod.friendly(b, text)

    def _keys_binding_dict(self, b: KeyBinding, targets: dict) -> dict:
        value = b.target if b.what == PRESET else self._norm_spec(b.target)
        lst = targets.get(b.what, [])
        idx = next((i for i, t in enumerate(lst) if t["value"] == value and not t.get("header")), -1)
        text = lst[idx]["text"] if idx >= 0 else self._spec_text(b.target)
        return {"id": b.id, "what": b.what, "target": value, "key": b.key, "keyText": keysmod.shown(b.key),
                "invert": b.invert, "mode": b.mode, "action": b.action, "step": b.step,
                "level": DB_FLOOR - 1 if b.level == -math.inf else round(b.level),
                "dbLo": DB_FLOOR - 1 if b.db_lo == -math.inf else round(b.db_lo), "dbHi": round(b.db_hi),
                "whatText": {MUTE: "MUTE", LEVEL: "LEVEL", PRESET: "PRESET RECALL"}[b.what],
                "targetText": text, "missing": idx < 0,
                "messageText": keysmod.message_text(b), "behaviourText": keysmod.behaviour_text(b),
                "_order": (WHATS.index(b.what), idx if idx >= 0 else 10 ** 6, b.key, b.id)}

    def _update_remote_views(self):
        """The MIDI, OSC and keyboard shortcut panels' target lists and bindings; signals only what changed."""
        def sort(dicts):
            out = sorted(dicts, key=lambda d: d["_order"])
            for d in out:
                del d["_order"]
            return out
        targets = self._compute_midi_targets()
        bindings = sort(self._binding_dict(b, targets) for b in self.midi.bindings())
        if targets != self._midi_targets:
            self._midi_targets = targets
            self.midiTargetsChanged.emit()
        if bindings != self._midi_bindings:
            self._midi_bindings = bindings
            self.midiBindingsChanged.emit()
        targets = self._compute_midi_targets(("Any preset: the value picks it (1 = preset 01)",
                                              "By number (the value)"))
        bindings = sort(self._osc_binding_dict(b, targets) for b in self.osc.bindings())
        if targets != self._osc_targets:
            self._osc_targets = targets
            self.oscTargetsChanged.emit()
        if bindings != self._osc_bindings:
            self._osc_bindings = bindings
            self.oscBindingsChanged.emit()
        targets = self._compute_midi_targets(None, "Presets")
        if targets != self._keys_targets:
            self._keys_targets = targets
            self.keysTargetsChanged.emit()
        bindings = sort(self._keys_binding_dict(b, targets) for b in self.keys.bindings())
        if bindings != self._keys_bindings:
            self._keys_bindings = bindings
            self.keysBindingsChanged.emit()
        self.keys.rename_all()                    # System Settings shows the names: where they changed

    @Property("QVariantMap", notify=midiTargetsChanged)
    def midiTargets(self):
        """{"mute"/"level"/"preset": [{value, text}, with {header: true, text} items heading each group]}."""
        return self._midi_targets

    @Property("QVariantList", notify=midiBindingsChanged)
    def midiBindings(self):
        """The saved bindings: mutes, then levels, then presets, each in the order of their targets."""
        return self._midi_bindings

    @Slot("QVariantMap", result=str)
    def saveMidiBinding(self, d):
        """Adds (id "") or replaces a binding. Returns "" or what's wrong (nothing is saved then)."""
        try:
            lo = int(d.get("dbLo", DB_FLOOR - 1))
            b = Binding(id=str(d.get("id") or ""), what=str(d.get("what", "")), target=str(d.get("target") or ""),
                        message=str(d.get("message", "")), channel=int(d.get("channel", 0)),
                        number=int(d.get("number", 0)), invert=bool(d.get("invert", False)),
                        mode=d.get("mode") if d.get("mode") in MODES else TOGGLE,
                        db_lo=-math.inf if lo < DB_FLOOR else float(lo), db_hi=float(int(d.get("dbHi", 0))))
        except (TypeError, ValueError):
            return "Something in this binding isn't a number."
        return self.midi.save_binding(b)

    @Slot(str)
    def removeMidiBinding(self, bid):
        self.midi.remove_binding(bid)

    @Slot(bool)
    def midiLearn(self, on):
        """Learn: the next note, CC or program change received is given to midiLearned, not acted on."""
        self.midi.set_learning(on)

    @Property(bool, notify=midiChanged)
    def midiCanLearn(self):
        return self.midi.enabled and self.midi.seq.is_open

    # ------------------------------------------------------------------ OSC remote control (OSC button)
    @Property(bool, notify=settingsPanelChanged)
    def oscPanelOpen(self):
        return self._panel == "osc"

    @oscPanelOpen.setter
    def oscPanelOpen(self, on):
        self._show_panel("osc", on)

    @Property(bool, notify=oscChanged)
    def oscEnabled(self):
        return self.osc.enabled

    @oscEnabled.setter
    def oscEnabled(self, on):
        self.osc.set_enabled(on)

    @Property(int, notify=oscChanged)
    def oscListenPort(self):
        return self.osc.settings.listen_port

    @Property(bool, notify=oscChanged)
    def oscListenNetwork(self):
        return self.osc.settings.listen_network

    @Property(str, notify=oscChanged)
    def oscSendHost(self):
        return self.osc.settings.send_host

    @Property(int, notify=oscChanged)
    def oscSendPort(self):
        return self.osc.settings.send_port

    @Slot(int, bool)
    def setOscListen(self, port, network):
        """The UDP port listened on, and whether other computers may send to it (else only this one)."""
        self.osc.set_listen(port, network)

    @Slot(str, int)
    def setOscSend(self, host, port):
        """Where feedback goes: a host name or address ("" = nowhere), and its UDP port."""
        self.osc.set_send(host, port)

    @Property(str, notify=oscChanged)
    def oscStatus(self):
        o, s = self.osc, self.osc.settings
        if not o.enabled:                           # with nothing to switch off, it needs no saying
            return "OSC control is off, the bindings below are inactive." if o.bindings() else ""
        if o.error:
            return o.error
        parts = []
        if s.listen_network:
            addrs = oscmod.local_addresses()
            parts.append(f"Controllers send to {' or '.join(addrs) if addrs else 'this computer'}, "
                         f"port {o.listening_port}. Anyone on the network can.")
        if not s.send_host:
            parts.append("No feedback host.")
        elif o.dest_error:
            parts.append(o.dest_error)
        elif o.dest is None:
            parts.append(f"Looking up {s.send_host}…")
        return " ".join(parts)

    @Property(str, notify=oscActivityChanged)
    def oscActivity(self):
        """The last message received, for the panel's "Last received:" box."""
        return self.osc.last or "Nothing yet"

    @Property("QVariantMap", constant=True)
    def oscChoices(self):
        """Drop-down entries: whats, level values, modes; value + text each."""
        return {"whats": [{"value": w, "text": {MUTE: "Mute", LEVEL: "Level", PRESET: "Preset recall"}[w]}
                          for w in WHATS],
                "values": [{"value": oscmod.POSITION, "text": "Position, 0 to 1"},
                           {"value": oscmod.DB, "text": "Level in dB"}],
                "modes": [{"value": TOGGLE, "text": "Toggle on each press"},
                          {"value": PRESS_MUTE, "text": "Mute on each press"},
                          {"value": PRESS_UNMUTE, "text": "Unmute on each press"},
                          {"value": FOLLOW, "text": "Mute while held"}],
                "scopes": [{"value": False, "text": "This computer only"},
                           {"value": True, "text": "The network"}],
                "dbFloor": DB_FLOOR}

    @Property("QVariantMap", notify=oscTargetsChanged)
    def oscTargets(self):
        """As midiTargets, with OSC's "by number" texts."""
        return self._osc_targets

    @Property("QVariantList", notify=oscBindingsChanged)
    def oscBindings(self):
        return self._osc_bindings

    @Slot("QVariantMap", result=str)
    def saveOscBinding(self, d):
        """Adds (id "") or replaces a binding. Returns "" or what's wrong (nothing is saved then)."""
        try:
            lo = int(d.get("dbLo", DB_FLOOR - 1))
            b = OscBinding(id=str(d.get("id") or ""), what=str(d.get("what", "")), target=str(d.get("target") or ""),
                           address=str(d.get("address") or "").strip(),
                           value=d.get("value") if d.get("value") in oscmod.VALUES else oscmod.POSITION,
                           invert=bool(d.get("invert", False)),
                           mode=d.get("mode") if d.get("mode") in MODES else TOGGLE,
                           db_lo=-math.inf if lo < DB_FLOOR else float(lo), db_hi=float(int(d.get("dbHi", 0))))
        except (TypeError, ValueError):
            return "Something in this binding isn't a number."
        return self.osc.save_binding(b)

    @Slot(str)
    def removeOscBinding(self, bid):
        self.osc.remove_binding(bid)

    @Slot(bool)
    def oscLearn(self, on):
        """Learn: the next OSC message received is given to oscLearned, not acted on."""
        self.osc.set_learning(on)

    @Property(bool, notify=oscChanged)
    def oscCanLearn(self):
        return self.osc.enabled and self.osc.udp.is_open

    # ------------------------------------------------------------------ keyboard shortcuts (keyboard button)
    @Property(bool, notify=settingsPanelChanged)
    def keysPanelOpen(self):
        return self._panel == "keys"

    @keysPanelOpen.setter
    def keysPanelOpen(self, on):
        self._show_panel("keys", on)

    @Property(bool, notify=keysChanged)
    def keysEnabled(self):
        return self.keys.enabled

    @keysEnabled.setter
    def keysEnabled(self, on):
        self.keys.set_enabled(on)

    @Property(bool, notify=keysChanged)
    def keysAvailable(self):
        """Plasma's global shortcuts service is there."""
        return self.keys.plasma.available

    @Property(str, notify=keysChanged)
    def keysStatus(self):
        k = self.keys
        if k.error:
            return k.error
        if not k.enabled and k.bindings():          # with nothing to switch off, it needs no saying
            return "Keyboard shortcuts are off, the bindings below are inactive."
        return ""

    @Property(str, notify=keysActivityChanged)
    def keysActivity(self):
        """The last shortcut pressed or let go, for the panel's "Last received:" box."""
        return self.keys.last or "Nothing yet"

    @Property("QVariantMap", constant=True)
    def keysChoices(self):
        """Drop-down entries: whats, modes, level actions; value + text each; the step's and levels' limits."""
        return {"whats": [{"value": w, "text": {MUTE: "Mute", LEVEL: "Level", PRESET: "Preset recall"}[w]}
                          for w in WHATS],
                "modes": [{"value": TOGGLE, "text": "Toggle on each press"},
                          {"value": PRESS_MUTE, "text": "Mute on each press"},
                          {"value": PRESS_UNMUTE, "text": "Unmute on each press"},
                          {"value": FOLLOW, "text": "Mute while held"}],
                "actions": [{"value": keysmod.UP, "text": "Up a step on each press"},
                            {"value": keysmod.DOWN, "text": "Down a step on each press"},
                            {"value": keysmod.SET, "text": "Set a level on each press"}],
                "stepMin": keysmod.STEP_MIN, "stepMax": keysmod.STEP_MAX, "defaultStep": keysmod.DEFAULT_STEP,
                "dbFloor": DB_FLOOR}

    @Property("QVariantMap", notify=keysTargetsChanged)
    def keysTargets(self):
        """As midiTargets, without "by number" (a key has no value to pick a preset by)."""
        return self._keys_targets

    @Property("QVariantList", notify=keysBindingsChanged)
    def keysBindings(self):
        return self._keys_bindings

    @Slot("QVariantMap", result=str)
    def saveKeyBinding(self, d):
        """Adds (id "") or replaces a binding. Returns "" or what's wrong (nothing is saved then)."""
        try:
            lo, level = int(d.get("dbLo", DB_FLOOR - 1)), int(d.get("level", 0))
            b = KeyBinding(id=str(d.get("id") or ""), what=str(d.get("what", "")), target=str(d.get("target") or ""),
                           key=keysmod.key_text(keysmod.key_code(str(d.get("key") or ""))),
                           invert=bool(d.get("invert", False)),
                           mode=d.get("mode") if d.get("mode") in MODES else TOGGLE,
                           action=d.get("action") if d.get("action") in keysmod.ACTIONS else keysmod.UP,
                           step=max(keysmod.STEP_MIN, min(keysmod.STEP_MAX, int(d.get("step", keysmod.DEFAULT_STEP)))),
                           level=-math.inf if level < DB_FLOOR else float(level),
                           db_lo=-math.inf if lo < DB_FLOOR else float(lo), db_hi=float(int(d.get("dbHi", 0))))
        except (TypeError, ValueError):
            return "Something in this binding isn't a number."
        if b.what == MUTE and b.mode != FOLLOW:
            b.invert = False                       # only "while held" has an inverted form (there's no feedback)
        return self.keys.save_binding(b)

    @Slot(str)
    def removeKeyBinding(self, bid):
        self.keys.remove_binding(bid)

    @Slot(bool)
    def keysRecord(self, on):
        """Recording a key combination in the window: Plasma's own shortcuts don't act meanwhile."""
        self.keys.set_recording(on)

    @Slot(int, int, result="QVariantMap")
    def keyCombination(self, key, modifiers):
        """A key press in the window → {key: as saved, text: as shown, hint}; key and text are "" while it's only
        modifiers, or a key that needs Ctrl, Alt or Meta with it (hint then says so)."""
        k = keysmod.combination(key, modifiers)
        if k and keysmod.needs_modifier(key, modifiers):
            return {"key": "", "text": "", "hint": f"{keysmod.shown(k)} alone would be taken from every window: "
                                                   "hold Ctrl, Alt or Meta with it (F1-F12 and media keys may be "
                                                   "used alone)."}
        return {"key": k, "text": keysmod.shown(k), "hint": ""}

    # ------------------------------------------------------------------ app settings (cog button)
    @Property(bool, notify=settingsPanelChanged)
    def appSettingsOpen(self):
        return self._panel == "app"

    @appSettingsOpen.setter
    def appSettingsOpen(self, on):
        self._show_panel("app", on)

    @Property("QVariantList", constant=True)
    def startWindowChoices(self):
        return [{"value": v, "text": START_WINDOW_LABELS[v]} for v in START_WINDOW_CHOICES]

    @Property(str, notify=appSettingsChanged)
    def startWindow(self):
        return self.app_settings.start_window

    @Slot(str, result=bool)
    def setStartWindow(self, value):
        ok = self.app_settings.set_start_window(value)
        if ok:
            self.appSettingsChanged.emit()
        return ok

    @Property("QVariantList", constant=True)
    def colorModeChoices(self):
        return [{"value": v, "text": COLOR_MODE_LABELS[v]} for v in COLOR_MODE_CHOICES]

    @Property(str, notify=appSettingsChanged)
    def colorMode(self):
        return self.app_settings.color_mode

    @Slot(str, result=bool)
    def setColorMode(self, value):
        ok = self.app_settings.set_color_mode(value)
        if ok:
            self.appSettingsChanged.emit()
        return ok

    @Property(bool, notify=appSettingsChanged)
    def darkMode(self):
        """True = the app draws its dark theme (the Colour mode setting, or the desktop's scheme for System)."""
        return is_dark(self.app_settings.color_mode, system_is_dark())

    @Property("QVariantList", constant=True)
    def zoomChoices(self):
        return [{"value": v, "text": zoom_label(v)} for v in ZOOM_CHOICES]

    @Property(int, notify=appSettingsChanged)
    def zoom(self):
        return self.app_settings.zoom

    @Slot(int, result=bool)
    def setZoom(self, value):
        ok = self.app_settings.set_zoom(int(value))
        if ok:
            self.appSettingsChanged.emit()
        return ok

    @Property(bool, notify=appSettingsChanged)
    def zoomPending(self):
        """The zoom setting differs from the one this run started with: it applies after a restart."""
        return self.app_settings.zoom != self.running_zoom

    @Slot()
    def restartApp(self):
        self.restartRequested.emit()

    # ------------------------------------------------------------------ clock
    @Property(bool, notify=settingsPanelChanged)
    def clockPanelOpen(self):
        return self._panel == "clock"

    @clockPanelOpen.setter
    def clockPanelOpen(self, on):
        self._show_panel("clock", on)

    def set_window_shown(self, shown: bool):
        """Called by the shell when the window is shown, hidden to the tray or minimised."""
        self._window_shown = bool(shown)
        self._update_clock_polling()
        self.e.set_settings_polling(self._window_shown)    # card settings: re-read only while visible

    def _update_clock_polling(self):
        # The clock status is read every second only while someone can see it; so is the virtual devices'.
        self.e.set_clock_active(self._panel == "clock" and self._window_shown)
        if self._panel == "pipewire" and self._window_shown:
            if not self._vd_timer.isActive():
                self._vd_timer.start()
                self.refreshVirtual()
        else:
            self._vd_timer.stop()

    @Property(bool, notify=clockChanged)
    def clockAvailable(self):
        return self.e.clock is not None

    @staticmethod
    def _khz(hz) -> str:
        return f"{hz / 1000:g} kHz" if hz else ""

    @Property("QVariantList", notify=clockChanged)
    def clockSources(self):
        """Radio rows: [{key, label, available, checked, status, state, inUse}].
        state: "sync" | "lock" | "nolock" | "na" | "internal" (for colouring)."""
        from ..core.clock import SOURCE_LABELS, SOURCES
        st = self.e.clock
        if st is None:
            return []
        rows = []
        for key in SOURCES:
            if key == "internal":
                rows.append({"key": key, "label": SOURCE_LABELS[key], "available": True, "checked": st.master,
                             "status": self._khz(st.internal_rate), "state": "internal", "inUse": st.master})
                continue
            s = st.status.get(key)
            sync = s.sync if s else "N/A"
            state = {"Sync": "sync", "Lock": "lock", "No Lock": "nolock"}.get(sync, "na")
            text = {"sync": "Sync", "lock": "Lock", "nolock": "No signal", "na": "Not available"}[state]
            if state in ("sync", "lock") and s.rate:
                text += " · " + self._khz(s.rate)
            in_use = (not st.master and (st.current == key if st.current is not None
                                         else st.preferred == key and state in ("sync", "lock")))
            rows.append({"key": key, "label": SOURCE_LABELS[key], "available": key in st.sources,
                         "checked": not st.master and st.preferred == key, "status": text, "state": state,
                         "inUse": in_use})
        return rows

    @Property("QVariantList", notify=clockChanged)
    def clockRates(self):
        st = self.e.clock
        return [{"hz": r, "text": self._khz(r)} for r in st.rates] if st else []

    @Property(int, notify=clockChanged)
    def clockRate(self):
        st = self.e.clock
        return st.internal_rate or 0 if st else 0

    @Property(str, notify=clockChanged)
    def clockRunningText(self):
        rate = f"{self.e.rate:,} Hz".replace(",", " ") if self.e.rate else "unknown rate"
        st = self.e.clock
        src = "internal clock" if st is None or st.master else "AutoSync"
        return f"Running at {rate} · {src}"

    @Property("QVariantMap", notify=clockChanged)
    def clockTune(self):
        """{enabled, value, min, max, nominal, note}: fine tuning of the internal clock, in Hz."""
        st, rng = self.e.clock, self.e.tune_range()
        if st is None or rng is None:
            return {"enabled": False, "value": 0, "min": 0, "max": 1, "nominal": 0,
                    "note": "Not available with this driver."}
        return {"enabled": st.master, "value": round(st.tuned_rate or 0, 1), "min": rng[0], "max": rng[1],
                "nominal": st.internal_rate,
                "note": "" if st.master else "Only with the Internal clock source."}

    @Slot(str, result=bool)
    def setClockSource(self, key):
        return self.e.set_clock_source(key)

    @Slot(int, result=bool)
    def setClockRate(self, hz):
        return self.e.set_internal_rate(hz)

    @Slot(str, result=bool)
    def setTunedRate(self, text):
        try:
            hz = float(str(text).lower().replace("hz", "").replace(" ", "").replace(",", "."))
        except ValueError:
            self.notify.emit("warning", f"Not a sample rate: '{text}'. Use Hz, like 48000 or 47998.5.")
            return False
        rng = self.e.tune_range()
        if rng and not rng[0] <= hz <= rng[1]:
            self.notify.emit("warning", f"Tuning is limited to {rng[0]}–{rng[1]} Hz; clamped.")
        return self.e.set_tuned_rate(hz)

    @Slot(result=bool)
    def resetTune(self):
        st = self.e.clock
        return bool(st and st.internal_rate) and self.e.set_tuned_rate(st.internal_rate)

    @Property(bool, notify=settingsPanelChanged)
    def settingsPanelOpen(self):
        return self._panel == "card"

    @settingsPanelOpen.setter
    def settingsPanelOpen(self, on):
        self._show_panel("card", on)

    def _show_panel(self, name: str, on):
        """Open one side-panel section, which closes whichever was open, or close it (panel left empty)."""
        new = name if on else ("" if self._panel == name else self._panel)
        if new != self._panel:
            self._panel = new
            self._update_clock_polling()
            if new != "channel" and self._channel_levels:
                self._channel_levels = []
                self.channelLevelsChanged.emit()
            self.settingsPanelChanged.emit()

    # ------------------------------------------------------------------ channel section (a pill clicked)
    def _channel_unit(self) -> Unit | None:
        if self._channel is None:
            return None
        kind, hw = self._channel
        return next((u for u in self.e.units(kind) if any(c.hw == hw for c in u.channels)), None)

    def _check_channel(self):
        # A sample-rate change can take the channel away (ADAT channels at double and quad speed).
        if self._panel == "channel" and self._channel_unit() is None:
            self._show_panel("channel", False)

    @Slot(str)
    def showChannel(self, key):
        """A pill was clicked: show its channel in the side panel, or close it if it is shown already."""
        u = self.e.unit(key)
        if u is None:
            return
        if self._panel == "channel" and self._channel_unit() == u:
            self._show_panel("channel", False)
            return
        self._channel = (u.kind, u.channels[0].hw)
        self._show_panel("channel", True)
        self.channelChanged.emit()

    @Property(bool, notify=settingsPanelChanged)
    def channelOpen(self):
        return self._panel == "channel"

    @Property(str, notify=channelChanged)
    def channelKey(self):
        u = self._channel_unit()
        return u.key if u else ""

    def _conn_dict(self, c) -> dict:
        db = raw_to_db(c.gain)
        return {"key": c.key, "srcKey": c.src.key, "dstKey": c.dst.key, "srcKind": c.src.kind,
                "seq": self.e.connection_seq(c.key),
                "muted": self.e.connection_muted(c.src.key, c.dst.key),           # its own mute
                "silenced": self.e.connection_silenced(c.src.key, c.dst.key),     # source/output muted
                "gainText": format_db(db), "gainDb": db if db != -math.inf else -1000.0}

    @Property("QVariantMap", notify=channelChanged)
    def channel(self):
        """The channel shown in the side panel: its unit (as in `inputs`), plus
        sides: one meter label per channel ("L"/"R", "1"/"2"; "" for mono);
        outLevelDb: output level in dB (-1000 for -inf; 0 for sources);
        settingGroups: [{title, settings: [{id, label, value, choices, texts}]}] (card settings, label "" if alone);
        connections: [connection (as in `connections`) + otherKey, otherKind, otherName, otherUserName]."""
        u = self._channel_unit()
        if u is None:
            return {}
        d = self._unit_dict(u)
        d["sides"] = [c.side for c in u.channels] if u.stereo else [""]
        lvl = self.e.output_level_db(u.channels[0].hw) if u.kind == OUT else 0.0
        d["outLevelDb"] = lvl if lvl != -math.inf else -1000.0
        groups: dict[str, list] = {}
        for st in self.e.unit_settings(u):
            groups.setdefault(st.menu_title, []).append(st)
        d["settingGroups"] = [
            {"title": title,
             "settings": [{"id": st.id, "label": (st.section or st.label) if len(ss) > 1 else "",
                           "value": self.e.card_setting(st.id) or "",
                           "choices": list(self.e.setting_choices(st.id)),
                           "texts": [st.choice_text(c) for c in self.e.setting_choices(st.id)]} for st in ss]}
            for title, ss in groups.items()]
        conns = []
        for c in self.e.connections():
            if u.key not in (c.src.key, c.dst.key):
                continue
            other = c.dst if c.src.key == u.key else c.src
            cd = self._conn_dict(c)
            cd.update(otherKey=other.key, otherKind=other.kind, otherName=other.hw_name,
                      otherUserName=self.e.memory.name_of(other.kind, other.channels[0].hw))
            conns.append(cd)
        d["connections"] = conns
        return d

    @Property("QVariantList", notify=channelLevelsChanged)
    def channelLevels(self):
        """Peak per channel of the shown channel, in dBFS (-1000 = silence). Empty while meters are off."""
        return self._channel_levels

    @Slot()
    def clearRouting(self):
        self.e.clear_routing()

    @Slot()
    def resendAll(self):
        self.e.resend_all()
        self.notify.emit("info", "Whole mixer re-sent to the card.")

    # ------------------------------------------------------------------ names & modes
    @Slot(str, int, str)
    def rename(self, kind, hw, name):
        self.e.rename(kind, hw, name)

    @Slot(str, int, bool)
    def setStereo(self, kind, pair, on):
        self.e.set_stereo(kind, pair, on)

    @Property(bool, constant=True)
    def loopbackSupported(self):
        return self.e.loopback_supported

    # ------------------------------------------------------------------ presets
    @Property("QVariantList", notify=presetsChanged)
    def presetNames(self):
        return [p.name if p else "" for p in self.e.presets()]

    @Property(int, constant=True)
    def presetCount(self):
        return MAX_PRESETS

    @Slot(int, str)
    def storePreset(self, slot, name):
        self.e.store_preset(slot, name)
        self.notify.emit("info", f"Stored preset {slot + 1}: {name or f'Preset {slot + 1}'}")

    @Slot(int)
    def requestRecall(self, slot):
        """Ask the UI to confirm what to restore (names / destinations / levels)."""
        p = self.e.presets()[slot] if 0 <= slot < MAX_PRESETS else None
        if p is not None:
            self.recallRequested.emit(slot, p.name, p.names is not None)

    def offer_file_preset(self, preset):
        self._file_preset = preset
        self.recallRequested.emit(-1, preset.name, preset.names is not None)

    @Slot(int, bool, bool, bool, bool, bool, result=bool)
    def confirmRecall(self, slot, names, destinations, levels, mutes, card):
        if slot == -1:
            p = getattr(self, "_file_preset", None)
            if p is None:
                return False
            self.e.apply_preset(p, names, destinations, levels, mutes, card)
            self._file_preset = None
            self.notify.emit("info", f"Loaded preset '{p.name}' from file")
            return True
        return self.e.recall_preset(slot, names, destinations, levels, mutes, card)

    # ------------------------------------------------------------------ File > Export / Import Memory
    # Export writes everything; import offers each part (IMPORT_PARTS), and a part that's picked replaces
    # what the app has, while the rest stays. The file is the card's memory file plus a key per other part, so
    # files from before those parts existed still import (routing and card settings only).
    IMPORT_PARTS = (("routing", "Routing"), ("presets", "Presets"), ("card", "Card Settings"), ("clock", "Clock Settings"),
                    ("pipewire", "PipeWire Virtual Devices"), ("midi", "MIDI Remote Control"),
                    ("osc", "OSC Remote Control"), ("shortcuts", "Keyboard Shortcuts"))

    def export_all(self, path: str):
        data = self.e.memory.to_dict()
        clock = self.e.clock_export()
        if clock is not None:
            data["clock"] = clock
        data["pipewire"] = [{"name": d.name, "direction": d.direction, "channels": list(d.channels)}
                            for d in self.vd.devices()]
        data["midi"] = self.midi.export()
        data["osc"] = self.osc.export()
        data["shortcuts"] = self.keys.export()
        atomic_write_json(path, data)

    def offer_import(self, path: str, data: dict):
        """Checks the file (ValueError if it isn't a memory file) and asks which parts to import."""
        mem = Memory.from_dict(data)
        self._import = data
        labels = {s.id: s.label for s in settings_for(self.e.info.model)}
        n = lambda count, what: f"{count} {what}{'' if count == 1 else 's'}"
        dev_text = lambda d: f"{str(d.get('direction', '')).capitalize()}: {d.get('name', '')}"
        tips = {
            "routing": (True, "Channel names, mono/stereo, connections, levels and mutes"),
            "presets": (any(mem.presets), n(sum(1 for p in mem.presets if p), "preset")),
            "card": (bool(mem.card_settings), ", ".join(labels.get(k, k) for k in sorted(mem.card_settings))),
            "clock": (isinstance(data.get("clock"), dict), self._clock_tip(data.get("clock"))),
            "pipewire": (isinstance(data.get("pipewire"), list) and bool(data["pipewire"]),
                         "\n".join(dev_text(d) for d in data.get("pipewire") or [] if isinstance(d, dict))),
        }
        for part in ("midi", "osc", "shortcuts"):
            d = data.get(part)
            ok = isinstance(d, dict)
            binds = len(d.get("bindings") or []) if ok else 0
            tips[part] = (ok, f"{'On' if ok and d.get('enabled') else 'Off'}, {n(binds, 'binding')}")
        parts = [{"id": pid, "label": label, "available": tips[pid][0],
                  "tip": tips[pid][1] if tips[pid][0] else "Not in this file."} for pid, label in self.IMPORT_PARTS]
        presets = [{"slot": i, "text": f"{i + 1:02d}   {p.name}"} for i, p in enumerate(mem.presets) if p]
        self.importRequested.emit(os.path.basename(path), parts, presets)

    @staticmethod
    def _clock_tip(d) -> str:
        if not isinstance(d, dict):
            return ""
        src = {"internal": "Internal"}.get(str(d.get("source")), str(d.get("source", "")).upper())
        bits = [f"Source: {src}"]
        if d.get("internal_rate"):
            bits.append(f"{int(d['internal_rate']):,} Hz".replace(",", " "))
        if d.get("tuned_rate") and abs(float(d["tuned_rate"]) - float(d.get("internal_rate") or 0)) > 0.01:
            bits.append(f"tuned to {float(d['tuned_rate']):.1f} Hz")
        return ", ".join(bits)

    @Slot("QVariantList", int, int, result=bool)
    def confirmImport(self, picked, preset_from=-1, preset_to=-1):
        """Imports the parts picked (ids from IMPORT_PARTS) from the file offer_import() read. Presets:
        all the file's presets (preset_from -1), or its preset preset_from into this app's slot preset_to."""
        data = getattr(self, "_import", None)
        self._import = None
        picked = [str(p) for p in picked or []]
        if data is None or not picked:
            return False
        problems = []
        try:
            if {"routing", "presets", "card"} & set(picked):
                self.e.import_memory_parts(data, routing="routing" in picked, card="card" in picked,
                                           presets="presets" in picked, preset_from=preset_from, preset_to=preset_to)
        except ValueError as e:
            self.notify.emit("error", f"Import failed: {e}")
            return False
        if "clock" in picked:
            problems += [f"Clock: {p}" for p in self.e.clock_import(data.get("clock"))]
        if "pipewire" in picked:
            problems += self._import_virtual(data.get("pipewire") or [])
        for part, ctl in (("midi", self.midi), ("osc", self.osc), ("shortcuts", self.keys)):
            if part in picked and isinstance(data.get(part), dict):
                try:
                    ctl.replace(data[part])
                except OSError as e:
                    problems.append(f"{ctl.NAME}: {e}")
        names = [label for pid, label in self.IMPORT_PARTS if pid in picked]
        if "presets" in picked and preset_from >= 0:
            names[names.index("Presets")] = f"Preset {preset_from + 1} (into slot {preset_to + 1})"
        if problems:
            self.notify.emit("warning", "Imported " + ", ".join(names) + ", except: " + "; ".join(problems))
        else:
            self.notify.emit("info", "Imported " + ", ".join(names))
        return True

    def _import_virtual(self, items: list) -> list[str]:
        """The file's virtual devices replace the app's: identical ones are kept running, others removed, the
        rest made on this card's PipeWire devices."""
        missing = self.vd.available()
        if missing:
            return ["PipeWire virtual devices need PipeWire and systemd: " + missing]
        self._vd_snap = self.vd.snapshot(self.e.info.index)
        wanted = [(str(d.get("direction", "")), str(d.get("name", "")).strip(), [str(c) for c in d.get("channels") or []])
                  for d in items if isinstance(d, dict)]
        todo = list(wanted)
        for dev in self.vd.devices():
            t = self._vd_snap.targets.get(dev.direction)
            same = (dev.direction, dev.name, list(dev.channels))
            if same in todo and t is not None and dev.target == t.node:
                todo.remove(same)
            else:
                self.removeVirtualDevice(dev.id)
        problems = []
        for direction, name, chans in todo:
            err = self.createVirtualDevice(direction, name, *(chans + ["", ""])[:2]) if len(chans) == 2 \
                else "it needs two channels"
            if err:
                problems.append(f'Virtual {direction} "{name}": {err}')
        self.refreshVirtual()
        return problems

    @Slot(int)
    def deletePreset(self, slot):
        self.e.delete_preset(slot)

    # ------------------------------------------------------------------ meters
    @Property(bool, notify=metersEnabledChanged)
    def metersEnabled(self):
        return bool(self.e.memory.settings.get("meters", True))

    @metersEnabled.setter
    def metersEnabled(self, on):
        if on != self.metersEnabled:
            self.e.memory.settings["meters"] = on
            self.e._changed()
            self.metersEnabledChanged.emit()

    @Property(int, notify=meterRateChanged)
    def meterRate(self):
        return self.e.meter_hz

    @meterRate.setter
    def meterRate(self, hz):
        self.e.set_meter_hz(hz)

    @Property("QVariantList", constant=True)
    def meterRates(self):
        return list(METER_RATES_HZ)

    @Property(int, notify=hueStepChanged)
    def hueStep(self):
        return self.e.hue_step

    @hueStep.setter
    def hueStep(self, deg):
        self.e.set_hue_step(deg)

    @Property("QVariantMap", notify=levelsChanged)
    def levels(self):
        return self._levels

    def _on_frame(self, frame):
        lv = {}
        for kind in (IN, PB, OUT):
            for u in self.e.units(kind):
                db = linear_to_db(self.e.unit_level(frame, u))
                # Quantised to 0.25 dB so unchanged meters do not cause repaints.
                lv[u.key] = round(max(0.0, min(1.0, (db - METER_FLOOR_DB) / -METER_FLOOR_DB)) * 240) / 240 \
                    if db > METER_FLOOR_DB else 0.0
        if lv != self._levels:
            self._levels = lv
            self.levelsChanged.emit()
        u = self._channel_unit() if self._panel == "channel" else None
        if u is not None:
            arr = {IN: frame.inputs, PB: frame.playback, OUT: frame.outputs}[u.kind]
            # Rounded to 0.1 dB (the readout's precision); below the readout's floor counts as silence.
            ch = [round(db, 1) if db > -120 else -1000.0 for db in (linear_to_db(arr[c.hw]) for c in u.channels)]
            if ch != self._channel_levels:
                self._channel_levels = ch
                self.channelLevelsChanged.emit()

    def clear_levels(self):
        self._levels = {}
        self.levelsChanged.emit()
        self._channel_levels = []
        self.channelLevelsChanged.emit()


def system_is_dark() -> bool:
    """The desktop's colour scheme: Qt's own reading (KDE, GNOME, …), else the lightness of the system palette."""
    app = QGuiApplication.instance()
    if app is None:
        return True
    scheme = app.styleHints().colorScheme()
    if scheme != Qt.ColorScheme.Unknown:
        return scheme == Qt.ColorScheme.Dark
    return app.palette().window().color().lightnessF() < 0.5
