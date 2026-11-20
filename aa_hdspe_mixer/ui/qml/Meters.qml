pragma Singleton
import QtQuick

// Latest meter levels (unit key -> 0..1), converted from Python ONCE per frame in Main.qml.
// Pills bind to this instead of `mixer.levels`, which would convert the whole map per binding.
QtObject {
    property var levels: ({})
}
