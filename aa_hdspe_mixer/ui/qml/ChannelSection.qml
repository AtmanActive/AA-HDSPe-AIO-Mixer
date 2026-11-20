import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Channel section of the side panel, shown when a pill in the routing view is clicked (click the pill again,
// or ✕, to close it). Fills the panel: a fixed header (kind, mono/stereo: double-click for a drop-down;
// hardware name; user name: double-click to rename; mute M; close ✕), and below it one scrolling column:
// the detailed meter and levels, the channel's own settings (as on the pill's right-click menu), then its
// connections. Follows the channel when it is joined into or split from a stereo pair.
ColumnLayout {
    id: sec
    spacing: 0
    readonly property var ch: mixer.channel
    readonly property bool isOut: ch.kind === "out"
    readonly property color kc: Theme.kindColor(ch.kind)
    readonly property string shownKey: ch.key || ""
    // Typing in a field and then clicking another pill: the edit goes to the channel it was typed for.
    onShownKeyChanged: { nameEdit.commit(); modeEdit.cancel(); levelField.focus = false }

    component Heading: Label { color: Theme.textDim; font.bold: true }
    component Note: Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        color: Theme.textFaint; font.pixelSize: 11
    }

    // ------------------------------------------------------------------ header
    RowLayout {
        Layout.fillWidth: true
        Layout.margins: 16; Layout.bottomMargin: 12
        Layout.rightMargin: 16 + vbar.width        // lines up with the content below and its scroll bar
        spacing: 10
        Rectangle {     // kind badge, as on the pill
            Layout.alignment: Qt.AlignVCenter
            implicitWidth: 44; implicitHeight: 44; radius: 22
            color: Qt.rgba(sec.kc.r, sec.kc.g, sec.kc.b, 0.18)
            border.color: sec.kc; border.width: 1.5
            Text {
                anchors.centerIn: parent
                text: sec.ch.kind === "in" ? "IN" : sec.ch.kind === "pb" ? "PB" : "OUT"
                color: sec.kc; font.pixelSize: 12; font.bold: true
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0      // clips rather than pushing the header past the panel's edge
            clip: true
            spacing: 1
            RowLayout {
                spacing: 0
                Label {
                    text: (sec.ch.kindLabel || "").toUpperCase() + "  ·  "
                    color: sec.kc; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.6
                }
                Item {      // mono/stereo: double-click for a drop-down
                    id: modeEdit
                    objectName: "channelMode"
                    implicitWidth: editing ? modeBox.implicitWidth : modeLabel.implicitWidth
                    implicitHeight: modeLabel.implicitHeight
                    property bool editing: false
                    // Taken when the drop-down opens, so the choice lands on that channel whatever is shown later.
                    property var target: null

                    function start() {
                        target = { kind: sec.ch.kind, pair: sec.ch.pair, stereo: !!sec.ch.stereo, pairable: !!sec.ch.pairable }
                        modeBox.currentIndex = target.stereo ? 1 : 0
                        editing = true
                        modeBox.forceActiveFocus()
                        modeBox.popup.open()
                    }
                    function apply(i) {
                        var t = target
                        cancel()
                        // the arrow keys reach a greyed-out Stereo too
                        if (t && (i === 1) !== t.stereo && (i === 0 || t.pairable)) mixer.setStereo(t.kind, t.pair, i === 1)
                    }
                    // Esc, or a click outside the list, closes it unchanged.
                    function cancel() { editing = false; target = null; modeBox.popup.close(); modeBox.focus = false }

                    Label {
                        id: modeLabel
                        visible: !modeEdit.editing
                        text: sec.ch.stereo ? "STEREO" : "MONO"
                        color: sec.kc; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.6
                        font.underline: modeMouse.containsMouse
                        MouseArea {
                            id: modeMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onDoubleClicked: modeEdit.start()
                        }
                        ToolTip.visible: modeMouse.containsMouse
                        ToolTip.delay: 600
                        ToolTip.text: "Double-click to switch between mono and stereo"
                                      + (sec.ch.pairable ? "" : ". This channel has no partner to pair with "
                                                              + "(pairs are 1+2, 3+4, …).")
                    }
                    ComboBox {
                        id: modeBox
                        objectName: "channelModeBox"
                        visible: modeEdit.editing
                        anchors.verticalCenter: parent.verticalCenter
                        implicitHeight: 24
                        font.pixelSize: 11
                        model: ["Mono", "Stereo"]
                        delegate: ItemDelegate {
                            required property string modelData
                            required property int index
                            objectName: "channelMode" + modelData
                            width: ListView.view.width
                            text: modelData
                            font.pixelSize: 11
                            highlighted: modeBox.highlightedIndex === index
                            enabled: index === 0 || !!sec.ch.pairable
                        }
                        onActivated: (i) => modeEdit.apply(i)
                        popup.onClosed: if (modeEdit.editing) modeEdit.cancel()
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                elide: Text.ElideRight
                text: sec.ch.hwName || ""; color: Theme.text; font.bold: true; font.pixelSize: 17
            }
            Item {          // user name: double-click to edit in place
                id: nameEdit
                objectName: "channelName"
                Layout.fillWidth: true
                implicitHeight: Math.max(nameLabel.implicitHeight, nameField.implicitHeight)
                property bool editing: false
                // Taken when editing starts, so the name lands on that channel whatever is shown later.
                property var target: null

                function start() {
                    target = { kind: sec.ch.kind, hw: sec.ch.hw, name: sec.ch.userName || "" }
                    nameField.text = target.name
                    editing = true
                    nameField.forceActiveFocus()
                    nameField.selectAll()
                }
                // Enter, or a click anywhere outside the field: apply. Esc: cancel.
                function commit() {
                    if (!editing) return
                    editing = false
                    var t = target, name = nameField.text.trim()
                    target = null
                    if (t && name !== t.name) mixer.rename(t.kind, t.hw, name)
                }
                function cancel() { editing = false; target = null }
                onEditingChanged: if (!editing) nameField.focus = false

                Label {
                    id: nameLabel
                    visible: !nameEdit.editing
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width
                    elide: Text.ElideRight
                    text: sec.ch.userName ? sec.ch.userName : "No name"
                    color: sec.ch.userName ? Theme.text : Theme.textFaint
                    font.pixelSize: 14; font.italic: !sec.ch.userName
                    MouseArea {
                        id: nameMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.IBeamCursor
                        onDoubleClicked: nameEdit.start()
                    }
                    ToolTip.visible: nameMouse.containsMouse
                    ToolTip.delay: 600
                    ToolTip.text: "Double-click to rename" + (sec.ch.stereo ? " (the name applies to the stereo pair)" : "")
                }
                TextField {
                    id: nameField
                    visible: nameEdit.editing
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width
                    font.pixelSize: 14
                    placeholderText: "No name"
                    selectByMouse: true
                    onAccepted: nameEdit.commit()
                    Keys.onEscapePressed: nameEdit.cancel()
                    onActiveFocusChanged: if (!activeFocus) nameEdit.commit()
                }
                // While editing, a press anywhere else in the window applies the name and goes on through to
                // whatever is underneath (clicks on plain items don't take focus from the field by themselves).
                MouseArea {
                    parent: nameEdit.Window.contentItem
                    anchors.fill: parent
                    z: 100000
                    visible: nameEdit.editing
                    onPressed: (m) => {
                        if (!nameField.contains(mapToItem(nameField, m.x, m.y))) nameEdit.commit()
                        m.accepted = false
                    }
                }
            }
        }
        Button {        // mute: lit while muted
            objectName: "channelMute"
            Layout.alignment: Qt.AlignVCenter
            implicitWidth: 34; implicitHeight: 34
            padding: 0
            text: "M"
            font.bold: true; font.pixelSize: 14
            onClicked: mixer.toggleMute(sec.ch.key)
            palette.button: sec.ch.muted ? Theme.mute : Theme.paneAlt
            palette.buttonText: sec.ch.muted ? Theme.accentText : Theme.text
            ToolTip.visible: hovered; ToolTip.delay: 900
            ToolTip.text: (sec.ch.muted ? "Muted. " : "Mute. ") + "Silences every connection "
                          + (sec.isOut ? "to" : "from") + " this channel; their levels are kept. "
                          + "Middle-click a pill does the same."
        }
        ToolButton {
            Layout.alignment: Qt.AlignTop
            Layout.leftMargin: -6       // keeps M and ✕ together
            text: "✕"
            onClicked: mixer.showChannel(sec.ch.key)
            ToolTip.visible: hovered; ToolTip.delay: 600; ToolTip.text: "Close (or click the pill again)"
        }
    }
    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.border }

    Flickable {     // everything under the header scrolls
        id: flick
        objectName: "channelScroll"
        Layout.fillWidth: true
        Layout.fillHeight: true
        contentHeight: body.implicitHeight + 32
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { id: vbar }

        ColumnLayout {
            id: body
            x: 16; y: 16
            // Room on the right for the scroll bar, so it never covers the controls.
            width: flick.width - 32 - vbar.width
            spacing: 6

            // ---------------------------------------------------------- meter and levels, centred
            RowLayout {         // meter, with the fader beside it for an output: centred as one
                Layout.alignment: Qt.AlignHCenter
                spacing: 14
                DetailMeter {
                    id: meter
                    objectName: "channelMeter"
                    Layout.alignment: Qt.AlignTop
                    // Room above for the fader's travel above 0 dB
                    Layout.topMargin: sec.isOut ? Math.max(0, fader.to * fader.dbPx + fader.handleSize / 2 - barTop) : 0
                    Layout.preferredHeight: 280
                    levels: mixer.channelLevels
                    sides: sec.ch.sides || [""]
                }
                // Output level fader on the meter's scale: same dB per pixel, its 0 dB level with the meter's 0
                // and -60 with -60; the top, up to +6 dB, rises above the meter.
                ColumnLayout {
                    visible: sec.isOut
                    Layout.alignment: Qt.AlignTop
                    spacing: 0
                    Item {
                        implicitHeight: meter.Layout.topMargin + meter.barTop - fader.to * fader.dbPx - fader.handleSize / 2
                    }
                    Slider {
                        id: fader
                        objectName: "channelFader"
                        readonly property real dbPx: meter.barH / -meter.floorDb
                        readonly property real handleSize: handle ? Math.max(handle.width, handle.height) : 13
                        // The handle's centre travels the slider's length less one handle
                        Layout.preferredHeight: (to - from) * dbPx + handleSize
                        orientation: Qt.Vertical
                        from: -60; to: 6; stepSize: 0.5
                        value: Math.max(-60, sec.ch.outLevelDb || 0)
                        // Write while dragging, at most ten times a second; the last value is always written.
                        onMoved: faderTimer.restart()
                        // Double-click: back to 0 dB. Told apart here, as the slider takes the mouse itself.
                        property double lastPress: 0
                        property bool doubleClicked: false
                        onPressedChanged: {
                            if (pressed) {
                                var now = Date.now()
                                doubleClicked = now - lastPress < Qt.styleHints.mouseDoubleClickInterval
                                lastPress = doubleClicked ? 0 : now
                                return
                            }
                            faderTimer.stop()
                            if (doubleClicked) {
                                doubleClicked = false
                                mixer.setOutputLevel(sec.ch.key, "0")
                                value = Qt.binding(() => Math.max(-60, sec.ch.outLevelDb || 0))
                            } else {
                                write()
                            }
                        }
                        function write() { mixer.setOutputLevel(sec.ch.key, value.toFixed(1)) }
                        Timer { id: faderTimer; interval: 100; onTriggered: fader.write() }
                        ToolTip.visible: hovered || pressed
                        ToolTip.delay: pressed ? 0 : 900
                        ToolTip.text: pressed ? value.toFixed(1) + " dB"
                                              : "Output level, -60 to +6 dB. Double-click for 0 dB; type -inf "
                                                + "below to silence it."
                    }
                }
            }
            Button {        // playback channels: test signal (core/tone.py)
                objectName: "channelTest"
                visible: sec.ch.kind === "pb"
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: 4
                text: sec.ch.testing ? "Stop Test" : "Test"
                onClicked: mixer.testChannel(sec.ch.key)
                ToolTip.visible: hovered; ToolTip.delay: 900
                ToolTip.text: "Plays three sine bursts at -6 dBFS (880, 440, 220 Hz; half a second each) on this "
                              + "channel" + (sec.ch.stereo ? ", left first, then right" : "")
                              + ". Heard wherever it is routed."
            }
            Note {
                visible: !mixer.metersEnabled
                horizontalAlignment: Text.AlignHCenter
                text: "Meters are off (View › Show Meters)."
            }

            // Output level (outputs only)
            Heading { visible: sec.isOut; text: "Output Level"; Layout.topMargin: 6; Layout.alignment: Qt.AlignHCenter }
            RowLayout {
                visible: sec.isOut
                Layout.fillWidth: true
                spacing: 6
                TextField {
                    id: levelField
                    objectName: "channelLevelBox"
                    Layout.fillWidth: true
                    horizontalAlignment: TextInput.AlignHCenter
                    font.family: "monospace"
                    text: sec.ch.outLevelText || "0.0"
                    selectByMouse: true
                    // As the name in the header: Enter, or a click anywhere outside the box, applies; Esc cancels.
                    // Taken when editing starts, so the level lands on that output whatever is shown later.
                    property var target: null
                    function apply() {      // false: not a level (the bridge says so); the box keeps it to fix
                        var t = target
                        if (!t || text.trim() === t.text) return true
                        return mixer.setOutputLevel(t.key, text)
                    }
                    onAccepted: if (apply()) { target = null; focus = false }
                    Keys.onEscapePressed: { target = null; focus = false }
                    onActiveFocusChanged: {
                        if (activeFocus) {
                            target = { key: sec.ch.key, text: text }
                            return
                        }
                        apply()             // focus gone: a click outside, Tab, or another channel shown
                        target = null
                        text = Qt.binding(() => sec.ch.outLevelText || "0.0")
                    }
                    // While editing, a press anywhere else in the window applies the level and goes on through to
                    // whatever is underneath (clicks on plain items don't take focus from the box by themselves).
                    MouseArea {
                        parent: levelField.Window.contentItem
                        anchors.fill: parent
                        z: 100000
                        visible: levelField.activeFocus
                        onPressed: (m) => {
                            if (!levelField.contains(mapToItem(levelField, m.x, m.y))) levelField.focus = false
                            m.accepted = false
                        }
                    }
                }
                Label { text: "dB"; color: Theme.textDim }
                Button {
                    text: "0 dB"
                    enabled: !!sec.ch.outLevelSet
                    onClicked: mixer.setOutputLevel(sec.ch.key, "0")
                }
            }
            Note { visible: sec.isOut; horizontalAlignment: Text.AlignHCenter; text: "Scales everything routed to this output." }

            // ---------------------------------------------------------- the channel's card settings
            // (the pill's submenus), one heading per group
            Repeater {
                model: sec.ch.settingGroups || []
                delegate: ColumnLayout {
                    id: group
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    Layout.topMargin: 14
                    spacing: 4
                    Heading { text: group.modelData.title }
                    Repeater {
                        model: group.modelData.settings
                        delegate: ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label {
                                visible: modelData.label !== ""
                                text: modelData.label
                                color: Theme.textFaint; font.pixelSize: 11
                            }
                            ComboBox {
                                Layout.fillWidth: true
                                model: modelData.texts
                                currentIndex: modelData.choices.indexOf(modelData.value)
                                onActivated: (i) => mixer.setCardSetting(modelData.id, modelData.choices[i])
                            }
                        }
                    }
                }
            }

            // ---------------------------------------------------------- connections and their levels
            Heading {
                Layout.topMargin: 14
                text: sec.isOut ? "Routed From" : "Routed To"
            }
            Repeater {
                model: sec.ch.connections || []
                delegate: RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Label {
                        id: other
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                        text: (modelData.otherKind === "in" ? "IN  " : modelData.otherKind === "pb" ? "PB  " : "OUT  ")
                              + modelData.otherName + (modelData.otherUserName ? "  " + modelData.otherUserName : "")
                        color: otherMouse.containsMouse ? Theme.text : Theme.kindColor(modelData.otherKind)
                        font.underline: otherMouse.containsMouse
                        MouseArea {
                            id: otherMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: mixer.showChannel(modelData.otherKey)
                        }
                        ToolTip.visible: otherMouse.containsMouse; ToolTip.delay: 900
                        ToolTip.text: "Show this channel"
                    }
                    GainLabel {
                        conn: modelData
                        wireColor: Theme.wireColor(modelData, mixer.hueStep)
                    }
                }
            }
            Note {
                visible: !(sec.ch.connections || []).length
                text: "Not routed."
            }
            Button {
                visible: sec.isOut
                Layout.fillWidth: true
                Layout.topMargin: 14
                text: "Remove All Routes to This Output"
                enabled: (sec.ch.connections || []).length > 0
                onClicked: mixer.removeAllTo(sec.ch.key)
            }
        }
    }
}
