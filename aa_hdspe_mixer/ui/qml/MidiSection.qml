import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// MIDI remote control section of the side panel (MIDI button in the toolbar): bindings from MIDI messages to
// mutes, levels and preset recalls (core/midi.py). The panel's header has the on/off switch, the ports and
// Add, which opens a new box at the top. A box being edited has Apply and Cancel; a saved one Edit and Remove.
//
// The boxes live in a ListModel kept in step with mixer.midiBindings by sync(), rather than a Repeater on
// the list itself: a box being edited keeps what was typed in it when other bindings change.
ColumnLayout {
    id: sec
    spacing: 10

    property string learning: ""       // the box listening for a MIDI message (Learn), by cardId
    property int made: 0
    readonly property var choices: mixer.midiChoices
    readonly property var editRoles: ["what", "target", "message", "channel", "number", "invert", "mode",
                                      "dbLo", "dbHi"]
    readonly property var viewRoles: ["whatText", "targetText", "missing", "messageText", "behaviourText"]

    function addDraft() {
        made++
        cards.insert(0, { cardId: "new-" + made, isNew: true, editing: true, what: "mute", target: "",
                          message: "note", channel: 0, number: 0, invert: false, mode: "toggle",
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
        var list = mixer.midiBindings
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
        mixer.midiLearn(on)
    }

    ListModel { id: cards }
    Component.onCompleted: sync()
    Connections {
        target: mixer
        function onMidiBindingsChanged() { sec.sync() }
        function onMidiLearned(m) {
            var i = sec.indexOf(sec.learning)
            sec.learning = ""
            if (i < 0) return
            var c = cards.get(i)
            var ok = (sec.choices.messages[c.what] || []).map(x => x.value)
            var heard = { note: "a note", cc: "a controller (CC)", program: "a program change",
                          pitchbend: "a pitch bend" }[m.message]
            if (ok.indexOf(m.message) < 0) {
                cards.setProperty(i, "error", "Heard " + heard + ", which can't control this. "
                                  + { mute: "Mutes take a note, a CC or pitch bend.",
                                      level: "Levels take a CC or pitch bend.", preset: "" }[c.what])
                return
            }
            if (c.what === "preset" && c.target === "preset:number" && !sec.picksByNumber(m.message)) {
                cards.setProperty(i, "error", "Heard " + heard + ", which can't pick a preset by number. "
                                  + "Use a program change or a CC, or choose one preset.")
                return
            }
            cards.setProperty(i, "message", m.message)
            cards.setProperty(i, "channel", m.channel)
            cards.setProperty(i, "number", m.number)
            cards.setProperty(i, "error", "")
        }
    }

    // A message that can pick the preset by its number (its program number or CC value).
    function picksByNumber(message) { return message === "program" || message === "cc" }

    component Note: Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        color: Theme.textFaint; font.pixelSize: 11
    }
    component Caps: Label { color: Theme.textFaint; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.6 }
    component Dim: Label { color: Theme.textDim }

    Label {         // only when something needs saying: off, an error, a port that isn't plugged in
        objectName: "midiStatus"
        visible: text !== ""
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: mixer.midiStatus
        color: mixer.midiEnabled ? Theme.textDim : Theme.textFaint; font.pixelSize: 11
    }
    Rectangle {     // fixed size: what comes in changes only the one line, never the panel's layout
        visible: mixer.midiEnabled
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
                objectName: "midiActivity"
                width: parent.width
                text: mixer.midiActivity
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
            required property string message
            required property int channel
            required property int number
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

            readonly property var targets: mixer.midiTargets[what] || []
            readonly property var messages: sec.choices.messages[what] || []
            readonly property bool byNumber: what === "preset" && target === "preset:number"
            readonly property var messageChoices: messages.filter(m => !byNumber || sec.picksByNumber(m.value))
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

            objectName: editing ? "midiEditor" : "midiBinding"
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
                Dim { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: card.messageText }
                Note { visible: card.behaviourText !== ""; text: card.behaviourText }
                RowLayout {
                    Layout.topMargin: 2
                    spacing: 8
                    Button {
                        objectName: "midiEdit"
                        text: "Edit"
                        onClicked: cards.setProperty(card.index, "editing", true)
                    }
                    Button {
                        objectName: "midiRemove"
                        text: "Remove"
                        onClicked: mixer.removeMidiBinding(card.cardId)
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
                        objectName: "midiWhat"
                        Layout.fillWidth: true
                        model: sec.choices.whats.map(w => w.text)
                        currentIndex: sec.choices.whats.findIndex(w => w.value === card.what)
                        onActivated: (i) => {
                            var v = sec.choices.whats[i].value
                            if (v === card.what) return
                            card.set("what", v)
                            card.set("target", "")
                            var ok = sec.choices.messages[v].map(m => m.value)
                            if (ok.indexOf(card.message) < 0) card.set("message", ok[0])
                        }
                    }

                    Dim { text: card.what === "preset" ? "Preset" : "Target" }
                    ComboBox {
                        // The list is in groups, each under a header that can't be chosen. It opens wider than
                        // the box (up to the window) so long names and connections can be read whole.
                        id: targetBox
                        objectName: "midiTarget"
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
                            if (card.byNumber && !sec.picksByNumber(card.message)) card.set("message", "program")
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

                    Dim { text: "MIDI" }
                    ComboBox {
                        objectName: "midiMessage"
                        Layout.fillWidth: true
                        model: card.messageChoices.map(m => m.text)
                        currentIndex: card.messageChoices.findIndex(m => m.value === card.message)
                        onActivated: (i) => card.set("message", card.messageChoices[i].value)
                    }

                    Item { implicitWidth: 1; implicitHeight: 1 }
                    Button {
                        objectName: "midiLearn"
                        Layout.fillWidth: true
                        text: card.listening ? "Listening… press or move a control" : "Learn"
                        highlighted: card.listening
                        enabled: mixer.midiCanLearn
                        onClicked: sec.learn(card.cardId, !card.listening)
                        ToolTip.visible: hovered; ToolTip.delay: 600
                        ToolTip.text: mixer.midiCanLearn ? "Fills in the MIDI message, channel and number from the next "
                                                           + "one received."
                                                         : "Turn MIDI control on to learn from the controller."
                    }

                    Dim { text: "Channel" }
                    ComboBox {
                        objectName: "midiChannel"
                        Layout.fillWidth: true
                        model: sec.choices.channels.map(c => c.text)
                        currentIndex: card.channel
                        onActivated: (i) => card.set("channel", i)
                    }

                    Dim {           // no number for "any program", nor for pitch bend (one per channel)
                        visible: !(card.byNumber && card.message === "program") && card.message !== "pitchbend"
                        text: card.message === "note" ? "Note" : card.message === "cc" ? "CC" : "Program"
                    }
                    // Made again when the message changes: a SpinBox doesn't redo its text for a new textFromValue.
                    Repeater {
                        model: [card.message]
                        delegate: SpinBox {
                            objectName: "midiNumber"
                            visible: !(card.byNumber && card.message === "program") && card.message !== "pitchbend"
                            Layout.fillWidth: true
                            editable: true
                            from: 0; to: 127
                            value: card.number
                            textFromValue: (v) => card.message === "program" ? String(v + 1)
                                                : card.message === "note" ? v + " (" + noteName(v) + ")" : String(v)
                            valueFromText: (t) => {
                                var v = parseInt(t)
                                if (isNaN(v)) return value
                                return Math.max(0, Math.min(127, card.message === "program" ? v - 1 : v))
                            }
                            validator: RegularExpressionValidator { regularExpression: /.*/ }
                            onValueModified: card.set("number", value)
                            function noteName(n) {
                                return ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"][n % 12]
                                       + (Math.floor(n / 12) - 1)
                            }
                        }
                    }

                    // behaviour: mutes
                    Dim { visible: card.what === "mute"; text: "Action" }
                    ComboBox {
                        objectName: "midiMode"
                        visible: card.what === "mute"
                        Layout.fillWidth: true
                        model: sec.choices.modes.map(m => m.text)
                        currentIndex: sec.choices.modes.findIndex(m => m.value === card.mode)
                        onActivated: (i) => card.set("mode", sec.choices.modes[i].value)
                    }

                    // behaviour: levels
                    Dim { visible: card.what === "level"; text: "Top" }
                    SpinBox {
                        objectName: "midiDbHi"
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
                        objectName: "midiDbLo"
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
                    objectName: "midiInvert"
                    visible: card.what !== "preset"
                    leftPadding: 0
                    text: card.what === "mute" ? "Inverted: high is sounding" : "Inverted: top is the bottom level"
                    checked: card.invert
                    onToggled: card.set("invert", checked)
                }
                Note {
                    visible: card.what === "level" && card.dbLo < sec.choices.dbFloor
                    text: "With −∞ at the bottom, the level follows a mixing desk's fader curve. A connection is "
                          + "never removed from here: its lowest is −90 dB."
                }
                Note {
                    visible: card.byNumber
                    text: card.message === "cc" ? "The CC value picks the preset: 0 recalls preset 1, 1 preset 2, …"
                                                : "The program number picks the preset: program 1 recalls preset 1, …"
                }
                Label {
                    objectName: "midiError"
                    visible: card.error !== ""
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: card.error
                    color: Theme.error; font.pixelSize: 11
                }
                RowLayout {
                    spacing: 8
                    Button {
                        objectName: "midiApply"
                        text: "Apply"
                        highlighted: true
                        onClicked: {
                            var err = mixer.saveMidiBinding({
                                id: card.isNew ? "" : card.cardId, what: card.what, target: card.target,
                                message: card.message, channel: card.channel, number: card.number,
                                invert: card.invert, mode: card.mode, dbLo: card.dbLo, dbHi: card.dbHi })
                            if (err !== "") { cards.setProperty(card.index, "error", err); return }
                            if (card.listening) sec.learn("", false)
                            if (card.isNew) { cards.remove(card.index); return }
                            cards.setProperty(card.index, "editing", false)
                            sec.sync()
                        }
                    }
                    Button {
                        objectName: "midiCancel"
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
