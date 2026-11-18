import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: notesRoot
    property var release: ({})
    // Versions between the installed one and `release`, newest first.
    property var earlier: []
    property bool zh: false
    property string heading: ""
    readonly property var notes: release && release.release_notes ? release.release_notes[zh ? "zh" : "en"] : null
    // The width of the window the notes sit in. The layout is chosen from it, not from the notes' own width: that
    // width changes when the scroll bar comes or goes, and the scroll bar depends on how tall this layout makes the notes.
    property real room: 0
    // Narrow windows put a change's label above its sentence instead of beside it.
    readonly property bool stacked: room < 420
    spacing: 8

    // "Topic: what changed" shows its topic as a short label before the sentence, as the app does; an item without a
    // short topic stays one sentence after a bullet.
    function topic(text) {
        var match = /^([^:：\n]{1,40})(?:：|: )([\s\S]+)$/.exec(text || "")
        if (!match)
            return null
        var name = match[1].trim(), wide = /[　-鿿＀-￯]/.test(name), sentence = match[2].trim()
        // Standing alone, an English sentence starts with a capital letter; a first word with its own capitals (macOS) stays.
        return name.length <= (wide ? 16 : 40) ? [name, sentence.replace(/^[a-z](?=[a-z]*(?![A-Za-z]))/, function(letter) { return letter.toUpperCase() })] : null
    }
    FontMetrics { id: tagFont; font.pixelSize: theme.micro }
    // One label column per list, so every sentence starts at the same place.
    function tagColumn(changes) {
        var widest = 0
        for (var i = 0; i < (changes || []).length; i++) {
            var parts = topic(changes[i])
            if (parts)
                widest = Math.max(widest, tagFont.advanceWidth(parts[0]))
        }
        return widest ? Math.min(Math.ceil(widest) + 12, Math.round(room * 0.4)) : 0
    }
    // One change: its label (parts[0]) and sentence (parts[1]), or the whole text after a bullet.
    component Change: GridLayout {
        id: change
        property string text
        property var parts: null
        property real column: 0
        property bool stacked: false
        columns: stacked || !parts ? 1 : 2; columnSpacing: 10; rowSpacing: 4
        Item {
            visible: !!change.parts
            // Stacked, a label is as wide as its words up to the width the notes have, and is cut short past it: the
            // window's width (room) also holds the scroll bar's strip, so it would push the notes under the bar.
            Layout.fillWidth: change.stacked
            Layout.maximumWidth: change.stacked ? tag.implicitWidth + 12 : change.column
            Layout.preferredWidth: change.stacked ? tag.implicitWidth + 12 : change.column
            Layout.preferredHeight: tag.implicitHeight + 2; Layout.alignment: Qt.AlignTop | Qt.AlignLeft; Layout.topMargin: 1
            Rectangle { width: Math.min(parent.width, tag.implicitWidth + 12); height: parent.height; radius: theme.radiusXs; color: theme.raised }
            Text {
                id: tag; x: 6; y: 1; width: parent.width - 12
                text: change.parts ? change.parts[0] : ""; color: theme.accent; font.pixelSize: theme.micro; elide: Text.ElideRight
            }
        }
        FText { text: change.parts ? change.parts[1] : "• " + change.text; color: change.parts ? theme.text : theme.muted; Layout.fillWidth: true }
    }

    FText { text: heading; font.weight: Font.DemiBold; font.pixelSize: theme.section; Layout.fillWidth: true }
    FText {
        text: release && release.development ? (zh ? "开发版本" : "Development version") : (zh ? "构建号：" : "Build: ") + (release && release.version || "—")
        color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true
    }
    FText { text: notes ? notes.summary : (zh ? "此版本未附带更新说明。" : "No release notes were included with this version."); Layout.fillWidth: true }
    Repeater {
        model: notes ? notes.changes : []
        delegate: Change {
            required property string modelData
            text: modelData; parts: notesRoot.topic(modelData); stacked: notesRoot.stacked; Layout.fillWidth: true
            column: notesRoot.tagColumn(notesRoot.notes ? notesRoot.notes.changes : [])
        }
    }
    Repeater {
        model: earlier
        delegate: ColumnLayout {
            id: version
            required property var modelData
            readonly property var notes: modelData.release_notes ? modelData.release_notes[notesRoot.zh ? "zh" : "en"] : null
            Layout.fillWidth: true; Layout.topMargin: 6; spacing: 4
            FText { text: "v" + version.modelData.product_version; font.weight: Font.DemiBold; Layout.fillWidth: true }
            FText { text: version.notes ? version.notes.summary : ""; Layout.fillWidth: true }
            Repeater {
                model: version.notes ? version.notes.changes : []
                delegate: Change {
                    required property string modelData
                    text: modelData; parts: notesRoot.topic(modelData); stacked: notesRoot.stacked; Layout.fillWidth: true
                    column: notesRoot.tagColumn(version.notes ? version.notes.changes : [])
                }
            }
        }
    }
}
