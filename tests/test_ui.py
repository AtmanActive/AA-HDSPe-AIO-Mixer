"""UI tests: the real QML (Main.qml) on a mock card, driven with simulated mouse and keyboard input.

Needs PySide6's QtTest (Debian: python3-pyside6.qttest); skipped without it. Runs offscreen (conftest.py).
"""
import os

import pytest

QtTest = pytest.importorskip("PySide6.QtTest")
from PySide6.QtCore import QPoint, QPointF, Qt                                   # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine                                 # noqa: E402
from PySide6.QtQuick import QQuickItem                                          # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle                                # noqa: E402
from PySide6.QtWidgets import QApplication                                      # noqa: E402

from aa_hdspe_mixer.core.app_settings import AppSettings                        # noqa: E402
from aa_hdspe_mixer.core.backend import MockBackend                             # noqa: E402
from aa_hdspe_mixer.core.engine import Engine                                   # noqa: E402
from aa_hdspe_mixer.ui.app import HERE, Shell                                   # noqa: E402
from aa_hdspe_mixer.ui.bridge import Bridge                                     # noqa: E402

QTest = QtTest.QTest


class Ui:
    """The app's window on a mock card, with helpers to find items and click on them."""
    styled = False

    def __init__(self, tmp_path):
        self.app = QApplication.instance() or QApplication([])
        if not Ui.styled:                                   # as the app does; only before the first QML load
            QQuickStyle.setStyle("Fusion")
            Ui.styled = True
        self.engine = Engine(MockBackend(), memory_file=str(tmp_path / "mem.json"))
        self.bridge = Bridge(self.engine, AppSettings(str(tmp_path / "settings.json")))
        self.shell = Shell(self.app, self.engine, self.bridge)
        self.qml = QQmlApplicationEngine()
        self.qml.rootContext().setContextProperty("mixer", self.bridge)
        self.qml.rootContext().setContextProperty("shell", self.shell)
        self.qml.load(os.path.join(HERE, "qml", "Main.qml"))
        assert self.qml.rootObjects(), "Main.qml failed to load"
        self.win = self.qml.rootObjects()[0]
        self.win.resize(1400, 900)
        self.engine.start()
        self.win.show()
        assert QTest.qWaitForWindowExposed(self.win)
        self.wait()

    def close(self):
        self.win.hide()
        self.engine.shutdown()
        self.qml.deleteLater()
        self.wait(50)

    def wait(self, ms=100):
        QTest.qWait(ms)                                     # lets bindings, layouts and Qt.callLater settle

    def items(self, type_name):
        """Visible items of a QML type (e.g. "ChannelPill"), in scene order."""
        out = []

        def walk(it):
            if not it.isVisible():
                return
            if it.metaObject().className().startswith(type_name + "_QML"):
                out.append(it)
            for c in it.childItems():
                walk(c)
        walk(self.win.contentItem())
        return out

    def item(self, name):
        it = self.win.findChild(QQuickItem, name)
        if it is None:                                      # e.g. a drop-down's entries, in the overlay
            it = self._find_visual(self.win.contentItem(), name)
        assert it is not None, name
        return it

    def _find_visual(self, it, name):
        if it.objectName() == name:
            return it
        for c in it.childItems():
            if (f := self._find_visual(c, name)) is not None:
                return f
        return None

    def pill(self, key):
        return next(p for p in self.items("ChannelPill") if p.property("unit").toVariant()["key"] == key)

    @staticmethod
    def center(it):
        p = it.mapToScene(QPointF(it.width() / 2, it.height() / 2))
        return QPoint(round(p.x()), round(p.y()))

    def click(self, at):
        QTest.mouseClick(self.win, Qt.LeftButton, Qt.NoModifier, self.center(at) if isinstance(at, QQuickItem) else at)
        self.wait()

    def double_click(self, it):
        QTest.mouseDClick(self.win, Qt.LeftButton, Qt.NoModifier, self.center(it))
        self.wait()

    def type(self, text):
        for ch in text:                     # keyClicks() takes widgets only; keyClick() also takes a window
            QTest.keyClick(self.win, ch)
        self.wait(30)

    def key(self, key):
        QTest.keyClick(self.win, key)
        self.wait()


@pytest.fixture
def ui(tmp_path):
    u = Ui(tmp_path)
    yield u
    u.close()


def test_info_bar_names_with_details_in_tooltips(ui):
    texts = {}
    for name in ("infoDriver", "infoCard", "infoRate"):
        row = ui.item(name)
        label, value = [c for c in row.childItems() if c.metaObject().className().startswith("QQuickText")]
        assert label.y() > value.y()                                   # the label sits lower than before
        texts[name] = (value.property("text"), row.property("tip"))
    i = ui.engine.info
    assert texts["infoDriver"] == ("mock", "Version: simulated")
    assert texts["infoCard"] == (i.name, f"Revision: {i.pci_revision}\nFirmware build: {i.firmware_build}"
                                  f"\nSerial number: {i.serial}")
    assert texts["infoRate"][0].endswith(" Hz") and "Speed" not in texts["infoRate"][0]
    assert texts["infoRate"][1] == "Single Speed"
    QTest.mouseMove(ui.win, ui.center(ui.item("infoCard")))
    ui.wait(900)
    def shown(it):                                                      # tooltips open in the overlay
        return (it.isVisible() and it.property("text") == texts["infoCard"][1]) or any(map(shown, it.childItems()))
    assert shown(ui.win.contentItem())


