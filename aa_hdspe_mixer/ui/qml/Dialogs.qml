import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Small modal dialogs used from several places. Instantiated once in Main.qml.
Item {
    id: dialogs
    property alias rename: renameDlg
    property alias store: storeDlg
    property alias about: aboutDlg
    property alias confirmClear: clearDlg
    property alias outLevel: outLevelDlg
    property alias hueStep: hueDlg
    property alias recall: recallDlg
    property alias importer: importDlg

    Dialog {
        id: renameDlg
        property var unit: ({})
        function openFor(u) { unit = u; nameField.text = u.userName || ""; open(); nameField.forceActiveFocus(); nameField.selectAll() }
        property int contentWidthHint: 380
        title: "Rename " + (unit.kindLabel || "") + " " + (unit.hwName || "")
        modal: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(contentWidthHint, (parent ? parent.width : 800) - 40)
        standardButtons: Dialog.Ok | Dialog.Cancel
        ColumnLayout {
            spacing: 8
            Label { text: "Name (leave empty to clear):" }
            TextField { id: nameField; Layout.preferredWidth: 280; onAccepted: renameDlg.accept() }
            Label {
                visible: !!renameDlg.unit.stereo
                text: "The name applies to the stereo pair."
                color: Theme.textFaint
            }
        }
        onAccepted: mixer.rename(unit.kind, unit.hw, nameField.text)
    }

    Dialog {
        id: storeDlg
        property int slot: 0
        function openFor(s) {
            slot = s
            var cur = mixer.presetNames[s]
            storeName.text = cur ? cur : "Preset " + (s + 1)
            open(); storeName.forceActiveFocus(); storeName.selectAll()
        }
        property int contentWidthHint: 380
        title: "Store Preset " + (slot + 1)
        modal: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(contentWidthHint, (parent ? parent.width : 800) - 40)
        standardButtons: Dialog.Ok | Dialog.Cancel
        ColumnLayout {
            spacing: 8
            Label { text: "Preset name:" }
            TextField { id: storeName; Layout.preferredWidth: 280; onAccepted: storeDlg.accept() }
            Label {
                visible: !!mixer.presetNames[storeDlg.slot]
                text: "Slot " + (storeDlg.slot + 1) + " holds “" + mixer.presetNames[storeDlg.slot] + "”; it will be replaced."
                color: Theme.warning
            }
        }
        onAccepted: mixer.storePreset(slot, storeName.text)
    }

    Dialog {
        id: outLevelDlg
        property var unit: ({})
        function openFor(u) {
            unit = u; levelField.text = u.outLevelText; open(); levelField.forceActiveFocus(); levelField.selectAll()
        }
        property int contentWidthHint: 380
        title: "Output Level: " + (unit.hwName || "") + (unit.userName ? " (" + unit.userName + ")" : "")
        modal: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(contentWidthHint, (parent ? parent.width : 800) - 40)
        standardButtons: Dialog.Ok | Dialog.Cancel
        ColumnLayout {
            spacing: 8
            Label { text: "Level in dB (-inf … +6.0, default 0):" }
            TextField {
                id: levelField
                Layout.preferredWidth: 120
                horizontalAlignment: TextInput.AlignHCenter
                font.family: "monospace"
                onAccepted: outLevelDlg.accept()
            }
            Label {
                Layout.preferredWidth: 330
                wrapMode: Text.WordWrap
                text: "Scales everything routed to this output" + (outLevelDlg.unit.stereo ? " (both channels)." : ".")
                      + " Shown on the output only when it is not 0 dB."
                color: Theme.textFaint
            }
        }
        onAccepted: mixer.setOutputLevel(unit.key, levelField.text)
    }

    Dialog {
        id: hueDlg
        property int original: 20
        function openNow() { original = mixer.hueStep; hueSpin.value = original; open() }
        property int contentWidthHint: 400
        title: "Wire Colours"
        modal: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(contentWidthHint, (parent ? parent.width : 800) - 40)
        standardButtons: Dialog.Ok | Dialog.Cancel
        ColumnLayout {
            spacing: 8
            Label { text: "Hue step between connections:" }
            RowLayout {
                SpinBox {
                    id: hueSpin
                    from: 0; to: 180; stepSize: 5; editable: true
                    onValueModified: mixer.hueStep = value       // live preview
                }
                Label { text: "degrees (default 20)" }
            }
            Label {
                Layout.preferredWidth: 350
                wrapMode: Text.WordWrap
                color: Theme.textFaint
                text: "Each new connection's wire gets the previous hue plus this step to make overlapping wires "
                      + "distinguishable. Set to 0 to colour wires by source type (inputs green, playback orange)."
            }
        }
        onRejected: mixer.hueStep = original
    }

    Dialog {
        id: recallDlg
        property int slot: 0
        property string presetName: ""
        property bool hasNames: true
        function openFor(s, name, withNames) {
            slot = s; presetName = name; hasNames = withNames
            cbNames.checked = withNames; cbDest.checked = true; cbLevels.checked = true; cbMutes.checked = true; cbCard.checked = true   // always all checked
            open()
        }
        function tickAll(on) {                  // only the boxes that can be ticked
            for (const cb of [cbNames, cbDest, cbLevels, cbMutes, cbCard])
                if (cb.enabled && cb.visible) cb.checked = on
        }
        property int contentWidthHint: 440
        title: slot < 0 ? "Load Preset File: " + presetName
                        : "Recall Preset " + (slot + 1) + ": " + presetName
        modal: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(contentWidthHint, (parent ? parent.width : 800) - 40)
        footer: DialogButtonBox {
            Button {
                text: "Recall"
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
                enabled: cbNames.checked || cbDest.checked || cbLevels.checked || cbMutes.checked
                         || (cbCard.visible && cbCard.checked)
                highlighted: true
            }
            Button { text: "Cancel"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
        }
        ColumnLayout {
            spacing: 6
            RowLayout {
                spacing: 6
                Label { text: "Restore from this preset:"; font.bold: true; Layout.rightMargin: 4 }
                Repeater {
                    model: [["All", true], ["None", false]]
                    Button {
                        objectName: "recall" + modelData[0]
                        text: modelData[0]
                        font.pixelSize: 11
                        topPadding: 2; bottomPadding: 2; leftPadding: 8; rightPadding: 8
                        implicitWidth: implicitContentWidth + leftPadding + rightPadding
                        implicitHeight: implicitContentHeight + topPadding + bottomPadding
                        onClicked: recallDlg.tickAll(modelData[1])
                    }
                }
            }
            CheckBox {
                id: cbNames
                text: "Names: channel names"
                enabled: recallDlg.hasNames
            }
            Label {
                visible: !recallDlg.hasNames
                leftPadding: 32
                text: "This preset was stored without names."
                color: Theme.textFaint
            }
            CheckBox { id: cbDest; text: "Destinations: which sources feed which outputs, mono/stereo" }
            CheckBox { id: cbLevels; text: "Levels: connection levels and output levels" }
            CheckBox { id: cbMutes; text: "Mutes: muted channels and connections" }
            CheckBox {
                id: cbCard
                visible: mixer.cardSettingLabels.length > 0
                text: "Card IO settings"
                hoverEnabled: true
                ToolTip.visible: hovered
                ToolTip.delay: 600
                ToolTip.text: mixer.cardSettingLabels.join("\n")
            }
        }
        onAccepted: mixer.confirmRecall(slot, cbNames.checked, cbDest.checked, cbLevels.checked, cbMutes.checked,
                                        cbCard.visible && cbCard.checked)
    }

    // File > Import Memory: which of the file's parts replace the app's (bridge.offer_import / confirmImport).
    Dialog {
        id: importDlg
        property string fileName: ""
        property var filePresets: []            // [{slot, text}]: the presets stored in the file
        property int presetChoice: 0            // 0 = all of them, n = filePresets[n - 1] only
        property int presetSlot: 0              // ... into this slot of the app's
        function openFor(name, parts, presets) {
            fileName = name
            filePresets = presets
            presetChoice = 0
            presetSlot = 0
            importParts.clear()
            for (const p of parts)                  // all that the file has, ticked
                importParts.append({ pid: p.id, label: p.label, available: p.available, tip: p.tip,
                                     picked: p.available })
            open()
        }
        function tickAll(on) {
            for (var i = 0; i < importParts.count; i++)
                if (importParts.get(i).available) importParts.setProperty(i, "picked", on)
        }
        function picked() {
            var out = []
            for (var i = 0; i < importParts.count; i++)
                if (importParts.get(i).picked) out.push(importParts.get(i).pid)
            return out
        }
        property int contentWidthHint: 440
        title: "Import Memory: " + fileName
        modal: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(contentWidthHint, (parent ? parent.width : 800) - 40)
        ListModel { id: importParts }
        footer: DialogButtonBox {
            Button {
                objectName: "importApply"
                text: "Import"
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
                enabled: { var n = 0; for (var i = 0; i < importParts.count; i++) if (importParts.get(i).picked) n++; return n > 0 }
                highlighted: true
            }
            Button { text: "Cancel"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
        }
        ColumnLayout {
            spacing: 6
            RowLayout {
                spacing: 6
                Label { text: "Import from this file:"; font.bold: true; Layout.rightMargin: 4 }
                Repeater {
                    model: [["All", true], ["None", false]]
                    Button {
                        objectName: "import" + modelData[0]
                        text: modelData[0]
                        font.pixelSize: 11
                        topPadding: 2; bottomPadding: 2; leftPadding: 8; rightPadding: 8
                        implicitWidth: implicitContentWidth + leftPadding + rightPadding
                        implicitHeight: implicitContentHeight + topPadding + bottomPadding
                        onClicked: importDlg.tickAll(modelData[1])
                    }
                }
            }
            Repeater {
                model: importParts
                ColumnLayout {
                    id: part
                    spacing: 6
                    Layout.fillWidth: true
                    readonly property bool on: available && picked
                    CheckBox {
                        objectName: "importPart_" + pid
                        text: label
                        enabled: available
                        checked: picked
                        onToggled: importParts.setProperty(index, "picked", checked)
                        hoverEnabled: true
                        ToolTip.visible: hovered && tip !== ""
                        ToolTip.delay: 600
                        ToolTip.text: tip
                    }
                    // Presets: all of the file's, or one of them into a slot picked here.
                    Loader {                    // only in the Presets row
                        active: pid === "presets"
                        visible: active
                        Layout.leftMargin: 32
                        sourceComponent: GridLayout {
                            columns: 2
                            columnSpacing: 8; rowSpacing: 6
                            Label { text: "Import"; color: Theme.textDim; enabled: part.on
                                    Layout.preferredWidth: intoLabel.implicitWidth }   // the drop-down stays put
                            ComboBox {
                                id: presetFrom
                                objectName: "importPresetFrom"
                                Layout.preferredWidth: 260
                                enabled: part.on
                                model: ["IMPORT ALL PRESETS"].concat(importDlg.filePresets.map(p => p.text))
                                currentIndex: importDlg.presetChoice
                                onActivated: (i) => {
                                    importDlg.presetChoice = i
                                    if (i > 0) importDlg.presetSlot = importDlg.filePresets[i - 1].slot   // same slot
                                }
                            }
                            Label { id: intoLabel; text: "Into slot"; color: Theme.textDim; opacity: presetTo.opacity }
                            ComboBox {
                                id: presetTo
                                objectName: "importPresetTo"
                                Layout.preferredWidth: 260
                                // Only when one preset is picked; otherwise invisible but keeping its space, so the
                                // dialog doesn't change size.
                                readonly property bool shown: part.on && importDlg.presetChoice > 0
                                opacity: shown ? 1 : 0
                                enabled: shown
                                currentIndex: importDlg.presetSlot
                                onActivated: (i) => importDlg.presetSlot = i
                                model: mixer.presetNames.map((n, i) => (i + 1 < 10 ? "0" : "") + (i + 1) + "   "
                                                                       + (n ? n : "(empty)"))
                            }
                        }
                    }
                }
            }
        }
        onAccepted: {
            var from = presetChoice > 0 ? filePresets[presetChoice - 1].slot : -1
            mixer.confirmImport(picked(), from, from >= 0 ? presetSlot : -1)
        }
    }

    Dialog {
        id: clearDlg
        property int contentWidthHint: 380
        title: "Clear All Routing"
        modal: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(contentWidthHint, (parent ? parent.width : 800) - 40)
        standardButtons: Dialog.Yes | Dialog.No
        Label { text: "Set every connection to -inf? The card goes silent immediately.\nStored presets are kept." }
        onAccepted: mixer.clearRouting()
    }

    Dialog {
        id: aboutDlg
        property int contentWidthHint: 640
        title: "About " + mixer.appName
        modal: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(contentWidthHint, (parent ? parent.width : 800) - 40)
        standardButtons: Dialog.Close
        RowLayout {
            spacing: 18
            Image { source: "../../resources/icon_fader_3d.svg"; sourceSize.width: 96; sourceSize.height: 96 }
            ColumnLayout {
                spacing: 6
                Label { objectName: "aboutTitle"; text: mixer.appName + " " + mixer.appVersion; font.pixelSize: 16; font.bold: true }
                Label { text: " " }             // a blank row
                Label { text: "Routing-focused hardware mixer for RME HDSPe AIO and AIO Pro audio cards." }
                Label { text: "Works with the in-kernel snd-hdspm (AIO) and the snd-hdspe driver (AIO, AIO Pro)." }
                Label { text: " " }             // a blank row
                Label { text: "Developed by AtmanActive 2026." }
                Label {
                    objectName: "aboutLink"
                    readonly property string url: "https://github.com/AtmanActive/AA-HDSPe-AIO-Mixer"
                    textFormat: Text.StyledText
                    linkColor: Theme.accent          // the default blue is unreadable on the dark theme
                    text: "<a href=\"" + url + "\">" + url + "</a>"
                    onLinkActivated: (link) => Qt.openUrlExternally(link)
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }
                Label { text: " " }             // a blank row
                Label { text: "Driver: " + mixer.driverText; color: Theme.textDim }
                Label { text: "Card: " + mixer.cardText; color: Theme.textDim }
            }
        }
    }
}
