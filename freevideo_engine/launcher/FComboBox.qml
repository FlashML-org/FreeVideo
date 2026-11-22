import QtQuick
import QtQuick.Controls.Basic

// A choice list in the app's own look: a field-like button and a popup in the sheet colour.
ComboBox {
    id: control
    implicitHeight: theme.heightSm
    font.pixelSize: theme.micro
    leftPadding: 12; rightPadding: 28
    contentItem: Text {
        text: control.displayText; color: theme.text; font: control.font
        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
    }
    background: Rectangle {
        radius: theme.radiusSm; color: theme.raised
        border.width: control.visualFocus ? 2 : 1
        border.color: control.visualFocus ? theme.accent : control.hovered ? theme.sheen : theme.border
    }
    indicator: FIcon { kind: "chevron-down"; ink: theme.muted; x: control.width - width - 10; y: (control.height - height) / 2; width: 12; height: 12 }
    delegate: ItemDelegate {
        width: control.width; height: theme.heightSm
        leftPadding: 12; rightPadding: 12
        highlighted: control.highlightedIndex === index
        contentItem: Text {
            text: control.textAt(index)
            color: theme.text; font: control.font; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
        }
        background: Rectangle {
            radius: theme.radiusXs
            color: parent.highlighted ? theme.accentSubtle : parent.hovered ? theme.surface : "transparent"
        }
    }
    popup: Popup {
        y: control.height + 4; width: control.width; padding: 4
        implicitHeight: Math.min(contentItem.implicitHeight + 8, 240)
        contentItem: ListView {
            clip: true; implicitHeight: contentHeight; model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollBar.vertical: FScrollBar {}
        }
        background: Rectangle { radius: theme.radiusSm; color: theme.canvas; border.color: theme.sheen }
    }
}
