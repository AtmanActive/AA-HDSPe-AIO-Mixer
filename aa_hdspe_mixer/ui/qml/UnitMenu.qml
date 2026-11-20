import QtQuick
import QtQuick.Controls

// Context menu for a channel pill.
Menu {
    id: menu
    property var unit: ({})

    function openFor(u, item, x, y) { unit = u; popup(item, x, y) }

    MenuItem {
        text: "Mute"; checkable: true; checked: !!menu.unit.muted
        onTriggered: mixer.toggleMute(menu.unit.key)
    }
    MenuItem {      // playback channels: test signal (core/tone.py)
        visible: menu.unit.kind === "pb"; height: visible ? implicitHeight : 0
        text: menu.unit.testing ? "Stop Test" : "Test"
        onTriggered: mixer.testChannel(menu.unit.key)
    }
    MenuSeparator {}
    // Card settings of this channel (card_settings.py), one submenu per group, inserted after Mute.
    // A group with several settings has one section each, under a (disabled) heading item.
    Instantiator {
        model: menu.unit.settingMenus || []
        delegate: Menu {
            id: sub
            required property var modelData
            required property int index
            title: modelData.title
            width: {        // as wide as the longest entry (the default width cuts hints off)
                var w = 200
                for (var i = 0; i < count; ++i) w = Math.max(w, itemAt(i).implicitWidth)
                return w + 16
            }
            Instantiator {
                model: sub.modelData.entries
                delegate: MenuItem {
                    required property var modelData
                    text: modelData.text
                    enabled: !modelData.header
                    font.bold: modelData.header
                    checkable: !modelData.header
                    checked: modelData.checked
                    onTriggered: mixer.setCardSetting(modelData.id, modelData.name)
                }
                onObjectAdded: (i, o) => sub.insertItem(i, o)
                onObjectRemoved: (i, o) => sub.removeItem(o)
            }
        }
        onObjectAdded: (i, o) => menu.insertMenu(2 + i, o)
        onObjectRemoved: (i, o) => menu.removeMenu(o)
    }
    MenuItem { text: "Rename…"; onTriggered: dialogs.rename.openFor(menu.unit) }
    MenuItem {
        text: menu.unit.stereo ? "Split into Mono" : "Join as Stereo Pair"
        enabled: !!menu.unit.pairable
        onTriggered: mixer.setStereo(menu.unit.kind, menu.unit.pair, !menu.unit.stereo)
    }
    MenuSeparator { visible: menu.unit.kind === "out" }
    MenuItem {
        visible: menu.unit.kind === "out"; height: visible ? implicitHeight : 0
        text: "Output Level…"
        onTriggered: dialogs.outLevel.openFor(menu.unit)
    }
    MenuItem {
        visible: menu.unit.kind === "out" && !!menu.unit.outLevelSet; height: visible ? implicitHeight : 0
        text: "Reset Output Level to 0 dB"
        onTriggered: mixer.setOutputLevel(menu.unit.key, "0")
    }
    MenuItem {
        visible: menu.unit.kind === "out"; height: visible ? implicitHeight : 0
        text: "Remove All Routes to This Output"
        onTriggered: mixer.removeAllTo(menu.unit.key)
    }
    MenuItem {   // kept in the data model; hidden until a driver exposes loopback
        visible: menu.unit.kind === "out" && mixer.loopbackSupported; height: visible ? implicitHeight : 0
        text: "Loopback"; checkable: true; checked: !!menu.unit.loopback
    }
}
