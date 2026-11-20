import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// [Add] overlay: pick ONE source from either all inputs or all playback channels (never both at once),
// then one or more outputs and a level.
Popup {
    id: pop
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(parent ? parent.width - 32 : 900, 1100)
    height: Math.min(parent ? parent.height - 32 : 700, 820)
    padding: 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    property string sourceKind: "in"       // "in" | "pb"
    property string srcKey: ""
    property var dstKeys: []

    function openFresh() {
        srcKey = ""; dstKeys = []; gainField.text = "0.0"; open()
    }
    function toggleDst(k) {
        var a = dstKeys.slice(), i = a.indexOf(k)
        if (i >= 0) a.splice(i, 1); else a.push(k)
        dstKeys = a
    }

    background: Rectangle { color: Theme.window; radius: 12; border.color: Theme.border }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Label { text: "Add Connection"; font.pixelSize: 18; font.bold: true; color: Theme.text }
            Item { Layout.fillWidth: true }
            ToolButton { text: "✕"; onClicked: pop.close() }
        }

        RowLayout {
            spacing: 0
            Label { text: "1. From  "; color: Theme.textDim; font.bold: true }
            Repeater {
                model: [{ k: "in", t: "Inputs" }, { k: "pb", t: "Playback" }]
                delegate: Button {
                    text: modelData.t
                    checkable: true
                    checked: pop.sourceKind === modelData.k
                    onClicked: { pop.sourceKind = modelData.k; pop.srcKey = "" }
                    palette.button: checked ? Theme.kindColor(modelData.k) : Theme.pane
                    palette.buttonText: checked ? Theme.accentText : Theme.text
                }
            }
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(srcFlow.implicitHeight + 4, pop.height * 0.32)
            clip: true
            Flow {
                id: srcFlow
                width: pop.width - 40
                spacing: Theme.gap
                // Pill tags (MUTED, card settings, output level) sit half outside the pill's edge.
                topPadding: 9; bottomPadding: 9
                Repeater {
                    model: pop.sourceKind === "in" ? mixer.inputs : mixer.playbacks
                    delegate: ChannelPill {
                        unit: modelData
                        selectable: true
                        showMeter: pop.visible
                        selected: pop.srcKey === modelData.key
                        onClicked: pop.srcKey = modelData.key
                        onContextRequested: (x, y) => unitMenu.openFor(modelData, this, x, y)
                    }
                }
            }
        }

        Label { text: "2. To (one or more outputs)"; color: Theme.textDim; font.bold: true }
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            Flow {
                width: pop.width - 40
                spacing: Theme.gap
                topPadding: 9; bottomPadding: 9
                Repeater {
                    model: mixer.outputs
                    delegate: ChannelPill {
                        unit: modelData
                        selectable: true
                        showMeter: pop.visible
                        selected: pop.dstKeys.indexOf(modelData.key) >= 0
                        onClicked: pop.toggleDst(modelData.key)
                        onContextRequested: (x, y) => unitMenu.openFor(modelData, this, x, y)
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Label { text: "3. Level (dB)"; color: Theme.textDim; font.bold: true }
            TextField {
                id: gainField
                text: "0.0"
                implicitWidth: 80
                horizontalAlignment: TextInput.AlignHCenter
                font.family: "monospace"
                onAccepted: addBtn.clicked()
            }
            Label {
                text: "-inf … +6.0"
                color: Theme.textFaint
            }
            Item { Layout.fillWidth: true }
            Button { text: "Cancel"; onClicked: pop.close() }
            Button {
                id: addBtn
                text: pop.dstKeys.length > 1 ? "Add " + pop.dstKeys.length + " Connections" : "Add"
                enabled: pop.srcKey !== "" && pop.dstKeys.length > 0
                highlighted: true
                onClicked: {
                    if (mixer.addConnections(pop.srcKey, pop.dstKeys, gainField.text) > 0) pop.close()
                }
            }
        }
    }
}
