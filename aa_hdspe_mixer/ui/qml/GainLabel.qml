import QtQuick
import QtQuick.Controls

// Clickable level number on a routing wire. Click to edit; Enter or a click anywhere outside applies, Esc cancels.
// "-inf" (or "off") removes the connection, which then disappears from the view.
Rectangle {
    id: root
    property var conn: ({})
    property color wireColor: Theme.kindColor(conn.srcKind)
    property bool editing: false
    property bool hovered: mouse.containsMouse || (editing && field.activeFocus)
    property bool lit: false        // its connection is highlighted (label or pill hovered)
    property bool dimmed: false     // another connection is highlighted
    readonly property bool muted: !!conn.muted          // the connection's own mute
    readonly property bool silenced: !!conn.silenced    // its source or output is muted
    opacity: dimmed ? 0.45 : 1
    signal hoverChanged2(bool on)

    width: editing ? 74 : Math.max(46, label.implicitWidth + 16)
    height: 22
    radius: 11
    color: hovered ? Theme.paneAlt : Theme.bar
    border.color: hovered ? Theme.text : muted ? Theme.mute : wireColor
    border.width: lit ? 2 : 1
    onHoveredChanged: hoverChanged2(hovered)
    // A hidden field keeps keyboard focus unless told otherwise (a click on plain items does not take it),
    // which kept this connection highlighted after editing ended. Drop focus whenever editing ends.
    onEditingChanged: if (!editing) field.focus = false

    // Apply the typed value and close. Invalid text: setGain shows a warning and nothing changes.
    function commit() {
        if (!editing) return
        editing = false
        mixer.setGain(conn.srcKey, conn.dstKey, field.text)
    }

    Text {
        id: label
        anchors.centerIn: parent
        visible: !root.editing
        text: root.conn.gainText === "-inf" ? "-∞" : root.conn.gainText
        color: root.muted ? Theme.mute : root.silenced ? Theme.textFaint : Theme.text
        font.pixelSize: 12; font.family: "monospace"
        font.strikeout: root.muted
    }
    TextField {
        id: field
        visible: root.editing
        anchors.fill: parent
        anchors.margins: 1
        padding: 0
        horizontalAlignment: TextInput.AlignHCenter
        font.pixelSize: 12; font.family: "monospace"
        color: Theme.text
        background: Rectangle { radius: 10; color: Theme.window }
        selectByMouse: true
        onAccepted: {
            if (mixer.setGain(root.conn.srcKey, root.conn.dstKey, text)) root.editing = false
        }
        Keys.onEscapePressed: root.editing = false
        onActiveFocusChanged: if (!activeFocus) root.commit()
    }
    // While editing, a press anywhere else in the window applies the value. Clicks on plain items do not
    // take focus away from the field, so a window-wide catcher sees the press, commits, and lets the
    // press through to whatever is underneath (another level, a pill, a button).
    MouseArea {
        parent: root.Window.contentItem
        anchors.fill: parent
        z: 100000
        visible: root.editing
        onPressed: (m) => {
            var p = mapToItem(field, m.x, m.y)
            if (!field.contains(p)) root.commit()
            m.accepted = false
        }
    }
    MouseArea {
        id: mouse
        anchors.fill: parent
        visible: !root.editing
        hoverEnabled: true
        cursorShape: Qt.IBeamCursor
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
        onClicked: (m) => {
            if (m.button === Qt.RightButton) { gainMenu.popup(); return }
            if (m.button === Qt.MiddleButton) { mixer.toggleConnectionMute(root.conn.srcKey, root.conn.dstKey); return }
            field.text = root.conn.gainText
            root.editing = true
            field.forceActiveFocus()
            field.selectAll()
        }
        // Fine adjust, 0.5 dB per notch. Stops at the quietest level (-90.3 dB) and never removes the
        // connection: removing stays an explicit action (right-click > Remove Connection).
        onWheel: (w) => mixer.nudgeGain(root.conn.srcKey, root.conn.dstKey, w.angleDelta.y > 0 ? 0.5 : -0.5)
    }
    Menu {
        id: gainMenu
        MenuItem {
            text: "Mute Connection"; checkable: true; checked: root.muted
            onTriggered: mixer.toggleConnectionMute(root.conn.srcKey, root.conn.dstKey)
        }
        MenuSeparator {}
        MenuItem { text: "0 dB"; onTriggered: mixer.setGain(root.conn.srcKey, root.conn.dstKey, "0") }
        MenuItem { text: "-6 dB"; onTriggered: mixer.setGain(root.conn.srcKey, root.conn.dstKey, "-6") }
        MenuItem { text: "-12 dB"; onTriggered: mixer.setGain(root.conn.srcKey, root.conn.dstKey, "-12") }
        MenuSeparator {}
        MenuItem { text: "Remove Connection"; onTriggered: mixer.removeConnection(root.conn.srcKey, root.conn.dstKey) }
    }
    ToolTip.visible: mouse.containsMouse
    ToolTip.delay: 900
    ToolTip.text: (root.muted ? "Muted (the level is kept). " : root.silenced ? "Silent: its source or output is muted. " : "")
                  + "Level in dB. Click to type a value, scroll for ±0.5 dB (down to -90.3), middle-click to mute, "
                  + "right-click for quick values, Mute or Remove Connection."
}