def test_recall_dialog_all_and_none(ui):
    ui.bridge.recallRequested.emit(0, "Test", False)            # stored without names: Names can't be ticked
    ui.wait(300)
    boxes = lambda: {i.property("text").split(":")[0]: i.property("checked")
                     for i in ui.items("CheckBox") if i.isVisible()}
    assert boxes() == {"Names": False, "Destinations": True, "Levels": True, "Mutes": True, "Card IO settings": True}
    ui.click(ui.center(ui.item("recallNone")))
    assert not any(boxes().values())
    ui.click(ui.center(ui.item("recallAll")))
    assert boxes() == {"Names": False, "Destinations": True, "Levels": True, "Mutes": True, "Card IO settings": True}


def test_export_everything_and_import_picked_parts(ui, tmp_path):
    from aa_hdspe_mixer.core.memory import read_json
    from aa_hdspe_mixer.core.midi import Binding
    from aa_hdspe_mixer.core.osc import OscBinding
    from aa_hdspe_mixer.core.shortcuts import KeyBinding
    b, e = ui.bridge, ui.engine
    # Something in every part.
    b.addConnections("in:0", ["out:6"], "-6")
    b.rename("in", 0, "Mic")
    s = next(x for x in e.card_settings() if not x.linked and len(e.setting_choices(x.id)) > 1)
    first, other = e.setting_choices(s.id)[0], e.setting_choices(s.id)[1]
    e.set_card_setting(s.id, other)
    e.set_internal_rate(44100)
    b.refreshVirtual()
    assert b.createVirtualDevice("output", "Monitors", "AUX2", "AUX3") == ""
    assert b.midi.save_binding(Binding(what="mute", target="in:0", number=60)) == ""
    assert b.osc.save_binding(OscBinding(what="mute", target="in:0", address="/mic")) == ""
    assert b.keys.save_binding(KeyBinding(what="mute", target="in:0", key="Ctrl+Alt+M")) == ""
    b.midi.set_enabled(True)
    b.storePreset(0, "A")
    b.storePreset(2, "C")
    path = str(tmp_path / "all.json")
    b.export_all(path)
    data = read_json(path)
    assert {"clock", "pipewire", "midi", "osc", "shortcuts"} <= set(data)
    assert data["clock"]["internal_rate"] == 44100 and data["pipewire"][0]["name"] == "Monitors"

    # Change it all.
    e.clear_routing()
    b.rename("in", 0, "")
    e.set_card_setting(s.id, first)
    e.set_internal_rate(48000)
    b.removeVirtualDevice(b.vd.devices()[0].id)
    for ctl in (b.midi, b.osc, b.keys):
        ctl.remove_binding(ctl.bindings()[0].id)
    b.midi.set_enabled(False)
    b.storePreset(1, "Mine")

    # The dialog: everything the file has, ticked; None and All; the clock left out.
    b.offer_import(path, data)
    ui.wait(300)
    boxes = {p: ui.item("importPart_" + p) for p in ("routing", "presets", "card", "clock", "pipewire", "midi", "osc",
                                                     "shortcuts")}
    assert all(x.property("enabled") and x.property("checked") for x in boxes.values())
    pfrom, pto = ui.item("importPresetFrom"), ui.item("importPresetTo")
    assert pfrom.property("enabled") and pfrom.property("currentText") == "IMPORT ALL PRESETS"
    assert pto.property("opacity") == 0 and not pto.property("enabled")   # all presets: no slot to pick
    ui.click(ui.center(ui.item("importNone")))
    assert not any(x.property("checked") for x in boxes.values()) and not ui.item("importApply").property("enabled")
    assert not pfrom.property("enabled")                                  # Presets unticked: nothing to pick
    ui.click(ui.center(ui.item("importAll")))
    assert all(x.property("checked") for x in boxes.values())
    ui.click(ui.center(boxes["clock"]))
    ui.click(ui.center(ui.item("importApply")))
    ui.wait(200)
    assert e.connection_db("in:0", "out:6") == pytest.approx(-6, abs=0.05) and e.memory.name_of("in", 0) == "Mic"
    assert e.card_setting(s.id) == other
    assert e.clock.internal_rate == 48000                                  # not picked: unchanged
    assert [(d.name, d.channels) for d in b.vd.devices()] == [("Monitors", ["AUX2", "AUX3"])]
    assert len(b.midi.bindings()) == len(b.osc.bindings()) == len(b.keys.bindings()) == 1 and b.midi.enabled
    assert b.keys.plasma.keys[b.keys.bindings()[0].id]                     # given to Plasma again
    assert b.presetNames[:3] == ["A", "", "C"]                             # all of the file's presets

    # One preset from the file, into a slot picked here: the other slots stay, and so does the routing.
    b.storePreset(1, "Mine")
    b.rename("in", 0, "Changed")
    b.offer_import(path, data)
    ui.wait(300)
    ui.click(ui.center(ui.item("importNone")))
    ui.click(ui.center(ui.item("importPart_presets")))
    pfrom, pto = ui.item("importPresetFrom"), ui.item("importPresetTo")
    assert list(pfrom.property("model")) == ["IMPORT ALL PRESETS", "01   A", "03   C"]
    ui.click(ui.center(pfrom))
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    assert pto.property("opacity") == 1 and pto.property("currentIndex") == 2   # the file's slot, to start
    ui.click(ui.center(pto))
    for _ in range(3):
        ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    assert pto.property("currentIndex") == 5
    ui.click(ui.center(ui.item("importApply")))
    ui.wait(200)
    assert b.presetNames[:6] == ["A", "Mine", "C", "", "", "C"] and e.memory.name_of("in", 0) == "Changed"

    # A file from before: routing, presets and card settings only.
    e.export_memory(str(tmp_path / "old.json"))
    b.offer_import(str(tmp_path / "old.json"), read_json(str(tmp_path / "old.json")))
    ui.wait(300)
    avail = {p: ui.item("importPart_" + p).property("enabled") for p in boxes}
    assert avail == {"routing": True, "presets": True, "card": True, "clock": False, "pipewire": False,
                     "midi": False, "osc": False, "shortcuts": False}
    ui.key(Qt.Key_Escape)


