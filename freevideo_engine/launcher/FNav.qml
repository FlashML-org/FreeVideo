import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Sidebar entry: icon and label, left aligned; the current page is marked.
Button {
    id: control
    property string glyph: "play"
    property bool selected: false
    property bool iconOnly: false
    implicitHeight: theme.height + 2
    implicitWidth: iconOnly ? 44 : 0
    leftPadding: iconOnly ? 0 : 12; rightPadding: iconOnly ? 0 : 12
    hoverEnabled: true
    opacity: enabled ? 1 : 0.45
    contentItem: RowLayout {
        spacing: control.iconOnly ? 0 : 11
        FIcon {
            kind: control.glyph; ink: control.selected ? theme.accent : theme.muted
            knockout: control.selected ? theme.raised : control.hovered ? theme.surface : theme.bg
            Layout.preferredWidth: 18; Layout.preferredHeight: 18
            Layout.alignment: control.iconOnly ? Qt.AlignHCenter : Qt.AlignVCenter
        }
        FText {
            visible: !control.iconOnly
            text: control.text; Layout.fillWidth: true; elide: Text.ElideRight; maximumLineCount: 1
            font.weight: control.selected ? Font.DemiBold : Font.Medium
            color: control.selected ? theme.text : control.hovered ? theme.text : theme.muted
        }
    }
    background: Rectangle {
        radius: theme.radiusSm
        color: control.selected ? theme.raised : control.hovered ? theme.surface : "transparent"
        border.width: control.visualFocus ? 2 : 0; border.color: theme.accentHover
        Behavior on color { ColorAnimation { duration: 140 } }
        Rectangle { visible: control.selected; width: 3; height: 18; radius: 2; color: theme.accent; x: 0; anchors.verticalCenter: parent.verticalCenter }
    }
    Accessible.name: text
    FToolTip {
        objectName: "navToolTip"
        visible: control.iconOnly && control.hovered && !win.dialogShown
        text: control.text
        // Open into the content area instead of centring outside the left edge.
        x: control.width + 8
        y: (control.height - height) / 2
    }
}
