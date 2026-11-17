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
    spacing: 8
    FText { text: heading; font.weight: Font.DemiBold; font.pixelSize: theme.section; Layout.fillWidth: true }
    FText {
        text: release && release.development ? (zh ? "开发版本" : "Development version") : (zh ? "构建号：" : "Build: ") + (release && release.version || "—")
        color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true
    }
    FText { text: notes ? notes.summary : (zh ? "此版本未附带更新说明。" : "No release notes were included with this version."); Layout.fillWidth: true }
    Repeater {
        model: notes ? notes.changes : []
        delegate: FText { required property string modelData; text: "• " + modelData; color: theme.muted; Layout.fillWidth: true }
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
                delegate: FText { required property string modelData; text: "• " + modelData; color: theme.muted; Layout.fillWidth: true }
            }
        }
    }
}
