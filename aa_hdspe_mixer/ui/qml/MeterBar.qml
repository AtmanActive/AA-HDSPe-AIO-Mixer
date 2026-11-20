import QtQuick

// Horizontal peak bar. `level` is 0..1 on a -60..0 dB scale (mapping done in Python).
Item {
    id: root
    property real level: 0
    property real shown: 0
    height: 4

    // Fast attack, smooth release, computed only while levels arrive (i.e. while visible).
    onLevelChanged: shown = level > shown ? level : shown * 0.82 + level * 0.18

    Rectangle { anchors.fill: parent; radius: 2; color: Theme.meterBack }
    Rectangle {
        height: parent.height
        width: parent.width * root.shown
        radius: 2
        color: root.shown > 0.95 ? Theme.meterHigh : root.shown > 0.8 ? Theme.meterMid : Theme.meterLow
    }
}
