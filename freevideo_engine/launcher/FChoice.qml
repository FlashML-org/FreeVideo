import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Button {
    id: control
    property string detail: ""
    // false: an independent option with a check box, as in a model list.
    property bool exclusive: true
    checkable: true
    autoExclusive: exclusive
    implicitHeight: contentItem.implicitHeight + 32
    padding: 16
    hoverEnabled: true
    opacity: enabled ? 1 : 0.5
    contentItem: RowLayout {
        spacing: 12
        Rectangle {
            visible: control.exclusive
            Layout.alignment: Qt.AlignTop; Layout.topMargin: 2
            width: 18; height: 18; radius: 9; color: "transparent"
            border.width: control.checked ? 5 : 1.5
            border.color: control.checked ? theme.accent : theme.disabled
            Behavior on border.width { NumberAnimation { duration: 120 } }
        }
        Rectangle {
            visible: !control.exclusive
            Layout.alignment: Qt.AlignTop; Layout.topMargin: 2
            width: 18; height: 18; radius: theme.radiusXs + 1
            color: control.checked ? theme.accent : "transparent"
            border.width: control.checked ? 0 : 1.5; border.color: theme.disabled
            Behavior on color { ColorAnimation { duration: 110 } }
            FText { anchors.centerIn: parent; visible: control.checked; text: "✓"; color: theme.bg; font.pixelSize: 12; font.weight: Font.Bold }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; spacing: 4
            FText { text: control.text; font.pixelSize: theme.strong; font.weight: Font.DemiBold; Layout.fillWidth: true }
            FText { text: control.detail; font.pixelSize: theme.micro; color: theme.muted; Layout.fillWidth: true }
        }
    }
    background: Rectangle {
        radius: theme.radiusMd; color: control.checked ? theme.accentSubtle : control.hovered ? theme.raised : theme.surface
        border.color: control.checked || control.activeFocus ? theme.accent : control.hovered ? theme.sheen : theme.border
        border.width: control.activeFocus && control.visualFocus ? 2 : 1
        Behavior on color { ColorAnimation { duration: 110 } }
    }
    Accessible.role: exclusive ? Accessible.RadioButton : Accessible.CheckBox
    Accessible.name: text
    Accessible.description: detail
}
