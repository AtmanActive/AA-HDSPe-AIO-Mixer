import QtQuick
import QtQuick.Controls

// One channel (mono) or channel pair (stereo): kind badge, hardware name, user name, meter.
Rectangle {
    id: pill
    property var unit: ({})
    property bool selectable: false     // picker (Add overlay): click selects, no tooltip, no double-click rename
    property bool clickable: false      // routing view: click shows the channel in the side panel
    property bool selected: false
    property bool showMeter: true
    readonly property color kindColor: Theme.kindColor(unit.kind)
    readonly property bool hovered: mouse.containsMouse
    readonly property bool muted: !!unit.muted
    signal clicked()
    signal contextRequested(real x, real y)

    width: Theme.pillW
    height: Theme.pillH
    radius: height / 2
    color: selected ? Qt.rgba(kindColor.r, kindColor.g, kindColor.b, 0.28)
                    : mouse.containsMouse && (selectable || clickable) ? Theme.paneAlt : Theme.pane
    border.color: selected ? kindColor : muted ? Theme.subdued(Theme.mute) : Theme.subdued(kindColor)
    border.width: selected ? 2 : 1.5

    Rectangle {          // kind badge
        id: badge
        x: 8; anchors.verticalCenter: parent.verticalCenter
        width: 30; height: 30; radius: 15
        color: Qt.rgba(pill.kindColor.r, pill.kindColor.g, pill.kindColor.b, 0.18)
        border.color: pill.kindColor
        Text {
            anchors.centerIn: parent
            text: pill.unit.kind === "in" ? "IN" : pill.unit.kind === "pb" ? "PB" : "OUT"
            color: pill.kindColor; font.pixelSize: 9; font.bold: true
        }
    }

    Column {
        opacity: pill.muted ? 0.45 : 1
        anchors.left: badge.right; anchors.leftMargin: 8
        anchors.right: parent.right; anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Row {
            spacing: 6
            Text { text: pill.unit.hwName || ""; color: Theme.textDim; font.pixelSize: 11; font.bold: true }
            Text {
                text: pill.unit.stereo ? "ST" : "M"
                color: Theme.textFaint; font.pixelSize: 9
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        Text {
            width: parent.width
            text: pill.unit.userName ? pill.unit.userName : "—"
            color: pill.unit.userName ? Theme.text : Theme.textFaint
            font.pixelSize: 13; font.bold: !!pill.unit.userName
            elide: Text.ElideRight
        }
        MeterBar {
            visible: pill.showMeter
            width: parent.width
            level: pill.showMeter ? (Meters.levels[pill.unit.key] || 0) : 0
        }
    }

    // Edge tags. Sources: card settings (e.g. S/PDIF) centred on the top edge, MUTED at the left end of
    // the bottom edge. Outputs: the other way round, MUTED (left) and output level (right) on the top edge,
    // card settings centred on the bottom edge.
    readonly property bool isOut: unit.kind === "out"
    Rectangle {     // output level tag: only when not 0 dB; not clickable (right-click menu changes it)
        id: levelTag
        visible: !!pill.unit.outLevelSet
        anchors.right: parent.right; anchors.rightMargin: 14
        anchors.verticalCenter: parent.top
        width: lvl.implicitWidth + 12; height: 16; radius: 8
        color: Theme.bar
        border.color: Theme.warning
        z: 1
        Text {
            id: lvl
            anchors.centerIn: parent
            text: (pill.unit.outLevelText === "-inf" ? "-∞" : pill.unit.outLevelText) + " dB"
            color: Theme.warning; font.pixelSize: 10; font.bold: true; font.family: "monospace"
        }
    }

    Rectangle {     // mute tag
        id: muteTag
        visible: pill.muted
        x: 14                                       // left end, mirroring the output level tag on the right
        anchors.verticalCenter: pill.isOut ? parent.top : parent.bottom
        width: muteText.implicitWidth + 12; height: 16; radius: 8
        color: Theme.bar
        border.color: Theme.mute
        z: 1
        Text {
            id: muteText
            anchors.centerIn: parent
            text: "MUTED"
            color: Theme.mute; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.6
        }
    }

    Row {           // card settings (e.g. S/PDIF input/output): always shown, centred
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: pill.isOut ? parent.bottom : parent.top
        spacing: 4
        z: 1
        Repeater {
            model: pill.unit.settingTags || []
            delegate: Rectangle {
                width: setText.implicitWidth + 12; height: 16; radius: 8
                color: Theme.bar
                border.color: Theme.textDim
                Text {
                    id: setText
                    anchors.centerIn: parent
                    text: modelData
                    color: Theme.textDim; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.6
                }
            }
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
        cursorShape: pill.selectable || pill.clickable ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: (m) => {
            if (m.button === Qt.RightButton) pill.contextRequested(m.x, m.y)
            else if (m.button === Qt.MiddleButton) mixer.toggleMute(pill.unit.key)
            else pill.clicked()
        }
        onDoubleClicked: (m) => { if (!pill.selectable) dialogs.rename.openFor(pill.unit) }
    }
    ToolTip.visible: mouse.containsMouse && !pill.selectable
    ToolTip.delay: 900
    ToolTip.text: (pill.unit.kindLabel || "") + " " + (pill.unit.hwName || "") + " — click for details, double-click to rename, middle-click to mute, right-click for options"
}