def test_pill_click_shows_and_hides_channel(ui):
    ui.bridge.addConnections("in:0", ["out:6"], "0")
    ui.wait(300)
    ui.click(ui.pill("out:6"))
    assert ui.bridge.channelOpen and ui.bridge.channelKey == "out:6"
    assert ui.pill("out:6").property("selected")
    ui.click(ui.pill("in:0"))                               # another pill: switches, never adds
    assert ui.bridge.channelKey == "in:0" and not ui.pill("out:6").property("selected")
    ui.click(ui.pill("in:0"))                               # the shown one again: closes
    assert not ui.bridge.channelOpen


def test_rename_in_channel_header(ui):
    name = lambda: ui.engine.memory.name_of("in", 0)
    ui.bridge.addConnections("in:0", ["out:6"], "0")
    ui.wait(300)
    ui.click(ui.pill("in:0"))
    ed = ui.item("channelName")
    assert not ed.property("editing")                       # a label until double-clicked

    ui.double_click(ed)
    assert ed.property("editing")
    ui.type("Vox")
    ui.key(Qt.Key_Escape)                                   # Esc cancels
    assert not ed.property("editing") and name() == ""
    assert ui.bridge.channelOpen                            # and leaves the panel alone

    ui.double_click(ed)
    ui.type("Vox")
    ui.key(Qt.Key_Return)                                   # Enter applies
    assert not ed.property("editing") and name() == "Vox"

    ui.double_click(ed)
    assert ed.property("editing")
    ui.type("Gtr")
    ui.click(QPoint(400, 800))                              # a click outside the field (empty routing view)
    assert not ed.property("editing") and name() == "Gtr"
    assert ui.bridge.channelKey == "in:0"

    ui.double_click(ed)
    ui.type("Bass")
    ui.click(ui.pill("out:6"))                              # a click on another pill: applies, then switches
    assert name() == "Bass"
    assert ui.engine.memory.name_of("out", 6) == ""         # the name went to the channel it was typed for
    assert ui.bridge.channelKey == "out:6" and not ed.property("editing")


def test_mono_stereo_in_channel_header(ui):
    stereo = lambda: ui.bridge.channel["stereo"]
    ui.bridge.addConnections("in:0", ["out:6"], "0")
    ui.wait(300)
    ui.click(ui.pill("in:0"))
    md = ui.item("channelMode")
    box = ui.item("channelModeBox")
    was = stereo()
    assert not md.property("editing")                       # a label until double-clicked

    ui.double_click(md)
    assert md.property("editing") and box.property("down")    # down: list open
    ui.key(Qt.Key_Escape)                                   # Esc: closes, unchanged
    assert not md.property("editing") and stereo() == was

    ui.double_click(md)
    ui.click(QPoint(400, 800))                              # a click outside the list: closes, unchanged
    assert not md.property("editing") and stereo() == was
    assert ui.bridge.channelKey.startswith("in:0")

    ui.double_click(md)
    ui.key(Qt.Key_Down if not was else Qt.Key_Up)           # pick the other one
    ui.key(Qt.Key_Return)
    assert not md.property("editing") and stereo() == (not was)
    assert ui.bridge.channelOpen                            # the panel follows the channel through the change
    assert ui.item("channelMode").property("editing") is False


