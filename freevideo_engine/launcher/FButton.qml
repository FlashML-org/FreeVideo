import QtQuick
import QtQuick.Controls.Basic

Button {
    id: control
    property bool primary: false
    property bool selected: false
    property bool danger: false
    property bool singleLine: false
    // A disabled button that is the only statement of what is happening: full strength, not a call to act.
    property bool waiting: false
    // A text link: accent text, no frame.
    property bool link: false
    implicitHeight: Math.max(theme.height, contentItem.implicitHeight + 14)
    // A text the button may change to: its width is the wider of the two, so the row does not move.
    property string widthText: ""
    TextMetrics { id: reserve; font: control.font; text: control.widthText }
    implicitWidth: Math.max(80, Math.max(contentItem.implicitWidth, reserve.advanceWidth) + leftPadding + rightPadding)
    leftPadding: 16; rightPadding: 16
    topPadding: 7; bottomPadding: 7
    font.pixelSize: theme.body; font.weight: primary ? Font.DemiBold : Font.Medium
    hoverEnabled: true
    opacity: enabled || waiting ? 1 : 0.42
    contentItem: Text {
        text: control.text; font: control.font
        color: control.waiting ? theme.text : control.primary ? theme.bg : control.danger ? theme.danger : control.selected ? theme.accent
             : control.link ? (control.hovered ? theme.accentHover : theme.accent)
             : control.flat && !control.hovered ? theme.muted : theme.text
        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        wrapMode: control.singleLine ? Text.NoWrap : Text.Wrap
        elide: control.singleLine ? Text.ElideRight : Text.ElideNone
        Behavior on color { ColorAnimation { duration: 110 } }
    }
    background: Rectangle {
        radius: theme.radiusSm
        color: control.waiting ? theme.raised : control.primary ? (control.down ? theme.accentDim : control.hovered ? theme.accentHover : theme.accent) :
               control.selected ? theme.accentSubtle : control.down ? theme.sheen : control.hovered ? theme.hover :
               control.flat ? "transparent" : theme.raised
        border.width: control.activeFocus && control.visualFocus ? 2 : (control.primary && !control.waiting) || control.flat ? 0 : 1
        border.color: control.activeFocus && control.visualFocus ? theme.accentHover : control.selected ? theme.accentDim : theme.border
        Behavior on color { ColorAnimation { duration: 110 } }
    }
    Accessible.name: text
}
