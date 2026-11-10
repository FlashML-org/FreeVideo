import QtQuick

// Capsule progress. `fraction` is the reported value, -1 while unknown.
// `shown` eases toward it, so the fill and any percentage bound to `shown`
// move together instead of jumping between status refreshes. While work
// runs, a soft sheen crosses the fill; unknown progress sweeps one way, as
// on macOS; a finished bar settles into the success colour. The fill is one accent colour, with no gradient.
Rectangle {
    id: meter
    property real fraction: -1
    property bool active: false
    property bool subdued: false
    readonly property bool known: fraction >= 0
    readonly property bool complete: known && fraction >= 1
    property real shown: Math.max(0, Math.min(1, fraction))
    Behavior on shown { NumberAnimation { duration: 520; easing.type: Easing.OutCubic } }
    implicitHeight: subdued ? 4 : 8
    radius: height / 2; color: theme.raised
    clip: true

    // The fill stays the first child: callers and tests read its width.
    Rectangle {
        id: fill
        visible: meter.known
        height: parent.height; radius: parent.radius
        // Never narrower than it is tall, so a small value is still a capsule.
        width: meter.shown > 0 ? Math.max(height, meter.shown * parent.width) : 0
        clip: true
        color: meter.complete ? theme.success : meter.subdued ? theme.accentDim : theme.accent
        Behavior on color { ColorAnimation { duration: 420 } }
        // Inset by the end radius, so the sheen never squares off the capsule.
        Item {
            x: fill.radius; width: Math.max(0, fill.width - 2 * fill.radius); height: fill.height
            clip: true
            Rectangle {
                id: sheen
                visible: meter.active && !meter.complete && !meter.subdued && fill.width > meter.height * 4
                width: Math.max(48, meter.width * 0.24); height: parent.height
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: "#00ffffff" }
                    GradientStop { position: 0.5; color: "#38ffffff" }
                    GradientStop { position: 1; color: "#00ffffff" }
                }
                SequentialAnimation on x {
                    running: sheen.visible; loops: Animation.Infinite
                    NumberAnimation { from: -sheen.width; to: fill.width + sheen.width; duration: 1700; easing.type: Easing.InOutSine }
                    PauseAnimation { duration: 1100 }
                }
            }
        }
    }
    // Unknown progress: one segment travels left to right, growing through
    // the middle of the track and easing at both ends.
    Rectangle {
        id: sweep
        visible: meter.active && !meter.known && width > 0
        property real t: 0
        readonly property real span: meter.width * (0.14 + 0.2 * Math.sin(Math.PI * t))
        readonly property real start: -span + t * (meter.width + span)
        // Clamped to the track, so both ends stay round as it enters and leaves.
        x: Math.max(0, start)
        width: Math.max(0, Math.min(meter.width, start + span) - x)
        height: parent.height; radius: parent.radius
        // Fades in and out at the ends instead of showing a hairline sliver.
        opacity: Math.min(1, width / (height * 2))
        color: meter.subdued ? theme.accentDim : theme.accent
        NumberAnimation on t {
            running: meter.active && !meter.known; loops: Animation.Infinite
            from: 0; to: 1; duration: 1500; easing.type: Easing.InOutCubic
        }
    }
}