def test_stereo_from_right_channel(ui):
    ui.engine.set_stereo("in", 0, False)                    # AN 1/2 as two mono channels
    ui.bridge.addConnections("in:1", ["out:6"], "0")
    ui.wait(300)
    right = next(u.key for u in ui.engine.units("in") if u.channels[0].hw == 1)
    ui.click(ui.pill(right))
    assert ui.bridge.channel["hw"] == 1 and not ui.bridge.channel["stereo"]
    ui.double_click(ui.item("channelMode"))
    assert ui.item("channelModeStereo").isEnabled()        # selectable on the right channel too
    ui.click(ui.item("channelModeStereo"))
    assert ui.bridge.channel["stereo"] and ui.bridge.channel["hw"] == 0


def test_mute_in_channel_header(ui):
    ui.bridge.addConnections("in:0", ["out:6"], "0")
    ui.wait(300)
    ui.click(ui.pill("out:6"))
    assert not ui.bridge.channel["muted"]
    ui.click(ui.item("channelMute"))
    assert ui.bridge.channel["muted"] and ui.engine.unit_muted("out:6")
    ui.click(ui.item("channelMute"))
    assert not ui.bridge.channel["muted"]


def test_channel_panel_scrolls_under_fixed_header(ui):
    ui.bridge.addConnections("in:0", ["out:6"], "0")
    ui.wait(300)
    ui.click(ui.pill("out:6"))
    ui.win.resize(1400, 560)                                # too short for the whole section
    ui.wait(300)
    flick, fader, mute = ui.item("channelScroll"), ui.item("channelFader"), ui.item("channelMute")
    assert flick.property("contentHeight") > flick.height()
    head_y = ui.center(mute).y()

    # Dragging the fader moves the fader, not the panel
    p = ui.center(fader)
    QTest.mousePress(ui.win, Qt.LeftButton, Qt.NoModifier, p)
    ui.wait()
    pressed_at = fader.property("value")
    for dy in range(10, 90, 10):
        QTest.mouseMove(ui.win, QPoint(p.x(), p.y() + dy))
        ui.wait(20)
    QTest.mouseRelease(ui.win, Qt.LeftButton, Qt.NoModifier, QPoint(p.x(), p.y() + 80))
    ui.wait()
    assert flick.property("contentY") == 0
    assert fader.property("value") < pressed_at - 10
    assert ui.bridge.channel["outLevelDb"] == fader.property("value")      # written on release

    # Scrolled to the end, the header stays where it was
    flick.setProperty("contentY", flick.property("contentHeight") - flick.height())
    ui.wait()
    assert ui.center(mute).y() == head_y


def test_toolbar_sections_close_with_x(ui):
    for prop in ("settingsPanelOpen", "clockPanelOpen", "appSettingsOpen"):
        setattr(ui.bridge, prop, True)
        ui.wait()
        ui.click(ui.item("panelClose"))
        assert not getattr(ui.bridge, prop), prop
        assert not (ui.bridge.settingsPanelOpen or ui.bridge.clockPanelOpen or ui.bridge.appSettingsOpen)


def test_test_button_for_playback_channels(ui):
    ui.bridge.addConnections("pb:0", ["out:6"], "0")
    ui.wait(300)
    ui.click(ui.pill("out:6"))
    assert not ui.item("channelTest").isVisible()           # outputs (and inputs) have none
    ui.click(ui.pill("pb:0"))
    btn = ui.item("channelTest")
    assert btn.isVisible() and btn.property("text") == "Test"
    ui.click(btn)
    assert ui.engine.unit_testing("pb:0") and btn.property("text") == "Stop Test"
    assert ui.engine.backend.tone.plans[-1].slots == [0, 1]
    ui.click(btn)
    assert not ui.engine.unit_testing("pb:0") and btn.property("text") == "Test"


def test_channel_panel_fits_the_side_panel(ui):
    """The header once asked for more room than the panel had and pushed the section past its edge."""
    ui.bridge.addConnections("pb:0", ["out:6"], "0")
    ui.wait(300)
    for key in ("pb:0", "out:6"):
        ui.click(ui.pill(key))
        flick = ui.item("channelScroll")
        sec = flick.parentItem()
        panel = sec.parentItem()
        right = lambda it: it.mapToScene(QPointF(it.width(), 0)).x()
        assert flick.width() == sec.width() == panel.width() - 1, key
        head, body = sec.childItems()[0], flick.property("contentItem").childItems()[0]
        assert right(head) == right(body) <= right(panel) - 16 - 8, key      # room for the scroll bar too
        ui.click(ui.pill(key))                              # close before the next


def test_fader_double_click_resets_to_0_db(ui):
    ui.bridge.addConnections("in:0", ["out:6"], "0")
    ui.bridge.setOutputLevel("out:6", "-20")
    ui.wait(300)
    ui.click(ui.pill("out:6"))
    fader = ui.item("channelFader")
    assert fader.property("value") == -20
    ui.double_click(fader)
    assert ui.bridge.channel["outLevelDb"] == 0 and fader.property("value") == 0
    ui.bridge.setOutputLevel("out:6", "-10")                # still follows the level set elsewhere
    ui.wait()
    assert fader.property("value") == -10

    ui.click(fader)                                         # two clicks far apart: no reset
    ui.wait(800)
    ui.click(fader)
    assert ui.bridge.channel["outLevelDb"] != 0
    ui.bridge.setOutputLevel("out:6", "-30")
    ui.wait()
    assert fader.property("value") == -30


