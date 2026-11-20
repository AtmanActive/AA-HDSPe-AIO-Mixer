import QtQuick

// Vertical caption pillar at the left of a pill row ("SOURCES", "OUTPUTS"); takes no vertical space.
Rectangle {
    id: root
    property alias text: label.text
    width: 20
    radius: 6
    color: Theme.paneAlt
    border.color: Theme.border
    z: 1
    Text {
        id: label
        anchors.centerIn: parent
        rotation: -90
        color: Theme.textFaint
        font.pixelSize: 10; font.bold: true; font.letterSpacing: 1.2
    }
}
