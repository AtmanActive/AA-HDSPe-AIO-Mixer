import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Clock section of the side panel (clock button in the toolbar): sample rate, clock source with the status
// of each incoming clock, and fine tuning of the internal clock. Reads the card every second while open.
// Nothing here is stored: the card owns its clock (core/clock.py).
ColumnLayout {
    id: sec
    spacing: 6

    function stateColor(state) {
        return state === "sync" ? Theme.meterLow : state === "lock" ? Theme.warning
             : state === "internal" ? Theme.textDim : Theme.textFaint
    }

    // Its title and running state are in the panel's header (SettingsPanel.qml).
    Label {
        visible: !mixer.clockAvailable
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: "The driver doesn't expose the clock controls."
        color: Theme.textFaint
    }

    // ------------------------------------------------------------------ sample rate
    Item { implicitHeight: 4 }
    Label { text: "Sample Rate"; color: Theme.textDim; font.bold: true; visible: mixer.clockAvailable }
    ComboBox {
        id: rateBox
        visible: mixer.clockAvailable
        Layout.fillWidth: true
        model: mixer.clockRates.map(r => r.text)
        currentIndex: mixer.clockRates.findIndex(r => r.hz === mixer.clockRate)
        onActivated: (i) => mixer.setClockRate(mixer.clockRates[i].hz)
    }
    Label {
        visible: mixer.clockAvailable
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: "The internal clock's rate. A program playing or recording sets its own rate."
              + (mixer.driverText.indexOf("snd-hdspm") === 0 ? " snd-hdspm: single speed only." : "")
        color: Theme.textFaint; font.pixelSize: 11
    }

    // ------------------------------------------------------------------ clock source
    Item { implicitHeight: 6 }
    Label { text: "Clock Source"; color: Theme.textDim; font.bold: true; visible: mixer.clockAvailable }
    ButtonGroup { id: sourceGroup }
    // Status is re-read every second: a count as model keeps the rows in place (re-created rows would
    // lose a click in progress); each row reads its entry by index.
    readonly property var sources: mixer.clockSources
    Repeater {
        model: sec.sources.length
        delegate: RowLayout {
            readonly property var modelData: sec.sources[index] || ({})
            Layout.fillWidth: true
            spacing: 6
            RadioButton {
                text: modelData.label
                enabled: modelData.available
                checked: modelData.checked
                ButtonGroup.group: sourceGroup
                onClicked: mixer.setClockSource(modelData.key)
                Layout.fillWidth: true
            }
            Label {
                text: (modelData.status || "") + (modelData.inUse && modelData.key !== "internal" ? "  ●" : "")
                color: sec.stateColor(modelData.state)
                font.pixelSize: 11
                horizontalAlignment: Text.AlignRight
            }
        }
    }
    Label {
        visible: mixer.clockAvailable
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: "Internal: the card is the clock master. Any other source: the card follows that input "
              + "(AutoSync) and falls back to another one if it is lost. ● = in use."
        color: Theme.textFaint; font.pixelSize: 11
    }

    // ------------------------------------------------------------------ fine tuning
    Item { implicitHeight: 6 }
    Label { text: "Sample Rate Tuning"; color: Theme.textDim; font.bold: true; visible: mixer.clockAvailable }
    Slider {
        id: tune
        visible: mixer.clockAvailable
        Layout.fillWidth: true
        enabled: mixer.clockTune.enabled
        from: mixer.clockTune.min; to: mixer.clockTune.max
        stepSize: 1
        value: mixer.clockTune.value
        // Write while dragging, at most ten times a second; the last value is always written.
        onMoved: tuneTimer.restart()
        onPressedChanged: if (!pressed) { tuneTimer.stop(); mixer.setTunedRate(String(value)) }
        Timer { id: tuneTimer; interval: 100; onTriggered: mixer.setTunedRate(String(tune.value)) }
    }
    RowLayout {
        visible: mixer.clockAvailable
        Layout.fillWidth: true
        spacing: 6
        TextField {
            id: tuneField
            enabled: mixer.clockTune.enabled
            Layout.fillWidth: true
            horizontalAlignment: TextInput.AlignHCenter
            font.family: "monospace"
            text: mixer.clockTune.value.toFixed(1)
            selectByMouse: true
            onAccepted: { mixer.setTunedRate(text); focus = false }
            onActiveFocusChanged: if (!activeFocus) text = Qt.binding(() => mixer.clockTune.value.toFixed(1))
        }
        Label { text: "Hz"; color: Theme.textDim }
        Button {
            text: "Reset"
            enabled: mixer.clockTune.enabled && Math.abs(mixer.clockTune.value - mixer.clockTune.nominal) >= 0.5
            onClicked: mixer.resetTune()
        }
    }
    Label {
        visible: mixer.clockAvailable
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: (mixer.clockTune.note ? mixer.clockTune.note + " " : "")
              + "Range " + mixer.clockTune.min + "–" + mixer.clockTune.max + " Hz."
        color: Theme.textFaint; font.pixelSize: 11
    }
}