def test_output_level_box(ui):
    level = lambda key="out:6": ui.engine.output_level_db(ui.engine.unit(key).channels[0].hw)
    ui.bridge.addConnections("in:0", ["out:6"], "0")
    ui.bridge.addConnections("in:0", ["out:0"], "0")
    ui.wait(300)
    ui.click(ui.pill("out:6"))
    box = ui.item("channelLevelBox")

    def edit(text):
        ui.wait(600)                                        # past the double-click interval of the last click
        ui.click(box)
        assert box.hasActiveFocus()
        QTest.keyClick(ui.win, Qt.Key_A, Qt.ControlModifier)
        ui.type(text)

    edit("-12")
    ui.key(Qt.Key_Escape)                                   # Esc cancels
    assert level() == 0 and box.property("text") == "0.0" and not box.hasActiveFocus()

    edit("-12")
    ui.key(Qt.Key_Return)                                   # Enter applies
    assert level() == -12 and not box.hasActiveFocus()

    edit("-6")
    ui.click(QPoint(400, 800))                              # a click outside applies
    assert level() == -6 and not box.hasActiveFocus() and box.property("text") == "-6.0"

    edit("-3")
    ui.click(ui.pill("out:0"))                              # a click on another pill: applies to this output
    assert level() == -3 and level("out:0") == 0 and ui.bridge.channelKey == "out:0"

    edit("loud")
    ui.click(QPoint(400, 800))                              # not a level: nothing changes, the box shows it again
    assert level("out:0") == 0 and box.property("text") == "0.0"


def test_fader_on_the_meter_scale(ui):
    ui.bridge.addConnections("in:0", ["out:6"], "0")
    ui.wait(300)
    ui.click(ui.pill("out:6"))
    fader, meter = ui.item("channelFader"), ui.item("channelMeter")
    top = meter.mapToScene(QPointF(0, meter.property("barTop"))).y()
    per_db = meter.property("barH") / 60
    handle = fader.property("handle")
    for db in (0, -6, -24, -60, 6):
        fader.setProperty("value", db)
        ui.wait(30)
        centre = handle.mapToScene(QPointF(handle.width() / 2, handle.height() / 2)).y()
        assert centre == pytest.approx(top - db * per_db, abs=1), db       # level with the meter's mark
    assert meter.mapToScene(QPointF(0, 0)).y() >= fader.mapToScene(QPointF(0, 0)).y()


def test_wire_colours_and_meter_rate_in_app_settings(ui):
    ui.win.resize(1400, 1200)                                # all of the section in view
    ui.bridge.appSettingsOpen = True
    ui.wait()
    rate, hue = ui.item("appMeterRate"), ui.item("appHueStep")
    assert hue.isVisible() and rate.isVisible() and ui.center(rate).y() < ui.win.height() - 40

    assert rate.property("currentText") == f"{ui.bridge.meterRate} Hz"
    ui.click(rate)
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    assert ui.bridge.meterRate == 30
    ui.bridge.meterRate = 10                                 # as from the View menu: the box follows
    ui.wait()
    assert rate.property("currentText") == "10 Hz"

    assert hue.property("value") == ui.bridge.hueStep == 20
    ui.wait(600)
    ui.click(hue)                                            # the middle of the box: its text
    assert hue.hasActiveFocus()
    QTest.keyClick(ui.win, Qt.Key_A, Qt.ControlModifier)
    ui.type("45")
    ui.click(QPoint(400, 800))                               # a click outside applies, like Enter
    assert ui.bridge.hueStep == 45 and not hue.hasActiveFocus()
    ui.bridge.hueStep = 0                                    # as from the Wire Colours dialog: the box follows
    ui.wait()
    assert hue.property("value") == 0
    ui.click(ui.item("appHueDefault"))
    assert ui.bridge.hueStep == 20


