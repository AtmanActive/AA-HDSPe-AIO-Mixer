import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// App settings section of the side panel (cog button in the toolbar). App-wide, not per card:
// ~/.config/aa-hdspe-aio-mixer/settings.json (core/app_settings.py).
ColumnLayout {
    spacing: 6

    // Its title is in the panel's header (SettingsPanel.qml).
    Label { text: "Start Window"; color: Theme.textDim; font.bold: true }
    Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: "How should the window appear when the app starts. Minimized starts in the system tray without "
              + "showing the window; Last starts the way the window was left."
        color: Theme.textFaint; font.pixelSize: 11
    }
    ComboBox {
        Layout.fillWidth: true
        model: mixer.startWindowChoices.map(c => c.text)
        currentIndex: mixer.startWindowChoices.findIndex(c => c.value === mixer.startWindow)
        onActivated: (i) => mixer.setStartWindow(mixer.startWindowChoices[i].value)
    }
    Item { implicitHeight: 6 }

    Label { text: "Color Mode"; color: Theme.textDim; font.bold: true }
    Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: "Choose Light or dark colours. System mode follows the desktop's colour scheme, also dynamically."
        color: Theme.textFaint; font.pixelSize: 11
    }
    ComboBox {
        Layout.fillWidth: true
        model: mixer.colorModeChoices.map(c => c.text)
        currentIndex: mixer.colorModeChoices.findIndex(c => c.value === mixer.colorMode)
        onActivated: (i) => mixer.setColorMode(mixer.colorModeChoices[i].value)
    }
    Item { implicitHeight: 6 }

    Label { text: "Zoom"; color: Theme.textDim; font.bold: true }
    Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: "Size of the whole app, from 25 % to 400 %. Takes effect only when the app restarts."
        color: Theme.textFaint; font.pixelSize: 11
    }
    ComboBox {
        Layout.fillWidth: true
        model: mixer.zoomChoices.map(c => c.text)
        currentIndex: mixer.zoomChoices.findIndex(c => c.value === mixer.zoom)
        onActivated: (i) => mixer.setZoom(mixer.zoomChoices[i].value)
    }
    RowLayout {
        visible: mixer.zoomPending
        Layout.fillWidth: true
        spacing: 8
        Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: "Restart to apply the new zoom level."
            color: Theme.warning; font.pixelSize: 11
        }
        Button { text: "Restart Now"; onClicked: mixer.restartApp() }
    }
    Item { implicitHeight: 10 }

    // Display settings that are kept per card, in its memory file, like the View menu's
    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.border }
    Item { implicitHeight: 4 }
    Label { text: "FOR THIS CARD"; color: Theme.textFaint; font.pixelSize: 10; font.bold: true }
    Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: "Saved with this card's memory."
        color: Theme.textFaint; font.pixelSize: 11
    }
    Item { implicitHeight: 6 }

    Label { text: "Wire Colours"; color: Theme.textDim; font.bold: true }
    Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: "Hue step between connection lines: each new wire gets the previous hue plus this step to "
              + "make the overlapping wires distinguishable. Set to 0 to colour the wires by source type (inputs green, "
              + "playback orange)."
        color: Theme.textFaint; font.pixelSize: 11
    }
    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        SpinBox {
            id: hueSpin
            objectName: "appHueStep"
            from: 0; to: 180; stepSize: 5; editable: true
            value: mixer.hueStep
            onValueModified: mixer.hueStep = value
            textFromValue: (v) => v + "°"
            valueFromText: (t) => parseInt(t) || 0
            validator: RegularExpressionValidator { regularExpression: /\s*\d{0,3}\s*°?\s*/ }
            // While typing, a press anywhere else in the window applies the value (as Enter does) and goes on
            // through to whatever is underneath.
            MouseArea {
                parent: hueSpin.Window.contentItem
                anchors.fill: parent
                z: 100000
                visible: hueSpin.activeFocus
                onPressed: (m) => {
                    if (!hueSpin.contains(mapToItem(hueSpin, m.x, m.y))) hueSpin.focus = false
                    m.accepted = false
                }
            }
        }
        Button {
            objectName: "appHueDefault"
            text: "Default"
            enabled: mixer.hueStep !== 20
            onClicked: mixer.hueStep = 20
        }
    }
    Item { implicitHeight: 6 }

    Label { text: "Meter Refresh Rate"; color: Theme.textDim; font.bold: true }
    Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: "How often the meters update."
        color: Theme.textFaint; font.pixelSize: 11
    }
    ComboBox {
        objectName: "appMeterRate"
        Layout.fillWidth: true
        model: mixer.meterRates.map(hz => hz + " Hz")
        currentIndex: mixer.meterRates.indexOf(mixer.meterRate)
        onActivated: (i) => mixer.meterRate = mixer.meterRates[i]
    }
}
