import QtQuick
import QtQuick.Layouts

ColumnLayout {
    property var release: ({})
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
}
