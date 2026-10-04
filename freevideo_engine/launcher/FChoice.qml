import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Button {
    id: control
    property string detail: ""
    checkable: true
    autoExclusive: true
    implicitHeight: contentItem.implicitHeight + 32
    padding: 16
    hoverEnabled: true
    opacity: enabled ? 1 : 0.5
    contentItem: RowLayout {
        spacing: 12
        Rectangle {
            Layout.alignment: Qt.AlignTop; Layout.topMargin: 2
            width: 18; height: 18; radius: 9; color: "transparent"
            border.width: control.checked ? 5 : 1.5
            border.color: control.checked ? theme.accent : theme.disabled
            Behavior on border.width { NumberAnimation { duration: 120 } }
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
    Accessible.role: Accessible.RadioButton
    Accessible.name: text
    Accessible.description: detail
}
