pragma Singleton
import QtQuick

QtObject {
    // Two themes tuned for a mixer: neutral greys, colour only where it carries meaning.
    // `dark` is bound by Main.qml to the cog panel's Colour mode (System follows the desktop).
    property bool dark: true

    readonly property color window: dark ? "#1b1e23" : "#eceff3"
    readonly property color pane: dark ? "#21252b" : "#f7f8fa"
    readonly property color paneAlt: dark ? "#272c33" : "#e3e7ec"
    readonly property color bar: dark ? "#15171b" : "#dde1e7"
    readonly property color border: dark ? "#363c45" : "#bcc3cc"
    readonly property color text: dark ? "#e6e9ee" : "#1a1e24"
    readonly property color textDim: dark ? "#9aa3ae" : "#4c5562"
    readonly property color textFaint: dark ? "#6b7480" : "#77808c"
    readonly property color accent: dark ? "#29b6f6" : "#0284c7"
    readonly property color accentText: dark ? "#0b0d10" : "#ffffff"   // text on an accent background
    readonly property color input: dark ? "#4fc3a1" : "#1f9576"      // hardware inputs
    readonly property color playback: dark ? "#f0a35e" : "#d0731f"   // playback channels
    readonly property color output: dark ? "#9fa8da" : "#5463b8"     // outputs
    readonly property color error: dark ? "#ef5350" : "#d32f2f"
    readonly property color warning: dark ? "#ffca28" : "#b77900"
    readonly property color mute: dark ? "#ef5350" : "#d32f2f"       // muted channels and connections
    readonly property color meterBack: dark ? "#0f1114" : "#cfd4db"
    readonly property color meterLow: dark ? "#43a047" : "#2e9a3a"
    readonly property color meterMid: dark ? "#fdd835" : "#e0b800"
    readonly property color meterHigh: dark ? "#e53935" : "#d32f2f"
    // A quieter version of a colour, for borders: towards the background.
    function subdued(c) { return dark ? Qt.darker(c, 1.6) : Qt.tint(c, Qt.rgba(1, 1, 1, 0.45)) }
    readonly property int radius: 8
    readonly property int pillW: 148
    readonly property int pillH: 58
    readonly property int gap: 10

    readonly property real wireBaseHue: 195     // degrees; the first connection's wire colour

    // Wire colour of a connection: hue rotated by `step` degrees per connection, in creation order.
    // step 0 = colour by source kind (inputs green, playback orange).
    function wireColor(conn, step) {
        if (!step) return kindColor(conn.srcKind)
        var h = ((wireBaseHue + (conn.seq || 0) * step) % 360 + 360) % 360
        return Qt.hsla(h / 360, 0.72, dark ? 0.62 : 0.45, 1)
    }

    function kindColor(kind) {
        return kind === "in" ? input : kind === "pb" ? playback : output
    }
}
