import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// OSC remote control section of the side panel (OSC button in the toolbar): bindings from OSC addresses to
// mutes, levels and preset recalls (core/osc.py). Works like the MIDI section (MidiSection.qml): the panel's
// header has the on/off switch, the UDP ports, the feedback host and Add, which opens a new box at the top.
// A box being edited has Apply and Cancel; a saved one Edit and Remove.
//
// The boxes live in a ListModel kept in step with mixer.oscBindings by sync(): a box being edited keeps what
// was typed in it when other bindings change.
ColumnLayout {
    id: sec
    spacing: 10

    property string learning: ""       // the box listening for an OSC message (Learn), by cardId
    property int made: 0
    readonly property var choices: mixer.oscChoices
    readonly property var editRoles: ["what", "target", "address", "value", "invert", "mode", "dbLo", "dbHi"]
    readonly property var viewRoles: ["whatText", "targetText", "missing", "messageText", "behaviourText"]

    function addDraft() {
        made++
        cards.insert(0, { cardId: "new-" + made, isNew: true, editing: true, what: "mute", target: "",
                          address: "", value: "position", invert: false, mode: "toggle",
                          dbLo: choices.dbFloor - 1, dbHi: 0, error: "", whatText: "", targetText: "",
                          missing: false, messageText: "", behaviourText: "" })
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
        var list = mixer.oscBindings
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
        mixer.oscLearn(on)
    }

    ListModel { id: cards }
    Component.onCompleted: sync()
    Connections {
        target: mixer
        function onOscBindingsChanged() { sec.sync() }
        function onOscLearned(m) {
            var i = sec.indexOf(sec.learning)
            sec.learning = ""
            if (i < 0) return
            cards.setProperty(i, "address", m.address)
            // A level whose value isn't within 0 to 1 is most likely in dB.
            if (cards.get(i).what === "level" && m.number !== null && m.number !== undefined
                    && (m.number < 0 || m.number > 1))
                cards.setProperty(i, "value", "db")
            cards.setProperty(i, "error", "")
        }
    }

    component Note: Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        color: Theme.textFaint; font.pixelSize: 11
    }
    component Caps: Label { color: Theme.textFaint; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.6 }
    component Dim: Label { color: Theme.textDim }

    Label {         // only when something needs saying: off, an error, where to send, the feedback host
        objectName: "oscStatus"
        visible: text !== ""
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: mixer.oscStatus
        color: mixer.oscEnabled ? Theme.textDim : Theme.textFaint; font.pixelSize: 11
    }
    Rectangle {     // fixed size: what comes in changes only the one line, never the panel's layout
        visible: mixer.oscEnabled
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
                objectName: "oscActivity"
                width: parent.width
                text: mixer.oscActivity
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
            required property string address
            required property string value
            required property bool invert
            required property string mode
            required property int dbLo
            required property int dbHi
            required property string error
            required property string whatText
            required property string targetText
            required property bool missing
            required property string messageText
            required property string behaviourText

            readonly property var targets: mixer.oscTargets[what] || []
            readonly property bool byNumber: what === "preset" && target === "preset:number"
            readonly property bool inDb: what === "level" && value === "db"
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

            objectName: editing ? "oscEditor" : "oscBinding"
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
                Dim { Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; text: card.messageText }
                Note { visible: card.behaviourText !== ""; text: card.behaviourText }
                RowLayout {
                    Layout.topMargin: 2
                    spacing: 8
                    Button {
                        objectName: "oscEdit"
                        text: "Edit"
                        onClicked: cards.setProperty(card.index, "editing", true)
                    }
                    Button {
                        objectName: "oscRemove"
                        text: "Remove"
                        onClicked: mixer.removeOscBinding(card.cardId)
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
                        objectName: "oscWhat"
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
                        objectName: "oscTarget"
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

                    Dim { text: "Address" }
                    TextField {
                        objectName: "oscAddress"
                        Layout.fillWidth: true
                        placeholderText: "/1/fader1"
                        text: card.address
                        onTextEdited: card.set("address", text)
                    }

                    Item { implicitWidth: 1; implicitHeight: 1 }
                    Button {
                        objectName: "oscLearn"
                        Layout.fillWidth: true
                        text: card.listening ? "Listening… press or move a control" : "Learn"
                        highlighted: card.listening
                        enabled: mixer.oscCanLearn
                        onClicked: sec.learn(card.cardId, !card.listening)
                        ToolTip.visible: hovered; ToolTip.delay: 600
                        ToolTip.text: mixer.oscCanLearn ? "Fills in the address from the next message received."
                                                        : "Turn OSC control on to learn from the controller."
                    }

                    // behaviour: levels, what the value is
                    Dim { visible: card.what === "level"; text: "Value" }
                    ComboBox {
                        objectName: "oscValue"
                        visible: card.what === "level"
                        Layout.fillWidth: true
                        model: sec.choices.values.map(v => v.text)
                        currentIndex: sec.choices.values.findIndex(v => v.value === card.value)
                        onActivated: (i) => card.set("value", sec.choices.values[i].value)
                    }

                    // behaviour: mutes
                    Dim { visible: card.what === "mute"; text: "Action" }
                    ComboBox {
                        objectName: "oscMode"
                        visible: card.what === "mute"
                        Layout.fillWidth: true
                        model: sec.choices.modes.map(m => m.text)
                        currentIndex: sec.choices.modes.findIndex(m => m.value === card.mode)
                        onActivated: (i) => card.set("mode", sec.choices.modes[i].value)
                    }

                    // behaviour: levels, the limits
                    Dim { visible: card.what === "level"; text: "Top" }
                    SpinBox {
                        objectName: "oscDbHi"
                        visible: card.what === "level"
                        Layout.fillWidth: true
                        editable: true
                        from: sec.choices.dbFloor; to: 6
                        value: card.dbHi
                        textFromValue: (v) => card.dbText(v)
                        valueFromText: (t) => card.dbValue(t, value)
                        validator: RegularExpressionValidator { regularExpression: /.*/ }
                        onValueModified: card.set("dbHi", value)
                    }
                    Dim { visible: card.what === "level"; text: "Bottom" }
                    SpinBox {
                        objectName: "oscDbLo"
                        visible: card.what === "level"
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
                    objectName: "oscInvert"
                    visible: card.what !== "preset" && !card.inDb
                    leftPadding: 0
                    text: card.what === "mute" ? "Inverted: high is sounding" : "Inverted: top is the bottom level"
                    checked: card.invert
                    onToggled: card.set("invert", checked)
                }
                Note {
                    visible: card.what === "mute"
                    text: "A press is a value of 0.5 and up (1 from a button), or a message with no value."
                }
                Note {
                    visible: card.what === "level" && !card.inDb && card.dbLo < sec.choices.dbFloor
                    text: "With −∞ at the bottom, the level follows a mixing desk's fader curve. A connection is "
                          + "never removed from here: its lowest is −90 dB."
                }
                Note {
                    visible: card.inDb
                    text: "The value is the level in dB, kept between Bottom and Top. With −∞ at the bottom, "
                          + "−100 and below is −∞."
                }
                Note {
                    visible: card.byNumber
                    text: "The value picks the preset: 1 recalls preset 1, 2 preset 2, …"
                }
                Label {
                    objectName: "oscError"
                    visible: card.error !== ""
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: card.error
                    color: Theme.error; font.pixelSize: 11
                }
                RowLayout {
                    spacing: 8
                    Button {
                        objectName: "oscApply"
                        text: "Apply"
                        highlighted: true
                        onClicked: {
                            var err = mixer.saveOscBinding({
                                id: card.isNew ? "" : card.cardId, what: card.what, target: card.target,
                                address: card.address, value: card.value, invert: card.invert && !card.inDb,
                                mode: card.mode, dbLo: card.dbLo, dbHi: card.dbHi })
                            if (err !== "") { cards.setProperty(card.index, "error", err); return }
                            if (card.listening) sec.learn("", false)
                            if (card.isNew) { cards.remove(card.index); return }
                            cards.setProperty(card.index, "editing", false)
                            sec.sync()
                        }
                    }
                    Button {
                        objectName: "oscCancel"
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