def test_pipewire_virtual_devices_panel(ui):
    ui.win.resize(1400, 1200)
    ui.click(ui.item("pipewireButton"))
    assert ui.bridge.pipewirePanelOpen
    names = lambda: [(d["direction"], d["name"]) for d in ui.bridge.virtualDevices]

    ui.click(ui.item("vdAddOutput"))
    assert len(ui.items("Box")) == 1 and ui.item("vdName").hasActiveFocus()   # the new box, ready to type
    ui.type("Monitors")
    ui.click(ui.item("vdRight"))                            # right: AUX1 -> AUX3 (AES R)
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    ui.click(ui.item("vdApply"))
    assert names() == [("output", "Monitors")]
    dev = ui.bridge.virtualDevices[0]
    assert (dev["left"], dev["right"], dev["state"]) == ("AN 1 (AUX0)", "AES R (AUX3)", "ok")
    assert ui.engine.backend.virtual.get("out-monitors").channels == ["AUX0", "AUX3"]
    assert [b.objectName() for b in ui.items("Box")] == ["vdDevice"]          # made: only Remove left

    ui.click(ui.item("vdAddInput"))
    ui.type("monitors")                                     # an input may share an output's name
    ui.key(Qt.Key_Return)                                   # Enter applies too
    ui.click(ui.item("vdAddOutput"))
    ui.type("MONITORS")
    ui.click(ui.item("vdApply"))                            # but not another output's: the box stays, says why
    err = ui.items("Box")[0].findChild(QQuickItem, "vdError")
    assert err.isVisible() and "already a virtual output" in err.property("text")
    assert [b.objectName() for b in ui.items("Box")] == ["vdDraft", "vdDevice", "vdDevice"]
    ui.click(ui.item("vdCancel"))
    assert [b.objectName() for b in ui.items("Box")] == ["vdDevice", "vdDevice"]
    assert names() == [("input", "monitors"), ("output", "Monitors")]       # inputs first

    ui.click(ui.items("Box")[0].findChild(QQuickItem, "vdRemove"))
    assert names() == [("output", "Monitors")]


def test_virtual_device_draft_keeps_its_choices_through_refreshes(ui):
    ui.bridge.pipewirePanelOpen = True
    ui.wait()
    ui.click(ui.item("vdAddInput"))
    right = ui.item("vdRight")
    right.setProperty("currentIndex", 5)
    for _ in range(3):
        ui.bridge.refreshVirtual()                          # as every 2 s while the section is shown
        ui.wait(50)
    assert right.property("currentIndex") == 5
    ui.bridge.createVirtualDevice("output", "Other", "AUX4", "AUX5")     # the list changes, the draft stays
    ui.wait()
    assert ui.item("vdRight").property("currentIndex") == 5 and len(ui.items("Box")) == 2


def midi_cards(ui, prefix="midi"):
    """The MIDI (or OSC, or keyboard shortcuts) section's boxes, top to bottom: ("midiEditor" | "midiBinding", item)."""
    out = []

    def walk(it):
        if not it.isVisible():
            return
        if it.objectName() in (prefix + "Editor", prefix + "Binding"):
            out.append((it.objectName(), it))
        for c in it.childItems():
            walk(c)
    walk(ui.win.contentItem())
    out.sort(key=lambda c: c[1].mapToScene(QPointF(0, 0)).y())
    return out


def test_midi_panel_has_the_router_link_at_its_bottom(ui):
    ui.win.resize(1400, 900)
    ui.click(ui.item("midiButton"))
    link = ui.item("midiRouterLink")
    assert link.isVisible()
    assert "Linux-MIDI-snd_seq_dummy-Router" in link.property("text")
    assert "Need a MIDI router?" in link.property("text")
    bottom = link.mapToScene(QPointF(0, link.height())).y()
    assert ui.win.contentItem().height() - bottom < 30                  # at the window's bottom edge
    ui.click(ui.item("midiButton"))                                     # closed: gone with the section
    assert not link.isVisible()


def test_midi_panel_add_learn_edit_remove(ui):
    from aa_hdspe_mixer.core.alsaseq import Msg
    seq = ui.engine.backend.midi
    ui.win.resize(1400, 1200)
    ui.click(ui.item("midiButton"))
    assert ui.bridge.midiPanelOpen and not ui.bridge.midiEnabled
    assert ui.item("midiStatus").property("text") == ""                 # off, but no bindings: nothing to say
    ui.click(ui.item("midiEnable"))
    assert ui.bridge.midiEnabled
    ui.click(ui.item("midiIn"))                              # None → the controller
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    assert ui.bridge.midiInput == "Mock Controller:Mock Controller MIDI 1" and seq.inputs

    ui.click(ui.item("midiAdd"))
    (kind, box), = midi_cards(ui)
    assert kind == "midiEditor"
    find = lambda b, name: ui._find_visual(b, name)          # (by the visual tree: some come from Repeaters)
    ui.click(find(box, "midiApply"))                         # nothing chosen yet: says why, stays
    assert "Choose a channel" in find(box, "midiError").property("text") and not ui.bridge.midiBindings
    ui.click(find(box, "midiTarget"))
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    assert find(box, "midiTarget").property("currentText") == "Input ADAT 1/2"   # Return on a header: its first (sorted)
    heads = [t["text"] for t in ui.bridge.midiTargets["mute"] if t.get("header")]
    assert heads == ["Hardware inputs", "Playback channels (from software)", "Hardware outputs"]   # no connections
    ui.click(find(box, "midiLearn"))
    seq.inject(Msg("note_on", 2, 62, 100))                   # pressed on the controller
    ui.wait()
    assert find(box, "midiNumber").property("displayText") == "62 (D4)"
    assert find(box, "midiChannel").property("currentText") == "3"
    assert not ui.engine.unit_muted("in:12")                  # learning doesn't act
    ui.click(find(box, "midiApply"))
    (b,) = ui.bridge.midiBindings
    assert (b["what"], b["target"], b["message"], b["channel"], b["number"]) == ("mute", "in:12", "note", 3, 62)
    assert [k for k, _ in midi_cards(ui)] == ["midiBinding"]
    seq.inject(Msg("note_on", 2, 62, 100))
    assert ui.engine.unit_muted("in:12")

    # A new box keeps what's chosen in it while another binding is edited and saved.
    ui.click(ui.item("midiAdd"))
    draft = midi_cards(ui)[0][1]
    ui.click(find(draft, "midiWhat"))                        # Mute → Level
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    assert find(draft, "midiMessage").property("currentText") == "Controller (CC)"
    saved = midi_cards(ui)[1][1]
    ui.click(find(saved, "midiEdit"))
    assert [k for k, _ in midi_cards(ui)] == ["midiEditor", "midiEditor"]
    ui.click(find(saved, "midiMode"))                        # Toggle → (Mute, Unmute on each press) → Mute while held
    for _ in range(3):
        ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    ui.click(find(saved, "midiApply"))
    assert ui.bridge.midiBindings[0]["mode"] == "follow" and len(ui.bridge.midiBindings) == 1
    assert [k for k, _ in midi_cards(ui)] == ["midiEditor", "midiBinding"]
    assert find(midi_cards(ui)[0][1], "midiWhat").property("currentText") == "Level"
    ui.click(find(midi_cards(ui)[0][1], "midiCancel"))

    ui.click(ui.item("midiEnable"))                          # off, with bindings: said
    assert ui.item("midiStatus").property("text") == "MIDI control is off, the bindings below are inactive."
    ui.click(find(midi_cards(ui)[0][1], "midiRemove"))
    assert ui.bridge.midiBindings == [] and midi_cards(ui) == []
    assert ui.item("midiStatus").property("text") == ""
    assert ui.engine.memory.presets[0] is None or "midi" not in str(ui.engine.memory.presets[0].to_dict())


