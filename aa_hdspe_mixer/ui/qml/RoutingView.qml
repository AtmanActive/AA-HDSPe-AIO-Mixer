import QtQuick
import QtQuick.Controls

// Main pane: used sources on top, used outputs at the bottom, wires with level numbers between.
// Connections at -inf do not exist in the matrix view, so they are never drawn.
Flickable {
    id: view
    clip: true
    contentWidth: width
    contentHeight: content.height
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}

    readonly property int pad: 16
    readonly property int laneH: 28
    readonly property int labelW: 20                       // vertical SOURCES / OUTPUTS pillars
    readonly property int flowX: pad + labelW + Theme.gap  // pill rows start right of the pillars
    property var geo: []            // per-connection wire geometry
    property string hotKey: ""      // connection whose level label is under the mouse
    property string hotUnit: ""     // source/output pill under the mouse: all its connections light up
    readonly property bool anyHot: hotKey !== "" || hotUnit !== ""

    function isHot(c) {
        return c.key === hotKey || (hotUnit !== "" && (c.srcKey === hotUnit || c.dstKey === hotUnit))
    }
    function setHover(prop, key, on) {
        if (on) view[prop] = key
        else if (view[prop] === key) view[prop] = ""
    }
    onHotKeyChanged: canvas.requestPaint()
    onHotUnitChanged: canvas.requestPaint()
    readonly property var conns: mixer.connections
    // Repeaters below use a count as model and read their entry by index, so a change that keeps the
    // count (a level edit, a mute) updates delegates in place instead of re-creating them. Re-created
    // items lose a click that is in progress, e.g. clicking a second level while editing the first.
    readonly property var srcs: mixer.usedSources
    readonly property var dsts: mixer.usedOutputs

    function relayout() {
        var src = {}, dst = {}
        for (var i = 0; i < srcRep.count; ++i) {
            var it = srcRep.itemAt(i)
            if (it) src[it.unit.key] = { x: topFlow.x + it.x, y: topFlow.y + it.y + it.height, w: it.width }
        }
        for (i = 0; i < dstRep.count; ++i) {
            it = dstRep.itemAt(i)
            if (it) dst[it.unit.key] = { x: bottomFlow.x + it.x, y: bottomFlow.y + it.y, w: it.width }
        }
        var list = []
        for (i = 0; i < conns.length; ++i) {
            var c = conns[i]
            if (src[c.srcKey] && dst[c.dstKey]) list.push({ c: c, s: src[c.srcKey], d: dst[c.dstKey] })
        }
        // Lanes ordered by source position, then destination position: few crossings, stable.
        list.sort(function (a, b) {
            return (a.s.y - b.s.y) || (a.s.x - b.s.x) || (a.d.y - b.d.y) || (a.d.x - b.d.x)
        })
        // Spread exits along each source pill and entries along each destination pill.
        var outs = {}, ins = {}
        list.forEach(function (e) {
            (outs[e.c.srcKey] = outs[e.c.srcKey] || []).push(e);
            (ins[e.c.dstKey] = ins[e.c.dstKey] || []).push(e)
        })
        function spread(groups, sideKey, otherKey, field) {
            for (var k in groups) {
                var g = groups[k]
                g.sort(function (a, b) { return (a[otherKey].y - b[otherKey].y) || (a[otherKey].x - b[otherKey].x) })
                for (var j = 0; j < g.length; ++j) {
                    var p = g[j][sideKey]
                    var inset = p.w * 0.22
                    g[j][field] = g.length === 1 ? p.x + p.w / 2
                                                 : p.x + inset + (p.w - 2 * inset) * j / (g.length - 1)
                }
            }
        }
        spread(outs, "s", "d", "sx")
        spread(ins, "d", "s", "dx")
        var top = wires.y + pad
        var out = []
        for (i = 0; i < list.length; ++i) {
            var e = list[i]
            out.push({ key: e.c.key, conn: e.c, sx: e.sx, sy: e.s.y, dx: e.dx, dy: e.d.y,
                       ly: top + i * laneH + laneH / 2, color: Theme.wireColor(e.c, mixer.hueStep),
                       quiet: e.c.muted || e.c.silenced })
        }
        geo = out
        canvas.requestPaint()
    }
    function scheduleRelayout() { Qt.callLater(relayout) }

    readonly property int hueStep: mixer.hueStep
    onConnsChanged: scheduleRelayout()
    onHueStepChanged: scheduleRelayout()
    readonly property bool darkTheme: Theme.dark      // wire colours are baked into the layout
    onDarkThemeChanged: scheduleRelayout()
    onWidthChanged: scheduleRelayout()

    Item {
        id: content
        width: view.width
        height: bottomFlow.y + bottomFlow.height + view.pad

        Canvas {
            id: canvas
            anchors.fill: parent
            z: 0
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                ctx.lineCap = "round"; ctx.lineJoin = "round"
                for (var pass = 0; pass < 2; ++pass) {
                    for (var i = 0; i < view.geo.length; ++i) {
                        var g = view.geo[i]
                        var hot = view.isHot(g.conn)
                        if ((pass === 1) !== hot) continue      // draw the hovered wire last, on top
                        ctx.strokeStyle = g.color
                        ctx.globalAlpha = (!view.anyHot || hot ? 0.95 : 0.3) * (g.quiet ? 0.6 : 1)
                        ctx.lineWidth = hot ? 3 : 2
                        ctx.setLineDash(g.quiet ? [4, 4] : [])       // muted, or its source/output is
                        var r = Math.min(8, Math.abs(g.dx - g.sx) / 2)
                        var dir = g.dx >= g.sx ? 1 : -1
                        ctx.beginPath()
                        ctx.moveTo(g.sx, g.sy)
                        if (r < 1) {
                            ctx.lineTo(g.dx, g.dy)
                        } else {
                            ctx.lineTo(g.sx, g.ly - r)
                            ctx.quadraticCurveTo(g.sx, g.ly, g.sx + dir * r, g.ly)
                            ctx.lineTo(g.dx - dir * r, g.ly)
                            ctx.quadraticCurveTo(g.dx, g.ly, g.dx, g.ly + r)
                            ctx.lineTo(g.dx, g.dy)
                        }
                        ctx.stroke()
                        ctx.fillStyle = g.color
                        ctx.beginPath(); ctx.arc(g.sx, g.sy + 1, 3, 0, 2 * Math.PI); ctx.fill()
                        ctx.beginPath(); ctx.arc(g.dx, g.dy - 1, 3, 0, 2 * Math.PI); ctx.fill()
                    }
                }
            }
        }

        SideLabel {
            x: view.pad; y: topFlow.y
            height: topFlow.height
            text: "SOURCES"
            visible: srcRep.count > 0
        }
        Flow {
            id: topFlow
            x: view.flowX; y: view.pad
            width: view.width - view.flowX - view.pad
            spacing: Theme.gap
            z: 1
            Repeater {
                id: srcRep
                model: view.srcs.length
                delegate: ChannelPill {
                    unit: view.srcs[index] || ({})
                    clickable: true
                    selected: mixer.channelOpen && mixer.channelKey === unit.key
                    onClicked: mixer.showChannel(unit.key)
                    onHoveredChanged: view.setHover("hotUnit", unit.key, hovered)
                    onContextRequested: (x, y) => unitMenu.openFor(unit, this, x, y)
                }
            }
            onPositioningComplete: view.scheduleRelayout()
        }

        Item {
            id: wires
            y: topFlow.y + topFlow.height
            width: parent.width
            height: Math.max(56, view.geo.length * view.laneH + 2 * view.pad)
        }

        Repeater {
            model: view.geo.length
            delegate: GainLabel {
                z: 2
                readonly property var g: view.geo[index] || ({ conn: {}, dx: 0, ly: 0, key: "" })
                conn: g.conn
                wireColor: g.color || Theme.textFaint
                x: g.dx - width / 2
                y: g.ly - height / 2
                lit: view.isHot(g.conn)
                dimmed: view.anyHot && !lit
                onHoverChanged2: (on) => view.setHover("hotKey", g.key, on)
            }
        }

        SideLabel {
            x: view.pad; y: bottomFlow.y
            height: bottomFlow.height
            text: "OUTPUTS"
            visible: dstRep.count > 0
        }
        Flow {
            id: bottomFlow
            x: view.flowX; y: wires.y + wires.height
            width: view.width - view.flowX - view.pad
            spacing: Theme.gap
            z: 1
            // The row moves when the lane count changes (wires.height follows geo): wire ends must follow.
            onYChanged: view.scheduleRelayout()
            Repeater {
                id: dstRep
                model: view.dsts.length
                delegate: ChannelPill {
                    unit: view.dsts[index] || ({})
                    clickable: true
                    selected: mixer.channelOpen && mixer.channelKey === unit.key
                    onClicked: mixer.showChannel(unit.key)
                    onHoveredChanged: view.setHover("hotUnit", unit.key, hovered)
                    onContextRequested: (x, y) => unitMenu.openFor(unit, this, x, y)
                }
            }
            onPositioningComplete: view.scheduleRelayout()
        }
    }

    Column {
        parent: view
        anchors.centerIn: parent
        visible: view.conns.length === 0
        spacing: 12
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "No routing yet"
            color: Theme.textDim; font.pixelSize: 18
        }
        Text {
            objectName: "silencedNote"
            anchors.horizontalCenter: parent.horizontalCenter
            text: "The card has been fully silenced."
            color: Theme.textDim; font.pixelSize: 15
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Click Add to connect an input or playback channel to an output."
            color: Theme.textFaint; font.pixelSize: 13
        }
    }
}
