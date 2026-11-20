import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: win
    // Just the app name. Qt appends the application display name to a title that doesn't already end
    // with it, which doubled it before; a title equal to the name gets nothing appended.
    title: mixer.appName
    width: 1100; height: 720
    minimumWidth: 520; minimumHeight: 420
    visible: false        // shown by Python (or kept hidden in the tray)
    color: Theme.window

    palette {
        window: Theme.window; windowText: Theme.text; base: Theme.pane; alternateBase: Theme.paneAlt
        text: Theme.text; button: Theme.paneAlt; buttonText: Theme.text; highlight: Theme.accent
        highlightedText: Theme.accentText; toolTipBase: Theme.bar; toolTipText: Theme.text
        placeholderText: Theme.textFaint; mid: Theme.border; dark: Theme.bar; light: Theme.paneAlt
    }

    Binding { target: Theme; property: "dark"; value: mixer.darkMode }   // cog panel: Colour mode

    onClosing: (close) => { close.accepted = false; shell.hideToTray() }

    // --- testing hooks (used with --qml-call for screenshots) -----------------------------------
    function openAdd() { addOverlay.openFresh() }
    function hoverUnit(key) { routing.hotUnit = key }
    function recallFirst() { mixer.requestRecall(0) }
    function addRoute(spec) { var p = spec.split(">"); mixer.addConnections(p[0], [p[1]], "0") }   // "in:0>out:6"
    function muteUnit(key) { mixer.toggleMute(key) }
    function openPanel() { mixer.settingsPanelOpen = true }
    function openClock() { mixer.clockPanelOpen = true }
    function openAppSettings() { mixer.appSettingsOpen = true }
    function showChannel(key) { mixer.showChannel(key) }                                    // "out:6"
    function setColorMode(mode) { mixer.setColorMode(mode) }
    function setZoom(level) { mixer.setZoom(parseInt(level)) }
    function setSetting(spec) { var p = spec.split("="); mixer.setCardSetting(p[0], p[1]) }     // "spdif_in=Optical"
    function setLevel(spec) { var p = spec.split("="); mixer.setOutputLevel(p[0], p[1]) }   // "out:10=-12"
    function openUnitMenu(key) { var u = mixer.inputs.concat(mixer.playbacks, mixer.outputs).find(x => x.key === key); unitMenu.openFor(u, routing, 300, 120) }
    function muteRoute(spec) { var p = spec.split(">"); mixer.toggleConnectionMute(p[0], p[1]) }   // "in:0>out:6"

    // ============================================================================ menus
    menuBar: MenuBar {
        Menu {
            title: "&File"
            Action { text: "&Load Preset from File…"; onTriggered: shell.loadPresetFile() }
            Action {
                text: "&Save Preset to File…"
                onTriggered: shell.savePresetFile(mixer.presetNames[presetBox.currentIndex] || "Preset")
            }
            MenuSeparator {}
            Action { text: "&Export Memory…"; onTriggered: shell.exportMemory() }
            Action { text: "&Import Memory…"; onTriggered: shell.importMemory() }
            MenuSeparator {}
            Action { text: "&Hide to Tray"; shortcut: "Ctrl+H"; onTriggered: shell.hideToTray() }
            Action { text: "&Quit"; shortcut: StandardKey.Quit; onTriggered: shell.quit() }
        }
        Menu {
            title: "&Edit"
            Action { text: "&Add Connection…"; shortcut: "Ctrl+N"; onTriggered: addOverlay.openFresh() }
            MenuSeparator {}
            Action { text: "&Store Preset…"; shortcut: "Ctrl+S"; onTriggered: dialogs.store.openFor(presetBox.currentIndex) }
            Action {
                text: "&Recall Preset…"; shortcut: "Ctrl+R"
                enabled: !!mixer.presetNames[presetBox.currentIndex]
                onTriggered: mixer.requestRecall(presetBox.currentIndex)
            }
            Action {
                text: "&Delete Preset"
                enabled: !!mixer.presetNames[presetBox.currentIndex]
                onTriggered: mixer.deletePreset(presetBox.currentIndex)
            }
            MenuSeparator {}
            Action { text: "&Clear All Routing…"; onTriggered: dialogs.confirmClear.open() }
        }
        Menu {
            title: "&View"
            Action {
                text: "Show &Meters"; checkable: true; checked: mixer.metersEnabled
                onTriggered: mixer.metersEnabled = checked
            }
            Menu {
                title: "Meter &Refresh Rate"
                Repeater {
                    model: mixer.meterRates
                    delegate: MenuItem {
                        text: modelData + " Hz"
                        checkable: true
                        checked: mixer.meterRate === modelData
                        onTriggered: mixer.meterRate = modelData
                    }
                }
            }
            Action { text: "Wire &Colours…"; onTriggered: dialogs.hueStep.openNow() }
            MenuSeparator {}
            Action { text: "&Resend Mixer to Card"; onTriggered: mixer.resendAll() }
        }
        Menu {
            title: "A&bout"
            Action { text: "&About " + mixer.appName + "…"; onTriggered: dialogs.about.open() }
        }
    }

    // ============================================================================ info bar (always visible)
    header: Rectangle {
        color: Theme.bar
        implicitHeight: infoRow.height + 14
        // Driver on the left, card in the middle, sample rate on the right. When the window is too narrow for
        // one line, they stack, all on the left.
        Item {
            id: infoRow
            readonly property int gap: 22
            property var parts: [null, null, null]           // filled as the Repeater makes them
            readonly property bool ready: parts[0] !== null && parts[1] !== null && parts[2] !== null
            readonly property bool oneLine: !ready
                || parts[0].width + parts[1].width + parts[2].width + 2 * gap <= width
            x: 14; y: 7
            width: parent.width - 28
            height: !ready ? 0 : oneLine ? Math.max(parts[0].height, parts[1].height, parts[2].height)
                                         : parts[0].height + parts[1].height + parts[2].height + 8
            function place(i, w, h) {
                if (!ready || !oneLine || i === 0) return 0
                if (i === 2) return width - w
                const lo = parts[0].width + gap, hi = width - parts[2].width - gap - w
                return Math.max(lo, Math.min(hi, Math.round((width - w) / 2)))
            }
            Repeater {
                id: infoRep
                onItemAdded: (i, it) => { const p = infoRow.parts.slice(); p[i] = it; infoRow.parts = p }
                onItemRemoved: (i, it) => { const p = infoRow.parts.slice(); p[i] = null; infoRow.parts = p }
                model: [
                    { label: "DRIVER", value: mixer.driverName, tip: mixer.driverTip, id: "infoDriver" },
                    { label: "CARD", value: mixer.cardName, tip: mixer.cardTip, id: "infoCard" },
                    { label: "SAMPLE RATE", value: mixer.rateName, tip: mixer.rateTip, id: "infoRate" }
                ]
                delegate: Row {          // the name only; the details are in the tooltip
                    objectName: modelData.id
                    property string tip: modelData.tip
                    spacing: 8
                    x: infoRow.place(index, width, height)
                    y: infoRow.oneLine || !infoRow.ready ? 0
                       : index === 0 ? 0 : index === 1 ? infoRow.parts[0].height + 4
                       : infoRow.parts[0].height + infoRow.parts[1].height + 8
                    Text { text: modelData.label; color: Theme.textFaint; font.pixelSize: 10; font.bold: true
                           anchors.verticalCenter: parent.verticalCenter
                           anchors.verticalCenterOffset: 1 }
                    Text { text: modelData.value; color: Theme.text; font.pixelSize: 13 }
                    HoverHandler { id: infoHover }
                    ToolTip.visible: infoHover.hovered
                    ToolTip.delay: 600
                    ToolTip.text: tip
                }
            }
        }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }
    }

    // ============================================================================ body
    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // Operational buttons
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: tools.implicitHeight + 16
            color: Theme.pane
            Flow {
                id: tools
                anchors.fill: parent
                anchors.margins: 8
                anchors.leftMargin: 14; anchors.rightMargin: 14
                spacing: 8
                // One height for every item on the toolbar (buttons, drop-down, dividers, label), so they line
                // up exactly whatever the style: the tallest natural height of the controls, at least 30 px.
                readonly property int toolH: Math.max(30, addBtn.implicitHeight, presetBox.implicitHeight,
                                                      storeBtn.implicitHeight)

                component Sep: Rectangle { width: 1; height: tools.toolH; color: Theme.border }
                component ToolButtonBase: Button {
                    height: tools.toolH
                    topInset: 0; bottomInset: 0         // the background fills the whole height
                }
                component IconButton: ToolButtonBase {  // toggles a section of the side panel
                    id: iconBtn
                    property string tip
                    property url image                  // a picture instead of the emoji, where there's none
                    font.family: "Noto Color Emoji"     // falls back to any emoji font if missing
                    font.pixelSize: Math.round(tools.toolH * 0.65)
                    padding: 0
                    width: tools.toolH + 8
                    highlighted: checked
                    checkable: true
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: tip
                    Image {
                        visible: iconBtn.image != ""
                        anchors.centerIn: parent
                        width: Math.round(iconBtn.font.pixelSize * 1.15); height: width   // an emoji's size
                        sourceSize.width: width * 2; sourceSize.height: height * 2
                        source: iconBtn.image
                        smooth: true
                    }
                }

                ToolButtonBase {
                    id: addBtn
                    text: "＋  Add"
                    highlighted: true
                    onClicked: addOverlay.openFresh()
                }
                Sep {}
                ComboBox {
                    id: presetBox
                    width: 260; height: tools.toolH
                    topInset: 0; bottomInset: 0
                    model: mixer.presetNames.map(function (n, i) {
                        return (i + 1 < 10 ? "0" : "") + (i + 1) + "   " + (n ? n : "(empty)")
                    })
                    currentIndex: 0
                }
                ToolButtonBase { id: storeBtn; text: "Store"; onClicked: dialogs.store.openFor(presetBox.currentIndex) }
                ToolButtonBase {
                    text: "Recall"
                    enabled: !!mixer.presetNames[presetBox.currentIndex]
                    onClicked: mixer.requestRecall(presetBox.currentIndex)
                }
                Sep {}
                Label {
                    height: tools.toolH; verticalAlignment: Text.AlignVCenter
                    text: mixer.connections.length + (mixer.connections.length === 1 ? " connection" : " connections")
                    color: Theme.textDim
                }
                Sep {}
                IconButton {
                    text: "🔨"; tip: "Card settings"
                    checked: mixer.settingsPanelOpen
                    onToggled: mixer.settingsPanelOpen = checked
                }
                IconButton {
                    text: "🕐"; tip: "Card sample clock"
                    checked: mixer.clockPanelOpen
                    onToggled: mixer.clockPanelOpen = checked
                }
                IconButton {
                    text: "🔌"; tip: "PipeWire virtual devices"
                    objectName: "pipewireButton"
                    checked: mixer.pipewirePanelOpen
                    onToggled: mixer.pipewirePanelOpen = checked
                }
                IconButton {
                    image: "../../resources/midi-din.svg"; tip: "MIDI remote control"   // no emoji for it
                    objectName: "midiButton"
                    checked: mixer.midiPanelOpen
                    onToggled: mixer.midiPanelOpen = checked
                }
                IconButton {
                    text: "📡"; tip: "OSC remote control"
                    objectName: "oscButton"
                    checked: mixer.oscPanelOpen
                    onToggled: mixer.oscPanelOpen = checked
                }
                IconButton {
                    text: "⌨️"; tip: "Keyboard shortcuts (KDE Plasma)"
                    objectName: "keysButton"
                    checked: mixer.keysPanelOpen
                    onToggled: mixer.keysPanelOpen = checked
                }
                IconButton {
                    text: "⚙️"; tip: "App settings"
                    checked: mixer.appSettingsOpen
                    onToggled: mixer.appSettingsOpen = checked
                }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0
            RoutingView {
                id: routing
                Layout.fillWidth: true
                Layout.fillHeight: true
            }
            SettingsPanel {            // always shown; one section at a time (toolbar buttons, or a pill clicked)
                Layout.fillHeight: true
            }
        }
    }

    // ============================================================================ status line
    footer: Rectangle {
        color: Theme.bar
        implicitHeight: 26
        visible: status.text !== ""
        Text {
            id: status
            anchors.fill: parent
            anchors.leftMargin: 14; anchors.rightMargin: 14
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            property string level: "info"
            text: mixer.errorText !== "" ? mixer.errorText : flashText
            color: mixer.errorText !== "" || level === "error" ? Theme.error
                 : level === "warning" ? Theme.warning : Theme.textDim
            property string flashText: ""
        }
        Timer { id: statusTimer; interval: 5000; onTriggered: status.flashText = "" }
        Connections {
            target: mixer
            function onRecallRequested(slot, name, hasNames) { dialogs.recall.openFor(slot, name, hasNames) }
            function onImportRequested(name, parts, presets) { dialogs.importer.openFor(name, parts, presets) }
            function onNotify(level, text) {
                status.level = level; status.flashText = text; statusTimer.restart()
            }
        }
    }

    Connections {
        target: mixer
        function onLevelsChanged() { Meters.levels = mixer.levels }
    }

    UnitMenu { id: unitMenu }
    Dialogs { id: dialogs; anchors.fill: parent }
    AddOverlay { id: addOverlay }
}
