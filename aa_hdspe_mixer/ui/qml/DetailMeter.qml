import QtQuick
import QtQuick.Controls

// Tall peak meter for the channel section: one bar per channel on a dBFS scale (-60 to 0), with a peak-hold
// line and readout (held 1.5 s) and a clip light that stays lit until clicked.
Item {
    id: m
    property var levels: []          // dBFS per channel, -1000 = silence; empty while meters are off
    property var sides: [""]         // label under each bar ("L"/"R", "1"/"2"; "" for mono)
    readonly property real floorDb: -60
    readonly property real clipDb: -0.1
    readonly property int scaleW: 26
    readonly property int slotW: 36      // per channel: wide enough for a readout like "-12.3"
    readonly property int barW: 18
    readonly property int clipH: 8
    readonly property int barTop: clipH + 4
    readonly property int footH: 32      // readout + side label under the bars
    readonly property real barH: height - barTop - footH
    readonly property var marks: [0, -6, -12, -18, -24, -30, -40, -50, -60]

    implicitWidth: scaleW + sides.length * slotW
    implicitHeight: 280

    function frac(db) { return db <= floorDb ? 0 : db >= 0 ? 1 : (db - floorDb) / -floorDb }
    function zoneColor(db) { return db > -3 ? Theme.meterHigh : db > -12 ? Theme.meterMid : Theme.meterLow }
    function resetPeaks() { for (var i = 0; i < bars.count; ++i) bars.itemAt(i).reset() }

    Repeater {      // scale
        model: m.marks
        delegate: Text {
            x: 0; width: m.scaleW - 5
            y: m.barTop + (1 - m.frac(modelData)) * m.barH - height / 2
            horizontalAlignment: Text.AlignRight
            text: modelData
            color: Theme.textFaint; font.pixelSize: 9; font.family: "monospace"
        }
    }

    Row {
        x: m.scaleW
        Repeater {
            id: bars
            model: m.sides.length
            delegate: Item {
                id: slot
                width: m.slotW; height: m.height
                readonly property real db: m.levels.length > index ? m.levels[index] : -1000
                readonly property real target: m.levels.length === 0 ? 0 : m.frac(db)
                property real shown: 0          // 0..1: jumps up at once, falls back smoothly
                property real hold: -1000       // held peak, dBFS
                property bool clipped: false

                function reset() { hold = db; clipped = false; holdTimer.stop() }
                onTargetChanged: if (target > shown) shown = target
                onDbChanged: {
                    if (db >= hold || !holdTimer.running) { hold = db; holdTimer.restart() }
                    if (db >= m.clipDb) clipped = true
                }
                Timer { id: holdTimer; interval: 1500; onTriggered: slot.hold = slot.db }
                Timer {     // release: also when no new level arrives (levels are sent only when they change)
                    interval: 40; repeat: true
                    running: slot.shown > slot.target
                    onTriggered: slot.shown = slot.shown - slot.target < 0.005 ? slot.target
                                                                             : slot.shown * 0.8 + slot.target * 0.2
                }

                Rectangle {     // clip light
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: m.barW; height: m.clipH; radius: 2
                    color: slot.clipped ? Theme.meterHigh : Theme.meterBack
                }
                Rectangle {     // bar
                    id: bar
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: m.barTop
                    width: m.barW; height: m.barH; radius: 2
                    color: Theme.meterBack
                    Item {      // the lit part, cut from full-height colour zones
                        anchors.bottom: parent.bottom
                        width: parent.width
                        height: parent.height * slot.shown
                        clip: true
                        Rectangle {
                            y: parent.height - bar.height; width: parent.width
                            height: bar.height * (1 - m.frac(-3)); color: Theme.meterHigh
                        }
                        Rectangle {
                            y: parent.height - bar.height * m.frac(-3); width: parent.width
                            height: bar.height * (m.frac(-3) - m.frac(-12)); color: Theme.meterMid
                        }
                        Rectangle {
                            y: parent.height - bar.height * m.frac(-12); width: parent.width
                            height: bar.height * m.frac(-12); color: Theme.meterLow
                        }
                    }
                    Repeater {  // scale ticks across the bar
                        model: m.marks.slice(1, -1)
                        delegate: Rectangle {
                            y: (1 - m.frac(modelData)) * bar.height
                            width: bar.width; height: 1
                            color: Theme.window; opacity: 0.35
                        }
                    }
                    Rectangle { // peak hold
                        visible: slot.hold > m.floorDb
                        y: Math.max(0, (1 - m.frac(slot.hold)) * bar.height - 1)
                        width: bar.width; height: 2
                        color: m.zoneColor(slot.hold)
                    }
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: m.barTop + m.barH + 4
                    text: slot.hold <= -1000 ? "-∞" : slot.hold.toFixed(1)
                    color: slot.clipped ? Theme.meterHigh : Theme.text
                    font.pixelSize: 10; font.family: "monospace"
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: m.barTop + m.barH + 18
                    text: m.sides[index] || ""
                    color: Theme.textFaint; font.pixelSize: 10; font.bold: true
                }
            }
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        onClicked: m.resetPeaks()
    }
    ToolTip.visible: mouse.containsMouse
    ToolTip.delay: 900
    ToolTip.text: "Peak level in dBFS. The number is the peak held for 1.5 s; the light on top stays red after "
                  + "a clip (" + m.clipDb + " dBFS or more). Click to reset both."
}
