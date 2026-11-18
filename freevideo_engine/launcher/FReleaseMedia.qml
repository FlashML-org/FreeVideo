import QtQuick

// The picture or video above a version's notes (release.media). The cover is drawn at once; the file goes over it once
// it is on this computer and plays. A video needs Qt Multimedia: it plays muted and loops, and a click on it or on the
// speaker turns the sound on or off. Without Qt Multimedia a video keeps its cover; a GIF or WebP moves as it is.
// Without media, or without a cover and file, nothing is drawn.
Item {
    id: root
    // {kind: "video" | "animated" | "image", poster, source}: file URLs, "" while absent.
    property var media: null
    property bool zh: false
    // The window's corner radius; the top corners are rounded to it when the block sits on the window's top edge.
    property real radius: 0
    property color cornerColor: theme.canvas
    property color edgeColor: theme.sheen
    property real maxHeight: 100000
    readonly property string kind: media ? (media.kind || "image") : ""
    readonly property bool videoPlaying: videoLoader.status === Loader.Ready && !!videoLoader.item && videoLoader.item.playing
    readonly property bool motionShown: kind === "video" ? videoPlaying : motion.status === Image.Ready
    readonly property bool shown: !!media && (cover.status === Image.Ready || motionShown)
    property bool soundOn: false
    visible: shown
    implicitHeight: shown ? Math.min(Math.round(width * 9 / 16), maxHeight) : 0
    clip: true
    onMediaChanged: soundOn = false

    Rectangle { anchors.fill: parent; color: "#000000" }
    Image {
        id: cover; anchors.fill: parent
        source: root.media && root.media.poster ? root.media.poster : ""
        fillMode: Image.PreserveAspectCrop; asynchronous: true; cache: false; smooth: true; mipmap: true
    }
    // A GIF or WebP moves; a still picture is shown as it is.
    AnimatedImage {
        id: motion; anchors.fill: parent
        source: root.media && root.kind !== "video" && root.media.source ? root.media.source : ""
        fillMode: Image.PreserveAspectCrop; asynchronous: true; cache: false; smooth: true
        playing: root.visible && root.kind === "animated"
        opacity: status === Image.Ready ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
    }
    Loader {
        id: videoLoader; anchors.fill: parent
        active: root.kind === "video" && !!root.media.source && multimedia && root.visible
        source: "FReleaseVideo.qml"
        opacity: root.videoPlaying ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
        onLoaded: { item.muted = Qt.binding(function() { return !root.soundOn }); item.source = root.media.source }
    }
    MouseArea {
        anchors.fill: parent; enabled: root.videoPlaying
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: root.soundOn = !root.soundOn
    }
    // The speaker, once the video plays.
    Rectangle {
        objectName: "releaseMediaSound"
        visible: root.videoPlaying
        width: 32; height: 32; radius: 16
        anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 12
        color: soundArea.containsMouse ? "#2a2c31" : "#141518"
        Accessible.role: Accessible.Button
        Accessible.name: root.zh ? (root.soundOn ? "关闭声音" : "打开声音") : (root.soundOn ? "Turn the sound off" : "Turn the sound on")
        Canvas {
            id: speaker; anchors.centerIn: parent; width: 16; height: 16
            property bool on: root.soundOn
            onOnChanged: requestPaint()
            onPaint: {
                var c = getContext("2d"); c.reset(); c.scale(width / 24, height / 24)
                c.strokeStyle = "#f3f5f8"; c.lineWidth = 2; c.lineCap = "round"; c.lineJoin = "round"
                c.beginPath(); c.moveTo(11, 5); c.lineTo(6, 9); c.lineTo(3, 9); c.lineTo(3, 15); c.lineTo(6, 15); c.lineTo(11, 19); c.closePath(); c.stroke()
                c.beginPath()
                if (on) { c.arc(12, 12, 4.5, -Math.PI / 4, Math.PI / 4); c.moveTo(18.4, 5.6); c.arc(12, 12, 9, -Math.PI / 4, Math.PI / 4) }
                else { c.moveTo(23, 9); c.lineTo(17, 15); c.moveTo(17, 9); c.lineTo(23, 15) }
                c.stroke()
            }
        }
        MouseArea { id: soundArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.soundOn = !root.soundOn }
    }
    // The window's rounded top corners over the picture, with its edge line.
    Canvas {
        anchors.fill: parent; visible: root.radius > 0
        property color fill: root.cornerColor
        property color edge: root.edgeColor
        onFillChanged: requestPaint()
        onEdgeChanged: requestPaint()
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            var c = getContext("2d"), r = root.radius, w = width; c.reset()
            c.fillStyle = fill
            c.beginPath(); c.moveTo(0, 0); c.lineTo(r, 0); c.arc(r, r, r, -Math.PI / 2, Math.PI, true); c.closePath(); c.fill()
            c.beginPath(); c.moveTo(w, 0); c.lineTo(w, r); c.arc(w - r, r, r, 0, -Math.PI / 2, true); c.closePath(); c.fill()
            c.strokeStyle = edge; c.lineWidth = 1
            c.beginPath(); c.moveTo(0.5, height); c.lineTo(0.5, r + 0.5); c.arc(r + 0.5, r + 0.5, r, Math.PI, -Math.PI / 2)
            c.lineTo(w - r - 0.5, 0.5); c.arc(w - r - 0.5, r + 0.5, r, -Math.PI / 2, 0); c.lineTo(w - 0.5, height); c.stroke()
        }
    }
}
