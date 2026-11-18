import QtQuick

// While more of a scrolled window's notes is below, their last lines fade into the window above the bottom edge, so
// a line cut in half by the edge is not seen; at the end nothing covers them. As the app's update window does.
Rectangle {
    id: fade
    // The ScrollView it sits over; the fade draws on top of it, not inside its content.
    property var view: null
    property color fill: theme.canvas
    readonly property var flick: view ? view.contentItem : null
    // Kept clear of the view's scroll bar.
    property real barWidth: 0
    parent: view
    z: 2
    x: 0; height: 56; y: view ? view.height - height : 0
    width: view ? view.width - barWidth : 0
    visible: !!flick && flick.contentHeight > flick.height + 1 && flick.contentY + flick.height < flick.contentHeight - 1
    gradient: Gradient {
        GradientStop { position: 0; color: Qt.rgba(fade.fill.r, fade.fill.g, fade.fill.b, 0) }
        GradientStop { position: 0.72; color: fade.fill }
        GradientStop { position: 1; color: fade.fill }
    }
}
