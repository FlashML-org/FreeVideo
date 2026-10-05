import QtQuick
import QtQuick.Controls.Basic

// Modal sheet shared by every launcher dialog.
Popup {
    anchors.centerIn: Overlay.overlay
    modal: true; focus: true; padding: 28
    background: Rectangle { radius: theme.radiusLg; color: theme.canvas; border.color: theme.sheen }
    Overlay.modal: Rectangle { color: theme.scrim }
    enter: Transition { ParallelAnimation {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 160; easing.type: Easing.OutCubic }
        NumberAnimation { property: "scale"; from: 0.98; to: 1; duration: 200; easing.type: Easing.OutCubic } } }
    exit: Transition { ParallelAnimation {
        NumberAnimation { property: "opacity"; to: 0; duration: 130; easing.type: Easing.InCubic }
        NumberAnimation { property: "scale"; to: 0.98; duration: 130; easing.type: Easing.InCubic } } }
}
