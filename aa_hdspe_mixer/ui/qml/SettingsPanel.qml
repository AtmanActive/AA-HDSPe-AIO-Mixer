import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Right-hand side panel, always shown. It shows one section at a time, picked by its toolbar button
// (the buttons work like radio buttons; clicking the open section's button again empties the panel):
//  - card settings (hammer): the card's other settings, from core/card_settings.py (panel=True);
//  - clock (clock button): sample rate, clock source with input status, fine tuning (ClockSection.qml);
//  - virtual devices (plug button): stereo PipeWire devices on the card's channels (PipeWireSection.qml),
//    with Add Input / Add Output in its header;
//  - MIDI (MIDI button): MIDI remote control bindings (MidiSection.qml), with the on/off switch, the ports and
//    Add in its header;
//  - OSC (OSC button): the same over the network (OscSection.qml), with the on/off switch, the UDP ports, the
//    feedback host and Add in its header;
//  - keyboard shortcuts (keyboard button): the same from KDE Plasma's global shortcuts (KeysSection.qml), with
//    the on/off switch and Add in its header;
//  - app settings (cog button): the app's own, app-wide settings (AppSettingsSection.qml);
//  - channel (a pill clicked in the routing view): that channel's meter and controls (ChannelSection.qml).
//    It has its own scrolling, under a header that stays put.
// Every section has a header that stays put, with ✕ to close it, and scrolls below it. The MIDI section also
// has a footer that stays put at the bottom: a link to a MIDI router.
// With none open the panel stays, empty. The panel is the same for every card; the contents depend on
// the card. Nothing in it is stored in presets.
Rectangle {
    id: panel
    color: Theme.pane
    implicitWidth: 290

    Rectangle { width: 1; height: parent.height; color: Theme.border }

    // Header of the hammer, clock, plug, MIDI, OSC, keyboard and cog sections, as on the channel section: stays put, with ✕ to close.
    readonly property bool sectionOpen: mixer.settingsPanelOpen || mixer.clockPanelOpen || mixer.pipewirePanelOpen
                                        || mixer.midiPanelOpen || mixer.oscPanelOpen || mixer.keysPanelOpen
                                        || mixer.appSettingsOpen
    function closeSection() {
        if (mixer.settingsPanelOpen) mixer.settingsPanelOpen = false
        else if (mixer.clockPanelOpen) mixer.clockPanelOpen = false
        else if (mixer.pipewirePanelOpen) mixer.pipewirePanelOpen = false
        else if (mixer.midiPanelOpen) mixer.midiPanelOpen = false
        else if (mixer.oscPanelOpen) mixer.oscPanelOpen = false
        else if (mixer.keysPanelOpen) mixer.keysPanelOpen = false
        else if (mixer.appSettingsOpen) mixer.appSettingsOpen = false
    }
    RowLayout {
        id: header
        visible: panel.sectionOpen && !mixer.channelOpen
        x: 17; y: 16
        width: panel.width - 33 - vbar.width      // lines up with the content below and its scroll bar
        spacing: 12
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 1
            Label {
                text: mixer.settingsPanelOpen ? "CARD SETTINGS" : mixer.clockPanelOpen ? "CLOCK"
                    : mixer.pipewirePanelOpen ? "PIPEWIRE VIRTUAL DEVICES"
                    : mixer.midiPanelOpen ? "MIDI REMOTE CONTROL"
                    : mixer.oscPanelOpen ? "OSC REMOTE CONTROL"
                    : mixer.keysPanelOpen ? "KEYBOARD SHORTCUTS" : "APP SETTINGS"
                color: Theme.textFaint; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.6
            }
            RowLayout {     // the plug section's header: its buttons in place of a title
                visible: mixer.pipewirePanelOpen
                Layout.topMargin: 4
                spacing: 8
                Button {
                    objectName: "vdAddInput"
                    text: "Add Input"
                    enabled: mixer.virtualNote === ""
                    onClicked: { pwSec.addDraft("input"); flick.contentY = 0 }      // the new box is on top
                }
                Button {
                    objectName: "vdAddOutput"
                    text: "Add Output"
                    enabled: mixer.virtualNote === ""
                    onClicked: { pwSec.addDraft("output"); flick.contentY = 0 }
                }
            }
            ColumnLayout {  // the MIDI section's header: on/off, the ports, Add
                visible: mixer.midiPanelOpen
                Layout.fillWidth: true
                Layout.topMargin: 4
                spacing: 6
                Switch {
                    objectName: "midiEnable"
                    leftPadding: 0
                    text: mixer.midiEnabled ? "On" : "Off"
                    checked: mixer.midiEnabled
                    onToggled: mixer.midiEnabled = checked
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 8; rowSpacing: 6
                    Label { text: "In"; color: Theme.textDim }
                    ComboBox {
                        objectName: "midiIn"
                        Layout.fillWidth: true
                        enabled: mixer.midiEnabled
                        model: mixer.midiInputs.map(p => p.text)
                        currentIndex: mixer.midiInputs.findIndex(p => p.value === mixer.midiInput)
                        onActivated: (i) => mixer.setMidiInput(mixer.midiInputs[i].value)
                        ToolTip.visible: hovered; ToolTip.delay: 600
                        ToolTip.text: "The controller the bindings listen to"
                    }
                    Label { text: "Out"; color: Theme.textDim }
                    ComboBox {
                        objectName: "midiOut"
                        Layout.fillWidth: true
                        enabled: mixer.midiEnabled
                        model: mixer.midiOutputs.map(p => p.text)
                        currentIndex: mixer.midiOutputs.findIndex(p => p.value === mixer.midiOutput)
                        onActivated: (i) => mixer.setMidiOutput(mixer.midiOutputs[i].value)
                        ToolTip.visible: hovered; ToolTip.delay: 600
                        ToolTip.text: "Where the mutes and levels are sent back to (lights, motor faders)"
                    }
                }
                Button {
                    objectName: "midiAdd"
                    text: "Add"
                    onClicked: { midiSec.addDraft(); flick.contentY = 0 }      // the new box is on top
                }
            }
            ColumnLayout {  // the OSC section's header: on/off, where it listens, where feedback goes, Add
                visible: mixer.oscPanelOpen
                Layout.fillWidth: true
                Layout.topMargin: 4
                spacing: 6
                Switch {
                    objectName: "oscEnable"
                    leftPadding: 0
                    text: mixer.oscEnabled ? "On" : "Off"
                    checked: mixer.oscEnabled
                    onToggled: mixer.oscEnabled = checked
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 8; rowSpacing: 6
                    Label { text: "In"; color: Theme.textDim }
                    SpinBox {
                        id: inPortBox
                        objectName: "oscInPort"
                        Layout.fillWidth: true
                        enabled: mixer.oscEnabled
                        editable: true
                        from: 1; to: 65535
                        value: mixer.oscListenPort
                        textFromValue: (v) => "UDP port " + v
                        valueFromText: (t) => { var v = parseInt(t.replace(/[^0-9]/g, "")); return isNaN(v) ? value : v }
                        validator: RegularExpressionValidator { regularExpression: /.*/ }
                        onValueModified: inPortApply.restart()      // once the arrows stop, not on every step
                        Timer { id: inPortApply; interval: 600
                                onTriggered: mixer.setOscListen(inPortBox.value, mixer.oscListenNetwork) }
                        ToolTip.visible: hovered; ToolTip.delay: 600
                        ToolTip.text: "The UDP port controllers send OSC to"
                    }
                    Label { text: "From"; color: Theme.textDim }
                    ComboBox {
                        objectName: "oscScope"
                        Layout.fillWidth: true
                        enabled: mixer.oscEnabled
                        model: mixer.oscChoices.scopes.map(c => c.text)
                        currentIndex: mixer.oscListenNetwork ? 1 : 0
                        onActivated: (i) => mixer.setOscListen(mixer.oscListenPort, i === 1)
                        ToolTip.visible: hovered; ToolTip.delay: 600
                        ToolTip.text: "Who may send: OSC has no passwords, so on the network anyone who can reach "
                                      + "the port can control the mixer"
                    }
                    Label { text: "Out"; color: Theme.textDim }
                    TextField {
                        objectName: "oscOutHost"
                        Layout.fillWidth: true
                        enabled: mixer.oscEnabled
                        placeholderText: "Host (none: no feedback)"
                        text: mixer.oscSendHost
                        onEditingFinished: mixer.setOscSend(text, mixer.oscSendPort)
                        ToolTip.visible: hovered; ToolTip.delay: 600
                        ToolTip.text: "The controller's name or address, where mutes and levels are sent back to"
                    }
                    Item { implicitWidth: 1; implicitHeight: 1 }
                    SpinBox {
                        id: outPortBox
                        objectName: "oscOutPort"
                        Layout.fillWidth: true
                        enabled: mixer.oscEnabled
                        editable: true
                        from: 1; to: 65535
                        value: mixer.oscSendPort
                        textFromValue: (v) => "UDP port " + v
                        valueFromText: (t) => { var v = parseInt(t.replace(/[^0-9]/g, "")); return isNaN(v) ? value : v }
                        validator: RegularExpressionValidator { regularExpression: /.*/ }
                        onValueModified: outPortApply.restart()
                        Timer { id: outPortApply; interval: 600
                                onTriggered: mixer.setOscSend(mixer.oscSendHost, outPortBox.value) }
                        ToolTip.visible: hovered; ToolTip.delay: 600
                        ToolTip.text: "The UDP port the controller listens on"
                    }
                }
                Button {
                    objectName: "oscAdd"
                    text: "Add"
                    onClicked: { oscSec.addDraft(); flick.contentY = 0 }       // the new box is on top
                }
            }
            ColumnLayout {  // the keyboard shortcuts section's header: on/off, Add
                visible: mixer.keysPanelOpen
                Layout.fillWidth: true
                Layout.topMargin: 4
                spacing: 6
                Switch {
                    objectName: "keysEnable"
                    leftPadding: 0
                    text: mixer.keysEnabled ? "On" : "Off"
                    checked: mixer.keysEnabled
                    onToggled: mixer.keysEnabled = checked
                }
                Button {
                    objectName: "keysAdd"
                    text: "Add"
                    onClicked: { keysSec.addDraft(); flick.contentY = 0 }      // the new box is on top
                }
            }
            Label {
                visible: !mixer.pipewirePanelOpen && !mixer.midiPanelOpen && !mixer.oscPanelOpen
                         && !mixer.keysPanelOpen
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: mixer.settingsPanelOpen ? "RME " + mixer.cardModel
                    : mixer.clockPanelOpen ? mixer.clockRunningText : mixer.appName
                color: Theme.text; font.bold: true; font.pixelSize: mixer.clockPanelOpen ? 13 : 14
            }
        }
        ToolButton {
            objectName: "panelClose"
            Layout.alignment: Qt.AlignTop
            text: "✕"
            onClicked: panel.closeSection()
            ToolTip.visible: hovered; ToolTip.delay: 600; ToolTip.text: "Close (or click its toolbar button again)"
        }
    }
    Rectangle {
        id: headerLine
        visible: header.visible
        x: 1; y: header.y + header.height + 12
        width: panel.width - 1; height: 1
        color: Theme.border
    }

    Flickable {
        id: flick
        visible: panel.sectionOpen && !mixer.channelOpen
        anchors.fill: parent
        anchors.leftMargin: 1
        anchors.topMargin: headerLine.y + 1
        anchors.bottomMargin: footer.visible ? footer.height : 0
        contentHeight: sections.implicitHeight + 32
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { id: vbar }

        ColumnLayout {
          id: sections
          x: 16; y: 16
          // Room on the right for the scroll bar, so it never covers the controls.
          width: panel.width - 32 - vbar.width
          spacing: 18

          ColumnLayout {
            id: col
            visible: mixer.settingsPanelOpen
            Layout.fillWidth: true
            spacing: 6

            Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: "Saved with the card's memory, not in presets."
                color: Theme.textFaint; font.pixelSize: 11
            }
            Item { implicitHeight: 6 }

            Repeater {
                model: mixer.panelSettings
                delegate: ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Label {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: modelData.label
                        color: modelData.available ? Theme.textDim : Theme.textFaint; font.bold: true
                    }
                    Label {
                        visible: modelData.description !== ""
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: modelData.description
                        color: Theme.textFaint; font.pixelSize: 11
                    }
                    ComboBox {
                        Layout.fillWidth: true
                        enabled: modelData.available
                        model: modelData.texts
                        currentIndex: modelData.choices.indexOf(modelData.value)
                        displayText: modelData.available ? currentText : "—"
                        onActivated: (i) => mixer.setCardSetting(modelData.id, modelData.choices[i])
                    }
                    Label {
                        visible: modelData.note !== ""
                        text: modelData.note
                        color: Theme.textFaint; font.pixelSize: 11; font.italic: true
                    }
                    Item { implicitHeight: 8 }
                }
            }
            Label {
                visible: mixer.panelSettings.length === 0
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: "No extra settings for this card yet."
                color: Theme.textFaint
            }
          }

          ClockSection {
              id: clock
              visible: mixer.clockPanelOpen
              Layout.fillWidth: true
          }

          PipeWireSection {
              id: pwSec
              visible: mixer.pipewirePanelOpen
              Layout.fillWidth: true
          }

          MidiSection {
              id: midiSec
              visible: mixer.midiPanelOpen
              Layout.fillWidth: true
          }

          OscSection {
              id: oscSec
              visible: mixer.oscPanelOpen
              Layout.fillWidth: true
          }

          KeysSection {
              id: keysSec
              visible: mixer.keysPanelOpen
              Layout.fillWidth: true
          }

          AppSettingsSection {
              id: appSec
              visible: mixer.appSettingsOpen
              Layout.fillWidth: true
          }
        }
    }

    // The MIDI section's footer: at the bottom of the panel however many bindings there are.
    Rectangle {
        id: footer
        visible: mixer.midiPanelOpen && !mixer.channelOpen
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.leftMargin: 1
        height: routerLink.implicitHeight + 24
        color: Theme.pane
        Rectangle { width: parent.width; height: 1; color: Theme.border }
        Label {
            id: routerLink
            objectName: "midiRouterLink"
            readonly property string url: "https://github.com/AtmanActive/Linux-MIDI-snd_seq_dummy-Router"
            x: 16; anchors.verticalCenter: parent.verticalCenter
            width: parent.width - 32
            elide: Text.ElideRight
            textFormat: Text.StyledText
            linkColor: Theme.accent          // the default blue is unreadable on the dark theme
            text: "<a href=\"" + url + "\">Need a MIDI router?</a>"
            onLinkActivated: (link) => Qt.openUrlExternally(link)
            HoverHandler { id: hoverLink; cursorShape: Qt.PointingHandCursor }
            ToolTip.visible: hoverLink.hovered; ToolTip.delay: 600; ToolTip.text: url
        }
    }

    ChannelSection {        // fills the panel, with its own fixed header and scrolling
        visible: mixer.channelOpen
        anchors.fill: parent
        anchors.leftMargin: 1
    }
}