def test_osc_panel_add_learn_edit_remove(ui):
    from PySide6.QtNetwork import QHostAddress, QUdpSocket
    from aa_hdspe_mixer.core.osc import OscMsg, encode
    ui.win.resize(1400, 1200)
    ui.click(ui.item("oscButton"))
    assert ui.bridge.oscPanelOpen and not ui.bridge.oscEnabled and not ui.bridge.midiPanelOpen
    assert ui.item("oscStatus").property("text") == ""                  # off, but no bindings: nothing to say
    ui.bridge.setOscListen(0, False)                         # a free port, not 8000
    ui.click(ui.item("oscEnable"))
    assert ui.bridge.oscEnabled and ui.bridge.osc.udp.is_open
    ctl = QUdpSocket()
    send = lambda m: (ctl.writeDatagram(encode(m), QHostAddress("127.0.0.1"), ui.bridge.osc.listening_port),
                      ui.wait(150))

    ui.click(ui.item("oscAdd"))
    (kind, box), = midi_cards(ui, "osc")
    assert kind == "oscEditor"
    find = lambda b, name: ui._find_visual(b, name)
    ui.click(find(box, "oscTarget"))
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    ui.click(find(box, "oscApply"))                          # no address yet: says why, stays
    assert "starts with /" in find(box, "oscError").property("text") and not ui.bridge.oscBindings
    ui.click(find(box, "oscLearn"))
    send(OscMsg("/1/push1", (1.0,)))                         # pressed on the controller
    assert find(box, "oscAddress").property("text") == "/1/push1"
    assert not ui.engine.unit_muted("in:12")                  # learning doesn't act
    assert ui.item("oscActivity").property("text") == "/1/push1 1"
    ui.click(find(box, "oscApply"))
    (b,) = ui.bridge.oscBindings
    assert (b["what"], b["target"], b["address"], b["mode"]) == ("mute", "in:12", "/1/push1", "toggle")
    send(OscMsg("/1/push1", (1.0,)))
    assert ui.engine.unit_muted("in:12")

    # A level learned from a value outside 0..1 is taken as dB; editing keeps a new box's choices.
    ui.click(ui.item("oscAdd"))
    draft = midi_cards(ui, "osc")[0][1]
    ui.click(find(draft, "oscWhat"))                          # Mute → Level
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    ui.click(find(draft, "oscLearn"))
    send(OscMsg("/mix/out1", (-12.0,)))
    assert find(draft, "oscValue").property("currentText") == "Level in dB"
    assert not find(draft, "oscInvert").isVisible()
    saved = midi_cards(ui, "osc")[1][1]
    ui.click(find(saved, "oscEdit"))
    ui.click(find(saved, "oscMode"))                          # Toggle → Mute on each press
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    ui.click(find(saved, "oscApply"))
    assert ui.bridge.oscBindings[0]["mode"] == "press_mute"
    assert find(midi_cards(ui, "osc")[0][1], "oscAddress").property("text") == "/mix/out1"
    ui.click(find(midi_cards(ui, "osc")[0][1], "oscCancel"))

    ui.click(ui.item("oscEnable"))                           # off, with bindings: said
    assert not ui.bridge.osc.udp.is_open
    assert ui.item("oscStatus").property("text") == "OSC control is off, the bindings below are inactive."
    ui.click(find(midi_cards(ui, "osc")[0][1], "oscRemove"))
    assert ui.bridge.oscBindings == [] and midi_cards(ui, "osc") == []
    assert ui.item("oscStatus").property("text") == ""


