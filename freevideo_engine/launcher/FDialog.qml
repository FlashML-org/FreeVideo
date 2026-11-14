import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic

// A short question with one confirming action.
FPopup {
    id: dialog
    property string title: ""
    property string text: ""
    property string acceptText: "OK"
    property string rejectText: ""
    // A second answer between cancelling and accepting, such as "Exit" beside "Run in background".
    property string altText: ""
    property bool destructive: false
    // The confirming button takes the keyboard focus, so Enter answers it; left off for a dialog that stops something.
    property bool defaultFocus: false
    signal accepted()
    signal rejected()
    signal alternative()
    // Escape or an outside click counts as the non-confirming answer.
    property bool answered: false
    onAboutToShow: answered = false
    onClosed: if (!answered) rejected()
    width: Math.min(460, (parent ? parent.width : 500) - 40)
    closePolicy: rejectText ? Popup.CloseOnEscape : Popup.NoAutoClose
    contentItem: ColumnLayout {
        spacing: 12
        FText { text: dialog.title; font.pixelSize: theme.section; font.weight: Font.DemiBold; Layout.fillWidth: true }
        FText { text: dialog.text; color: theme.muted; Layout.fillWidth: true }
        RowLayout {
            Layout.fillWidth: true; Layout.topMargin: 12; spacing: 8
            Item { Layout.fillWidth: true }
            FButton { visible: !!dialog.rejectText; text: dialog.rejectText; flat: true; onClicked: dialog.close() }
            FButton { objectName: "dialogAltButton"; visible: !!dialog.altText; text: dialog.altText; danger: true; onClicked: { dialog.answered = true; dialog.alternative(); dialog.close() } }
            FButton {
                objectName: "dialogAcceptButton"
                text: dialog.acceptText; primary: !dialog.destructive; danger: dialog.destructive
                focus: dialog.defaultFocus && !dialog.destructive
                Keys.onReturnPressed: if (focus) clicked()
                Keys.onEnterPressed: if (focus) clicked()
                onClicked: { dialog.answered = true; dialog.accepted(); dialog.close() }
            }
        }
    }
}
