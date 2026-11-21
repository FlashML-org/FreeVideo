import QtQuick
import QtQuick.Controls.Basic

// A thin rounded bar in the app's colours; it grows a little under the pointer.
ScrollBar {
    id: bar
    implicitWidth: 10; implicitHeight: 10
    padding: 2
    // The view's own bar sits along its edge; a replacement has to place itself the same way.
    readonly property Item view: parent
    readonly property bool upright: orientation === Qt.Vertical
    x: !view ? 0 : upright ? (view.mirrored === true ? 0 : view.width - width) : (view.leftPadding || 0)
    y: !view ? 0 : upright ? (view.topPadding || 0) : view.height - height
    width: !view ? implicitWidth : upright ? implicitWidth : (view.availableWidth || view.width)
    height: !view ? implicitHeight : upright ? (view.availableHeight || view.height) : implicitHeight
    contentItem: Rectangle {
        implicitWidth: 6; implicitHeight: 6; radius: 3
        color: bar.pressed ? theme.muted : bar.hovered ? theme.disabled : theme.border
        opacity: bar.size < 1 && bar.policy !== ScrollBar.AlwaysOff ? 1 : 0
    }
    background: null
}
