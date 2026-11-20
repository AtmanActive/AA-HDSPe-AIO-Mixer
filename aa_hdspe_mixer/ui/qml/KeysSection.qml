import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Keyboard shortcuts section of the side panel (keyboard button in the toolbar): bindings from KDE Plasma's
// global shortcuts to mutes, levels and preset recalls (core/shortcuts.py). Works like the MIDI section
// (MidiSection.qml), without feedback: the panel's header has the on/off switch and Add, which opens a new
// box at the top. A box being edited has Apply and Cancel; a saved one Edit and Remove.
//
// Learn records a key combination in the window: the next keys pressed (with Ctrl, Alt, Shift or Meta held,
// or one key on its own) are taken; Esc cancels. Plasma's own shortcuts don't act meanwhile.
//
// The boxes live in a ListModel kept in step with mixer.keysBindings by sync(): a box being edited keeps what
// was typed in it when other bindings change.
ColumnLayout {
    id: sec
    spacing: 10

    property string learning: ""       // the box recording keys (Learn), by cardId
    property int made: 0
    readonly property var choices: mixer.keysChoices
    readonly property var editRoles: ["what", "target", "key", "keyText", "invert", "mode", "action", "step",
                                      "level", "dbLo", "dbHi"]
    readonly property var viewRoles: ["whatText", "targetText", "missing", "messageText", "behaviourText"]

    function addDraft() {
        made++
        cards.insert(0, { cardId: "new-" + made, isNew: true, editing: true, what: "mute", target: "",
                          key: "", keyText: "", invert: false, mode: "toggle", action: "up",
                          step: choices.defaultStep, level: 0, dbLo: choices.dbFloor - 1, dbHi: 0, error: "",
                          whatText: "", targetText: "", missing: false, messageText: "", behaviourText: "" })
    }
    function fromBinding(b) {
        var c = { cardId: b.id, isNew: false, editing: false, error: "" }
        for (const r of editRoles.concat(viewRoles)) c[r] = b[r]
        return c
    }
    function indexOf(cardId) {
        for (var i = 0; i < cards.count; i++) if (cards.get(i).cardId === cardId) return i
        return -1
    }
    function sync() {
        var list = mixer.keysBindings
        var n = 0                                   // new boxes stay on top
        while (n < cards.count && cards.get(n).isNew) n++
        var ids = list.map(b => b.id)
        for (var i = cards.count - 1; i >= n; i--)
            if (ids.indexOf(cards.get(i).cardId) < 0) cards.remove(i)
        for (var k = 0; k < list.length; k++) {
            var b = list[k], pos = n + k, j = -1
            for (var m = pos; m < cards.count; m++) if (cards.get(m).cardId === b.id) { j = m; break }
            if (j < 0) { cards.insert(pos, fromBinding(b)); continue }
            if (j !== pos) cards.move(j, pos, 1)
            for (const r of viewRoles) cards.setProperty(pos, r, b[r])
            if (!cards.get(pos).editing) for (const r2 of editRoles) cards.setProperty(pos, r2, b[r2])
        }
    }
    function learn(cardId, on) {
        learning = on ? cardId : ""
        mixer.keysRecord(on)
        if (on) catcher.forceActiveFocus()
        learnTimeout.running = on
    }
    function took(combo) {          // keys recorded for the box that is learning
        var i = indexOf(learning)
        learn("", false)
        if (i < 0) return
        cards.setProperty(i, "key", combo.key)
        cards.setProperty(i, "keyText", combo.text)
        cards.setProperty(i, "error", "")
    }

    ListModel { id: cards }
    Component.onCompleted: sync()
    onVisibleChanged: if (!visible && learning !== "") learn("", false)
    Connections {
        target: mixer
        function onKeysBindingsChanged() { sec.sync() }
    }
    Timer { id: learnTimeout; interval: 15000; onTriggered: sec.learn("", false) }   // never blocks Plasma for long

    // Takes the keys while a box is learning: before the window's own shortcuts (menus) and the controls.
    Item {
        id: catcher
        objectName: "keysCatcher"
        implicitWidth: 0; implicitHeight: 0
        Layout.preferredHeight: 0
        Keys.onShortcutOverride: (event) => event.accepted = sec.learning !== ""
        Keys.onPressed: (event) => {
            event.accepted = true
            if (sec.learning === "") return
            if (event.key === Qt.Key_Escape && event.modifiers === Qt.NoModifier) { sec.learn("", false); return }
            var combo = mixer.keyCombination(event.key, event.modifiers)
            if (combo.key !== "") sec.took(combo)
            else if (combo.hint !== "") {           // keeps listening
                var i = sec.indexOf(sec.learning)
                if (i >= 0) cards.setProperty(i, "error", combo.hint)
                learnTimeout.restart()
            }
        }
        onActiveFocusChanged: if (!activeFocus && sec.learning !== "") sec.learn("", false)
    }

    component Note: Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        color: Theme.textFaint; font.pixelSize: 11
    }
    component Caps: Label { color: Theme.textFaint; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.6 }
    component Dim: Label { color: Theme.textDim }
    Label {         // only when something needs saying: off, Plasma not there, an error
        objectName: "keysStatus"
        visible: text !== ""
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: mixer.keysStatus
        color: mixer.keysEnabled && mixer.keysAvailable ? Theme.warning : Theme.textFaint; font.pixelSize: 11
    }
    Rectangle {     // fixed size: what comes in changes only the one line, never the panel's layout
        visible: mixer.keysEnabled
        Layout.fillWidth: true
        implicitHeight: lastCol.implicitHeight + 12
        radius: 4
        color: Theme.paneAlt
        Column {
            id: lastCol
            x: 8; y: 6
            width: parent.width - 16
            spacing: 2
            Label { text: "Last received:"; color: Theme.textFaint; font.pixelSize: 11 }
            Label {
                objectName: "keysActivity"
                width: parent.width
                text: mixer.keysActivity
                elide: Text.ElideRight
                maximumLineCount: 1
                color: Theme.textDim; font.pixelSize: 11
            }
        }
    }

    Repeater {
        model: cards
        delegate: Rectangle {
            id: card
            required property int index
            required property string cardId
            required property bool isNew
            required property bool editing
            required property string what
            required property string target
            required property string key
            required property string keyText
            required property bool invert
            required property string mode
            required property string action
            required property int step
            required property int level
            required property int dbLo
            required property int dbHi
            required property string error
            required property string whatText
            required property string targetText
            required property bool missing
            required property string messageText
            required property string behaviourText

            readonly property var targets: mixer.keysTargets[what] || []
            readonly property bool stepping: what === "level" && action !== "set"
            readonly property bool listening: sec.learning === cardId
            function set(role, v) {
                cards.setProperty(index, role, v)
                cards.setProperty(index, "error", "")
            }
            function dbText(v) {
                return v < sec.choices.dbFloor ? "−∞ dB" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v) + " dB"
            }
            function dbValue(t, fallback) {
                t = t.replace("−", "-").toLowerCase()
                if (t.indexOf("∞") >= 0 || t.indexOf("inf") >= 0) return sec.choices.dbFloor - 1
                var v = parseInt(t)
                return isNaN(v) ? fallback : v
            }

            objectName: editing ? "keysEditor" : "keysBinding"
            Layout.fillWidth: true
            implicitHeight: (editing ? editor.implicitHeight : view.implicitHeight) + 20
            radius: 4
            color: Theme.paneAlt
            border.color: Theme.border
            Rectangle {     // what it controls, by colour: mutes red, levels as outputs, presets accent
                width: 4; height: parent.height - 2; x: 1; y: 1; radius: 2
                color: card.what === "mute" ? Theme.mute : card.what === "level" ? Theme.output : Theme.accent
            }

            // ------------------------------------------------------------ saved: what it does, Edit, Remove
            ColumnLayout {
                id: view
                visible: !card.editing
                x: 14; y: 10
                width: parent.width - 24
                spacing: 4
                Caps { text: card.whatText }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: card.targetText
                    color: card.missing ? Theme.textFaint : Theme.text; font.bold: true; font.pixelSize: 14
                }
                Label {
                    visible: card.missing
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: "Not there now, so this does nothing until it is."
                    color: Theme.warning; font.pixelSize: 11
                }
                Dim {
                    Layout.fillWidth: true; wrapMode: Text.WordWrap; text: card.messageText
                    color: card.key === "" ? Theme.textFaint : Theme.textDim
                }
                Note { visible: card.behaviourText !== ""; text: card.behaviourText }
                RowLayout {
                    Layout.topMargin: 2
                    spacing: 8
                    Button {
                        objectName: "keysEdit"
                        text: "Edit"
                        onClicked: cards.setProperty(card.index, "editing", true)
                    }
                    Button {
                        objectName: "keysRemove"
                        text: "Remove"
                        onClicked: mixer.removeKeyBinding(card.cardId)
                    }
                }
            }

            // ------------------------------------------------------------ being edited: Apply, Cancel
            ColumnLayout {
                id: editor
                visible: card.editing
                x: 14; y: 10
                width: parent.width - 24
                spacing: 6
                Caps { text: card.isNew ? "NEW BINDING" : "EDIT BINDING" }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 8; rowSpacing: 6

                    Dim { text: "Control" }
                    ComboBox {
                        objectName: "keysWhat"
                        Layout.fillWidth: true
                        model: sec.choices.whats.map(w => w.text)
                        currentIndex: sec.choices.whats.findIndex(w => w.value === card.what)
                        onActivated: (i) => {
                            var v = sec.choices.whats[i].value
                            if (v === card.what) return
                            card.set("what", v)
                            card.set("target", "")
                        }
                    }

                    Dim { text: card.what === "preset" ? "Preset" : "Target" }
                    ComboBox {
                        // In groups, each under a header that can't be chosen; opens wider than the box.
                        id: targetBox
                        objectName: "keysTarget"
                        Layout.fillWidth: true
                        model: card.targets
                        textRole: "text"
                        currentIndex: card.targets.findIndex(t => !t.header && t.value === card.target)
                        displayText: currentIndex < 0 ? "Choose…" : currentText
                        onActivated: (i) => {
                            var t = card.targets[i]
                            if (t.header) t = card.targets[i + 1]   // (only by the keyboard): its group's first
                            currentIndex = Qt.binding(() => card.targets.findIndex(
                                t => !t.header && t.value === card.target))
                            card.set("target", t.value)
                        }
                        FontMetrics { id: targetFm; font: targetBox.font }
                        FontMetrics { id: headerFm; font.pixelSize: 11; font.bold: true }
                        readonly property real widest: card.targets.reduce((w, t) => Math.max(w,
                            (t.header ? headerFm : targetFm).advanceWidth(t.text)), 0)
                        popup.width: Math.max(width, Math.ceil(widest) + 40)
                        popup.x: width - popup.width         // grows to the left, into the window
                        popup.leftMargin: 6
                        popup.rightMargin: 6
                        delegate: ItemDelegate {
                            required property var modelData
                            required property int index
                            readonly property bool header: !!modelData.header
                            width: ListView.view.width
                            enabled: !header
                            highlighted: targetBox.highlightedIndex === index && !header
                            topPadding: header ? (index > 0 ? 12 : 6) : 6
                            bottomPadding: header ? 4 : 6
                            leftPadding: header ? 10 : 22
                            contentItem: Text {
                                text: modelData.text
                                font.pixelSize: header ? 11 : targetBox.font.pixelSize
                                font.bold: header
                                color: header ? Theme.textDim : (highlighted ? palette.highlightedText : palette.text)
                                elide: Text.ElideRight
                                verticalAlignment: Text.AlignVCenter
                            }
                            Rectangle {     // a rule above each group but the first
                                visible: header && index > 0
                                x: 8; y: 5; width: parent.width - 16; height: 1
                                color: Theme.border
                            }
                        }
                    }

                    Dim { text: "Keys" }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Rectangle {         // the key combination, as the desktop writes it
                            Layout.fillWidth: true
                            implicitHeight: clearBtn.implicitHeight
                            radius: 3
                            color: Theme.pane
                            border.color: card.listening ? Theme.accent : Theme.border
                            Label {
                                objectName: "keysKeys"
                                anchors.fill: parent
                                anchors.leftMargin: 8; anchors.rightMargin: 8
                                verticalAlignment: Text.AlignVCenter
                                elide: Text.ElideRight
                                text: card.listening ? "Listening…" : card.keyText !== "" ? card.keyText : "None"
                                color: card.keyText !== "" && !card.listening ? Theme.text : Theme.textFaint
                                font.bold: card.keyText !== "" && !card.listening
                            }
                        }
                        Button {
                            id: clearBtn
                            objectName: "keysClear"
                            text: "Clear"
                            enabled: card.keyText !== "" && !card.listening
                            onClicked: { card.set("key", ""); card.set("keyText", "") }
                        }
                    }

                    Item { implicitWidth: 1; implicitHeight: 1 }
                    Button {
                        objectName: "keysLearn"
                        Layout.fillWidth: true
                        text: card.listening ? "Press keys · Esc cancels" : "Learn"
                        highlighted: card.listening
                        focusPolicy: Qt.NoFocus             // the keys go to the catcher while listening
                        onClicked: sec.learn(card.cardId, !card.listening)
                    }

                    // behaviour: mutes
                    Dim { visible: card.what === "mute"; text: "Action" }
                    ComboBox {
                        objectName: "keysMode"
                        visible: card.what === "mute"
                        Layout.fillWidth: true
                        model: sec.choices.modes.map(m => m.text)
                        currentIndex: sec.choices.modes.findIndex(m => m.value === card.mode)
                        onActivated: (i) => card.set("mode", sec.choices.modes[i].value)
                    }

                    // behaviour: levels
                    Dim { visible: card.what === "level"; text: "Action" }
                    ComboBox {
                        objectName: "keysAction"
                        visible: card.what === "level"
                        Layout.fillWidth: true
                        model: sec.choices.actions.map(a => a.text)
                        currentIndex: sec.choices.actions.findIndex(a => a.value === card.action)
                        onActivated: (i) => card.set("action", sec.choices.actions[i].value)
                    }
                    Dim { visible: card.stepping; text: "Step" }
                    SpinBox {
                        objectName: "keysStep"
                        visible: card.stepping
                        Layout.fillWidth: true
                        editable: true
                        from: sec.choices.stepMin; to: sec.choices.stepMax
                        value: card.step
                        textFromValue: (v) => v + " dB"
                        valueFromText: (t) => { var v = parseInt(t); return isNaN(v) ? value : v }
                        validator: RegularExpressionValidator { regularExpression: /.*/ }
                        onValueModified: card.set("step", value)
                    }
                    Dim { visible: card.what === "level" && !card.stepping; text: "Level" }
                    SpinBox {
                        objectName: "keysLevel"
                        visible: card.what === "level" && !card.stepping
                        Layout.fillWidth: true
                        editable: true
                        from: sec.choices.dbFloor - 1; to: 6
                        value: card.level
                        textFromValue: (v) => card.dbText(v)
                        valueFromText: (t) => card.dbValue(t, value)
                        validator: RegularExpressionValidator { regularExpression: /.*/ }
                        onValueModified: card.set("level", value)
                    }
                    Dim { visible: card.stepping; text: "Top" }
                    SpinBox {
                        objectName: "keysDbHi"
                        visible: card.stepping
                        Layout.fillWidth: true
                        editable: true
                        from: sec.choices.dbFloor; to: 6
                        value: card.dbHi
                        textFromValue: (v) => card.dbText(v)
                        valueFromText: (t) => card.dbValue(t, value)
                        validator: RegularExpressionValidator { regularExpression: /.*/ }
                        onValueModified: card.set("dbHi", value)
                    }
                    Dim { visible: card.stepping; text: "Bottom" }
                    SpinBox {
                        objectName: "keysDbLo"
                        visible: card.stepping
                        Layout.fillWidth: true
                        editable: true
                        from: sec.choices.dbFloor - 1; to: 5
                        value: card.dbLo
                        textFromValue: (v) => card.dbText(v)
                        valueFromText: (t) => card.dbValue(t, value)
                        validator: RegularExpressionValidator { regularExpression: /.*/ }
                        onValueModified: card.set("dbLo", value)
                    }
                }
                CheckBox {
                    objectName: "keysInvert"
                    visible: card.what === "mute" && card.mode === "follow"
                    leftPadding: 0
                    text: "Inverted: sounding while held"
                    checked: card.invert
                    onToggled: card.set("invert", checked)
                }
                Note {
                    visible: card.keyText === "" && !card.listening
                    text: "No keys yet: Learn takes them, or give it some later in System Settings."
                }
                Note {
                    visible: card.what === "mute" && card.mode === "follow"
                    text: "Unmuted again when the keys are let go."
                }
                Note {
                    visible: card.stepping && card.dbLo < sec.choices.dbFloor
                    text: "With −∞ at the bottom, the steps go between −60 dB and the top: one more step down is "
                          + "−∞, and one up from there is −60 dB. A connection is never removed from here: its "
                          + "lowest is −90 dB."
                }
                Note {
                    visible: card.what === "level" && !card.stepping && card.level < sec.choices.dbFloor
                    text: "A connection is never removed from here: −∞ sets its lowest, −90 dB."
                }
                Label {
                    objectName: "keysError"
                    visible: card.error !== ""
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: card.error
                    color: Theme.error; font.pixelSize: 11
                }
                RowLayout {
                    spacing: 8
                    Button {
                        objectName: "keysApply"
                        text: "Apply"
                        highlighted: true
                        onClicked: {
                            if (card.listening) sec.learn("", false)
                            var err = mixer.saveKeyBinding({
                                id: card.isNew ? "" : card.cardId, what: card.what, target: card.target,
                                key: card.key, invert: card.invert, mode: card.mode, action: card.action,
                                step: card.step, level: card.level, dbLo: card.dbLo, dbHi: card.dbHi })
                            if (err !== "") { cards.setProperty(card.index, "error", err); return }
                            if (card.isNew) { cards.remove(card.index); return }
                            cards.setProperty(card.index, "editing", false)
                            sec.sync()
                        }
                    }
                    Button {
                        objectName: "keysCancel"
                        text: "Cancel"
                        onClicked: {
                            if (card.listening) sec.learn("", false)
                            if (card.isNew) { cards.remove(card.index); return }
                            cards.setProperty(card.index, "editing", false)
                            cards.setProperty(card.index, "error", "")
                            sec.sync()                      // back to what is saved
                        }
                    }
                }
            }
        }
    }

    Note {
        visible: cards.count === 0
        text: "No bindings yet. Click Add to create one."
    }
}
