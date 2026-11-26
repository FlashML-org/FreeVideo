import QtQuick

Text {
    color: theme.text
    font.family: backend.fontFamily      // the interface language's font order (Traditional Chinese has its own)
    font.pixelSize: theme.body
    wrapMode: Text.Wrap
    lineHeight: 1.2
    textFormat: Text.PlainText
}
