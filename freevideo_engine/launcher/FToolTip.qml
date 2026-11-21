import QtQuick
import QtQuick.Controls.Basic

// The launcher's hover tip: the sheet colour, a hairline edge and the app's own text, as the page tip in the editor.
ToolTip {
    id: tip
    delay: 400; padding: 8; leftPadding: 12; rightPadding: 12
    contentItem: Text {
        text: tip.text; color: theme.text; font.pixelSize: theme.micro; wrapMode: Text.Wrap
        width: Math.min(implicitWidth, 320)
    }
    background: Rectangle { radius: theme.radiusSm; color: theme.raised; border.color: theme.sheen }
}