def test_keys_panel_add_learn_edit_remove(ui):
    plasma = ui.engine.backend.global_shortcuts()            # the mock card's stand-in: never the desktop's
    ui.win.resize(1400, 1200)
    ui.click(ui.item("keysButton"))
    assert ui.bridge.keysPanelOpen and not ui.bridge.keysEnabled and not ui.bridge.oscPanelOpen
    assert ui.item("keysStatus").property("text") == ""                 # off, but no bindings: nothing to say
    ui.click(ui.item("keysEnable"))
    assert ui.bridge.keysEnabled and ui.item("keysStatus").property("text") == ""

    ui.click(ui.item("keysAdd"))
    (kind, box), = midi_cards(ui, "keys")
    assert kind == "keysEditor"
    find = lambda b, name: ui._find_visual(b, name)
    ui.click(find(box, "keysTarget"))
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    assert find(box, "keysKeys").property("text") == "None"
    ui.click(find(box, "keysLearn"))
    assert plasma.blocked and find(box, "keysKeys").property("text") == "Listening…"
    QTest.keyClick(ui.win, Qt.Key_M)                          # alone: refused, still listening
    ui.wait()
    assert "alone would be taken" in find(box, "keysError").property("text") and plasma.blocked
    QTest.keyClick(ui.win, Qt.Key_M, Qt.ControlModifier | Qt.AltModifier)
    ui.wait()
    assert find(box, "keysKeys").property("text") == "Ctrl+Alt+M" and not plasma.blocked
    ui.click(find(box, "keysApply"))
    (b,) = ui.bridge.keysBindings
    assert (b["what"], b["target"], b["key"], b["mode"]) == ("mute", "in:12", "Ctrl+Alt+M", "toggle")
    assert plasma.names[b["id"]] == "Mute or unmute: Input ADAT 1/2"     # as System Settings lists it
    plasma.press(b["id"])
    ui.wait(150)
    assert ui.engine.unit_muted("in:12") and ui.item("keysActivity").property("text") == "Ctrl+Alt+M: pressed"

    # Esc cancels learning; a level steps; a taken key says who has it.
    ui.click(ui.item("keysAdd"))
    draft = midi_cards(ui, "keys")[0][1]
    ui.click(find(draft, "keysWhat"))                         # Mute → Level
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    assert find(draft, "keysStep").isVisible() and not find(draft, "keysLevel").isVisible()
    ui.click(find(draft, "keysTarget"))
    ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    ui.click(find(draft, "keysLearn"))
    QTest.keyClick(ui.win, Qt.Key_Escape)
    ui.wait()
    assert find(draft, "keysKeys").property("text") == "None" and not plasma.blocked
    plasma.others[0x0C000000 | Qt.Key_Down.value] = "KWin: Switch One Desktop Down"
    ui.click(find(draft, "keysLearn"))
    QTest.keyClick(ui.win, Qt.Key_Down, Qt.ControlModifier | Qt.AltModifier)
    ui.wait()
    ui.click(find(draft, "keysApply"))
    assert "KWin: Switch One Desktop Down" in find(draft, "keysError").property("text")
    ui.click(find(draft, "keysClear"))
    ui.click(find(draft, "keysApply"))                        # no keys: saved, to be given some later
    lv = next(d for d in ui.bridge.keysBindings if d["what"] == "level")
    assert lv["key"] == "" and lv["messageText"] == "No keys yet"
    plasma.change(lv["id"], 0x0C000000 | Qt.Key_PageDown.value)     # given keys in System Settings
    ui.wait()
    lv = next(d for d in ui.bridge.keysBindings if d["what"] == "level")
    assert lv["keyText"] == "Ctrl+Alt+PgDown"

    # Edit: Toggle → Mute while held shows Inverted.
    saved = midi_cards(ui, "keys")[0][1]                    # mutes come first
    ui.click(find(saved, "keysEdit"))
    assert not find(saved, "keysInvert").isVisible()
    ui.click(find(saved, "keysMode"))
    for _ in range(3):
        ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    assert find(saved, "keysInvert").isVisible()
    ui.click(find(saved, "keysApply"))
    assert ui.bridge.keysBindings[0]["mode"] == "follow"

    ui.click(ui.item("keysEnable"))                          # off, with bindings: said
    assert ui.item("keysStatus").property("text") == "Keyboard shortcuts are off, the bindings below are inactive."
    for _ in range(2):
        ui.click(find(midi_cards(ui, "keys")[0][1], "keysRemove"))
    assert ui.bridge.keysBindings == [] and midi_cards(ui, "keys") == [] and plasma.keys == {}
    assert not ui.bridge.keysEnabled and ui.item("keysStatus").property("text") == ""
