import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// The interface language key (review/i18n/language-switch.md, direction A): a globe, the shown language's own name and an
// arrow; it opens a self-drawn list whose first item follows the system, then every language there is by its own name.
// The current choice is ticked. Up and down move, Enter chooses, Esc and a press outside close; typing finds a language
// (in the search box when there are more than ten, else by jumping to the first name that starts with what was typed).
Item {
    id: root
    property var languages: []          // [{tag, name, english, dir}] from the launcher (i18n.language_choices)
    property string setting: "auto"     // the saved choice: "auto" or a tag
    property string shown: "en"         // the language the interface shows now
    property string system: "en"        // the system's language
    property var t: function(en, zh, values) { return en }
    signal picked(string value)
    readonly property bool searchable: languages.length > 10
    property string query: ""
    property int highlighted: 0
    property string typed: ""
    implicitWidth: key.implicitWidth; implicitHeight: key.implicitHeight
    // Names for tests and measuring; the header key and the settings key are two of these, so they carry their own name.
    readonly property string prefix: objectName || "language"
    readonly property int nameX: 32          // 8 px inset, the 16 px tick column, 8 px gap: the search text starts here too
    readonly property int ruleRoom: 9        // under "follow the system": 4 px, the 1 px rule, 4 px

    function nameOf(tag) { for (var i = 0; i < languages.length; i++) if (languages[i].tag === tag) return languages[i].name; return tag }
    function matches(item, text) {
        var q = text.trim().toLowerCase()
        if (!q) return true
        var words = (item.name + " " + item.english + " " + item.tag).toLowerCase().split(/[\s()\-]+/)
        for (var i = 0; i < words.length; i++) if (words[i].indexOf(q) === 0) return true
        return false
    }
    // The rows as listed: following the system first (not while searching), then the languages that match.
    readonly property var rows: (query ? [] : [{tag: "auto", name: t("Follow system ({name})", "跟随系统（{name}）", {name: nameOf(system)})}])
                                .concat(languages.filter(function(item) { return matches(item, query) }))
    function choose(index) {
        var row = rows[index]
        list.close()
        if (row && row.tag !== setting) root.picked(row.tag)
    }
    function move(step) { if (rows.length) highlighted = Math.max(0, Math.min(rows.length - 1, highlighted + step)) }
    function jump(letter) {
        typed = (typeTimer.running ? typed : "") + letter; typeTimer.restart()
        for (var i = 0; i < rows.length; i++) if (rows[i].tag !== "auto" && matches(rows[i], typed)) { highlighted = i; return }
    }
    Timer { id: typeTimer; interval: 900 }
    onQueryChanged: highlighted = 0
    onHighlightedChanged: rowList.positionViewAtIndex(highlighted, ListView.Contain)

    Button {
        id: key; objectName: root.prefix + "Key"
        anchors.right: parent.right
        implicitHeight: theme.heightSm
        topPadding: 0; bottomPadding: 0; leftPadding: 10; rightPadding: 10
        implicitWidth: keyRow.implicitWidth + leftPadding + rightPadding
        hoverEnabled: true
        onClicked: list.opened ? list.close() : list.open()
        Accessible.name: root.t("Interface language: {name}", "界面语言：{name}", {name: root.nameOf(root.shown)})
        // Icon, name and arrow share one centre line: the middle of the name's first glyph ink (a capital or a CJK square,
        // so a descender does not pull it down), placed on the middle of the key. It is measured at 16 times the size
        // (glyph boxes come back in whole pixels) with the application font, so a CJK name is measured in the fallback
        // font it is drawn with; hinting is off for the name and its measure. The baseline sits on a whole screen pixel.
        contentItem: Item {
            id: keyRow
            readonly property int gap: 7
            readonly property real glyph: 15
            implicitWidth: glyph + gap + label.width + gap + arrow.width
            readonly property real dpr: Screen.devicePixelRatio || 1
            TextMetrics { id: firstGlyph; font.hintingPreference: Font.PreferNoHinting; font.weight: label.font.weight; font.pixelSize: label.font.pixelSize * 16; text: label.text.charAt(0) }
            readonly property real glyphMiddle: (firstGlyph.tightBoundingRect.y + firstGlyph.tightBoundingRect.height / 2) / 16
            readonly property real baselineY: Math.round((height / 2 - glyphMiddle) * dpr) / dpr
            readonly property real middle: baselineY + glyphMiddle
            onMiddleChanged: { globe.requestPaint(); arrow.requestPaint() }
            Canvas {
                id: globe; objectName: root.prefix + "Globe"
                width: keyRow.glyph; height: keyRow.height
                property color ink: theme.text
                onInkChanged: requestPaint(); onHeightChanged: requestPaint()
                onPaint: {
                    var c = getContext("2d"); c.reset()
                    c.translate(0, keyRow.middle - width / 2); c.scale(width / 24, width / 24)
                    c.strokeStyle = ink; c.lineWidth = 2; c.lineCap = "round"; c.lineJoin = "round"; c.beginPath()
                    c.moveTo(21,12); c.arc(12,12,9,0,Math.PI*2); c.moveTo(3,12); c.lineTo(21,12)
                    c.moveTo(12,3); c.bezierCurveTo(7.5,7.5,7.5,16.5,12,21); c.moveTo(12,3); c.bezierCurveTo(16.5,7.5,16.5,16.5,12,21)
                    c.stroke()
                }
            }
            FText {
                id: label; objectName: root.prefix + "Name"
                x: globe.width + keyRow.gap; y: keyRow.baselineY - baselineOffset
                text: root.nameOf(root.shown); font.pixelSize: theme.micro + 1; font.hintingPreference: Font.PreferNoHinting; color: theme.text
                wrapMode: Text.NoWrap
            }
            Canvas {
                id: arrow; objectName: root.prefix + "Arrow"
                x: label.x + label.width + keyRow.gap
                width: 10; height: keyRow.height
                property color ink: theme.text
                onInkChanged: requestPaint(); onHeightChanged: requestPaint()
                onPaint: {
                    // Chevron 7 wide, 3.5 tall, drawn about the shared centre line.
                    var c = getContext("2d"); c.reset()
                    var m = keyRow.middle
                    c.strokeStyle = ink; c.lineWidth = 1.6; c.lineCap = "round"; c.lineJoin = "round"; c.beginPath()
                    c.moveTo(1.5, m - 1.75); c.lineTo(5, m + 1.75); c.lineTo(8.5, m - 1.75)
                    c.stroke()
                }
            }
        }
        background: Rectangle {
            radius: theme.radiusSm
            color: list.opened ? theme.raised : key.hovered ? theme.hover : "transparent"
            border.width: 1; border.color: list.opened ? theme.sheen : theme.border
        }
    }

    Popup {
        id: list; objectName: root.prefix + "List"
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        width: 264; leftPadding: 6; rightPadding: 6; topPadding: 4; bottomPadding: 4; focus: true
        x: root.width - width; y: key.height + 6
        // Height left below the key in the window (12 px clear of the bottom edge), taken when the list opens.
        property real room: 400
        onAboutToShow: {
            root.query = ""; search.text = ""
            var window = root.Window.window
            if (window) room = window.height - root.mapToItem(null, 0, key.height + 6).y - topPadding - bottomPadding - 12
            var at = 0
            for (var i = 0; i < root.rows.length; i++) if (root.rows[i].tag === root.setting) at = i
            root.highlighted = at
        }
        onOpened: (root.searchable ? search : keys).forceActiveFocus()
        background: Rectangle { radius: theme.radiusMd; color: theme.canvas; border.color: theme.sheen }
        contentItem: ColumnLayout {
            spacing: 0
            // Keys for the list: up, down, Enter, Esc (handled here too: the popup's own Esc needs the window to hold focus);
            // without a search box a typed letter jumps.
            Item {
                id: keys; objectName: root.prefix + "ListKeys"; implicitHeight: 0; Layout.preferredHeight: 0
                Keys.onPressed: function(event) {
                    if (event.key === Qt.Key_Escape) { list.close(); event.accepted = true }
                    else if (event.key === Qt.Key_Down) { root.move(1); event.accepted = true }
                    else if (event.key === Qt.Key_Up) { root.move(-1); event.accepted = true }
                    else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) { root.choose(root.highlighted); event.accepted = true }
                    else if (event.text && event.text.trim() && !(event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))) { root.jump(event.text); event.accepted = true }
                }
            }
            TextField {
                id: search; objectName: root.prefix + "Search"; visible: root.searchable
                Layout.fillWidth: true; Layout.bottomMargin: 4
                implicitHeight: theme.heightSm
                onTextChanged: root.query = text
                placeholderText: root.t("Search languages", "搜索语言")
                color: theme.text; placeholderTextColor: theme.disabled
                font.pixelSize: theme.micro + 1
                leftPadding: root.nameX; rightPadding: 10
                Keys.onPressed: function(event) {
                    if (event.key === Qt.Key_Escape) { list.close(); event.accepted = true }
                    else if (event.key === Qt.Key_Down) { root.move(1); event.accepted = true }
                    else if (event.key === Qt.Key_Up) { root.move(-1); event.accepted = true }
                    else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) { root.choose(root.highlighted); event.accepted = true }
                }
                background: Rectangle {
                    radius: theme.radiusSm; color: theme.bg; border.width: 1; border.color: search.activeFocus ? theme.accent : theme.border
                    FIcon { kind: "search"; ink: theme.muted; x: 10; anchors.verticalCenter: parent.verticalCenter; width: 14; height: 14 }
                }
            }
            FText {
                objectName: root.prefix + "None"; visible: !!root.query && root.rows.length === 0
                Layout.fillWidth: true; Layout.topMargin: 6; Layout.bottomMargin: 6; horizontalAlignment: Text.AlignHCenter
                text: root.t("No matching language", "没有找到这种语言"); color: theme.muted; font.pixelSize: theme.micro + 1
            }
            // At most ten rows show, and never more than the room below the key; the rest scroll, so a small window still
            // reaches every language and the list ends at its last row.
            ListView {
                id: rowList; objectName: root.prefix + "Rows"
                Layout.fillWidth: true
                readonly property real tenRows: theme.heightSm * 10 + (root.rows.length > 1 && root.rows[0].tag === "auto" ? root.ruleRoom : 0)
                Layout.preferredHeight: Math.min(contentHeight, tenRows, Math.max(theme.heightSm * 3, list.room - (search.visible ? search.height + 4 : 0)))
                clip: true; spacing: 0; boundsBehavior: Flickable.StopAtBounds
                model: root.rows
                ScrollBar.vertical: FScrollBar {}
                delegate: Item {
                    id: row
                    required property var modelData
                    required property int index
                    objectName: root.prefix + "Row_" + modelData.tag
                    // "Follow the system" carries the rule under it: 1 px with 4 px above and below.
                    readonly property bool ruled: modelData.tag === "auto" && root.rows.length > 1
                    width: ListView.view.width; height: theme.heightSm + (ruled ? root.ruleRoom : 0)
                    readonly property bool chosen: modelData.tag === root.setting
                    Rectangle {
                        width: parent.width; height: theme.heightSm; radius: theme.radiusXs
                        color: row.chosen ? theme.accentSubtle : row.index === root.highlighted ? theme.hover : "transparent"
                    }
                    Rectangle { visible: row.ruled; y: theme.heightSm + 4; width: parent.width; height: 1; color: theme.border }
                    // The tick has its own 16 px column on the left; rows without it leave the column empty, so every name
                    // starts on one line (with the search text above).
                    FIcon { objectName: row.objectName + "Tick"; visible: row.chosen; kind: "check"; ink: theme.accent
                            x: 9; y: (theme.heightSm - height) / 2; width: 14; height: 14 }
                    FText { x: root.nameX; y: (theme.heightSm - height) / 2; text: row.modelData.name; font.pixelSize: theme.micro + 1
                            font.weight: row.chosen ? Font.DemiBold : Font.Normal; color: theme.text; elide: Text.ElideRight
                            wrapMode: Text.NoWrap; lineHeight: 1; width: parent.width - root.nameX - 10 }
                    MouseArea {
                        width: parent.width; height: theme.heightSm; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onEntered: root.highlighted = row.index
                        onClicked: root.choose(row.index)
                    }
                    Accessible.role: Accessible.MenuItem; Accessible.name: row.modelData.name; Accessible.checked: row.chosen
                }
            }
        }
    }
}
