import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Virtual PipeWire devices section of the side panel (plug button in the toolbar): stereo inputs and outputs
// on chosen channels of the card, for desktop apps (core/pwvirtual.py). Add Input / Add Output in the panel's
// header open a new box at the top: a name, a channel for left and right, then Apply makes the device. A device
// made has only Remove. Below the new boxes, the devices made: inputs first, then outputs, each by name.
ColumnLayout {
    id: pw
    spacing: 10

    function addDraft(direction) { drafts.insert(0, { direction: direction }) }
    ListModel { id: drafts }

    component Note: Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        color: Theme.textFaint; font.pixelSize: 11
    }
    component Caps: Label { color: Theme.textFaint; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.6 }
    // A device's box: a coloured strip on the left, green for inputs, orange for outputs (they play into the
    // card's playback channels), as on the routing view.
    component Box: Rectangle {
        id: box
        property string direction
        default property alias content: inner.data
        Layout.fillWidth: true
        implicitHeight: inner.implicitHeight + 20
        radius: 4
        color: Theme.paneAlt
        border.color: Theme.border
        Rectangle {
            width: 4; height: parent.height - 2; x: 1; y: 1; radius: 2
            color: box.direction === "input" ? Theme.input : Theme.playback
        }
        ColumnLayout {
            id: inner
            x: 14; y: 10
            width: parent.width - 24
            spacing: 6
        }
    }

    Note {
        text: "Create unlimited number of virtual PipeWire input or output stereo devices on chosen channels "
              + "of the card for desktop use."
    }
    Label {
        visible: text !== ""
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: mixer.virtualNote
        color: Theme.warning; font.pixelSize: 11
    }

    // ---------------------------------------------------------------- new devices, not made yet
    Repeater {
        model: drafts
        delegate: Box {
            id: draft
            required property int index
            required property string direction
            readonly property var target: mixer.virtualTargets[direction] || ({ available: false, channels: [] })
            readonly property var channels: target.channels
            property string error: ""
            objectName: "vdDraft"

            Caps { text: "NEW VIRTUAL " + draft.direction.toUpperCase() }
            TextField {
                id: nameField
                objectName: "vdName"
                Layout.fillWidth: true
                placeholderText: "Name, as apps will show it"
                Component.onCompleted: forceActiveFocus()
                onAccepted: apply.clicked()
                onTextEdited: draft.error = ""
            }
            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 8; rowSpacing: 6
                Label { text: "Left"; color: Theme.textDim }
                ComboBox {
                    id: leftBox
                    objectName: "vdLeft"
                    Layout.fillWidth: true
                    enabled: draft.target.available
                    model: draft.channels.map(c => c.text)
                    currentIndex: Math.min(0, count - 1)
                    onActivated: draft.error = ""
                }
                Label { text: "Right"; color: Theme.textDim }
                ComboBox {
                    id: rightBox
                    objectName: "vdRight"
                    Layout.fillWidth: true
                    enabled: draft.target.available
                    model: draft.channels.map(c => c.text)
                    currentIndex: Math.min(1, count - 1)
                    onActivated: draft.error = ""
                }
            }
            Label {
                objectName: "vdError"
                visible: draft.error !== ""
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: draft.error
                color: Theme.error; font.pixelSize: 11
            }
            RowLayout {
                spacing: 8
                Button {
                    id: apply
                    objectName: "vdApply"
                    text: "Apply"
                    highlighted: true
                    enabled: draft.target.available
                    onClicked: {
                        var ch = draft.channels
                        if (leftBox.currentIndex < 0 || rightBox.currentIndex < 0) return
                        draft.error = mixer.createVirtualDevice(draft.direction, nameField.text,
                                                                ch[leftBox.currentIndex].value,
                                                                ch[rightBox.currentIndex].value)
                        if (draft.error === "") drafts.remove(draft.index)
                    }
                }
                Button {
                    objectName: "vdCancel"
                    text: "Cancel"
                    onClicked: drafts.remove(draft.index)
                }
            }
        }
    }

    // ---------------------------------------------------------------- devices made
    Repeater {
        model: mixer.virtualDevices
        delegate: Box {
            id: dev
            required property var modelData
            direction: modelData.direction
            objectName: "vdDevice"

            Caps { text: dev.modelData.kindText }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: dev.modelData.name
                color: Theme.text; font.bold: true; font.pixelSize: 14
            }
            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 8; rowSpacing: 2
                Label { text: "Left"; color: Theme.textDim }
                Label { Layout.fillWidth: true; elide: Text.ElideRight; text: dev.modelData.left; color: Theme.text }
                Label { text: "Right"; color: Theme.textDim }
                Label { Layout.fillWidth: true; elide: Text.ElideRight; text: dev.modelData.right; color: Theme.text }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                Rectangle {
                    Layout.alignment: Qt.AlignTop; Layout.topMargin: 4
                    width: 8; height: 8; radius: 4
                    color: dev.modelData.state === "ok" ? Theme.meterLow
                         : dev.modelData.state === "starting" ? Theme.textFaint
                         : dev.modelData.state === "warning" ? Theme.warning : Theme.error
                }
                Label {
                    objectName: "vdState"
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: dev.modelData.stateText
                    color: dev.modelData.state === "ok" || dev.modelData.state === "starting" ? Theme.textFaint
                         : dev.modelData.state === "warning" ? Theme.warning : Theme.error
                    font.pixelSize: 11
                }
            }
            Button {
                objectName: "vdRemove"
                text: "Remove"
                onClicked: mixer.removeVirtualDevice(dev.modelData.id)
                ToolTip.visible: hovered; ToolTip.delay: 600
                ToolTip.text: "Stops this device and deletes it. Apps using it move to another device."
            }
        }
    }

    Note {
        visible: drafts.count === 0 && mixer.virtualDevices.length === 0
        text: "No virtual devices yet. Use Add Input or Add Output buttons above to create some."
    }
}
