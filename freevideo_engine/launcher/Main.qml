import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ApplicationWindow {
    id: win
    title: "FreeVideo"
    color: theme.bg
    width: 1280; height: 860
    property var s: initialState
    Connections { target: backend; function onChanged() { win.s = backend.state } }
    property bool settingsOpen: false
    property bool terminalOpen: false
    property string settingsTab: "downloads"
    property string modelInfo: "video"
    property bool modelInfoOpen: false
    property bool closePending: false
    property bool accepted: false
    property bool manualUpdate: false
    property string previousStatus: ""
    property string previousReview: ""
    property bool compact: width < 1000
    property bool shortWindow: height < 700
    property bool otherModelLinksOpen: false
    readonly property bool manualEnvironment: s.form.new_comfy && s.form.environment_method === "manual"
    readonly property bool usingRuntime: manualEnvironment && s.offline.runtime
    readonly property bool needsRuntime: s.page === "comfy" && manualEnvironment && !s.offline.runtime
    readonly property bool offlineSelected: usingRuntime || s.form.model_method === "manual"
    readonly property bool needsPackages: s.page === "models" && offlineSelected && s.offline.models === 0 && (usingRuntime || s.form.model_dirs.length === 0)
    readonly property int step: s.page === "comfy" ? 0 : s.page === "models" ? 1 : 2
    property string previousPage: ""
    property bool errorDetailsOpen: false
    property string previousError: ""
    function t(en, zh) { return s.zh ? zh : en }
    function sourceName(value) { return ({"auto": t("Automatic", "自动选择"), "official": "Hugging Face", "hf-mirror": t("HF Mirror", "HF 镜像"), "modelscope": t("ModelScope", "魔搭")})[value] || value }
    function number(n) { return typeof n === "number" && isFinite(n) }
    function fraction(row) { return row && number(row.total) && row.total > 0 && number(row.done) && row.done <= row.total ? row.done / row.total : -1 }
    function bytes(n) { return n >= 1073741824 ? (n/1073741824).toFixed(1)+" GiB" : (n/1048576).toFixed(1)+" MiB" }
    readonly property bool updateOffered: s.update.engine || (!!s.update.candidate && ["available", "downloading", "ready", "error", "cancelled"].indexOf(s.update.status) >= 0)
    // An engine update needs no download; offer it as the way to launch.
    readonly property bool updateFirst: s.page === "launcher" && s.update.engine && !s.update.phase && !s.busy && s.status !== "open" && s.status !== "restart-required" && !s.needs_consent
    function percent(row) { return row && row.total ? Math.floor(100 * row.done / row.total) + "%" : "" }
    function updateHeadline() {
        var phase = s.update.phase
        if (phase === "downloading") return t("Downloading update ", "正在下载更新 ") + percent(s.update.progress)
        if (phase === "waiting") return t("Updating after the current video finishes", "当前视频生成完成后自动更新")
        if (phase === "restarting") return t("Restarting FreeVideo…", "正在重启 FreeVideo…")
        if (phase === "engine") return t("Updating the engine…", "正在更新引擎…")
        if (phase === "checking") return t("Checking for updates…", "正在检查更新…")
        if (s.update.candidate) return t("FreeVideo ", "FreeVideo ") + s.update.candidate.version + t(" is available", " 可以更新")
        return t("New engine ", "新版引擎 ") + s.update.current + t(" is ready", " 已就绪")
    }
    function updateExplanation() {
        var phase = s.update.phase
        if (phase === "downloading") return t("FreeVideo restarts when the download finishes. Models and settings are kept.", "下载完成后自动重启，模型和设置都会保留。")
        if (phase === "waiting") return t("Running videos are not interrupted. FreeVideo restarts on its own afterwards.", "不会中断正在生成的视频，完成后自动重启更新。")
        if (phase === "restarting" || phase === "engine") return t("ComfyUI restarts once; open FreeVideo pages refresh automatically.", "ComfyUI 会重启一次，已打开的 FreeVideo 页面会自动刷新。")
        if (phase === "checking") return ""
        if (s.update.status === "error" && s.update.error) return s.update.error
        if (s.update.candidate) return t("Current version ", "当前版本 ") + s.update.current + t(". Updating keeps your models and settings and takes about a minute.", "。更新会保留模型和设置，约需 1 分钟。")
        return t("Installed engine ", "已安装引擎 ") + (s.update.installed || "—") + t(". Updating takes about a minute and keeps your models and settings.", "。更新约需 1 分钟，模型和设置都会保留。")
    }
    function primaryText() {
        if (needsRuntime) return t("Choose environment package", "选择运行环境包")
        if (s.page === "comfy") return t("Continue", "继续")
        if (needsPackages) return t("Choose offline packages", "选择离线包")
        if (s.page === "models") return t("Check & continue", "检查并继续")
        if (updateFirst) return t("Update & launch", "更新并启动")
        if (s.page === "launcher") return s.status === "open" ? t("Open FreeVideo", "打开 FreeVideo") : s.status === "restart-required" ? t("Connect again", "重新连接") : t("Launch FreeVideo", "启动 FreeVideo")
        return s.status === "review" ? t("Install & launch", "安装并启动") : s.status === "restart-required" ? t("Connect again", "重新连接") : t("Check & resume", "检查并继续")
    }
    onSChanged: {
        if (s.page !== previousPage) {
            Qt.callLater(function() { scroll.contentItem.contentY = 0 })
            if (previousPage) pageEnter.restart()
        }
        previousPage = s.page
        if (s.status !== previousStatus || s.review_id !== previousReview) accepted = false
        previousStatus = s.status; previousReview = s.review_id
        if (s.error !== previousError) {
            errorDetailsOpen = false
            if (s.error) Qt.callLater(function() { scroll.contentItem.contentY = 0 })
        }
        previousError = s.error
    }
    // A new page settles in rather than snapping.
    ParallelAnimation {
        id: pageEnter
        NumberAnimation { target: page; property: "opacity"; from: 0; to: 1; duration: 220; easing.type: Easing.OutCubic }
        NumberAnimation { target: pageShift; property: "y"; from: 8; to: 0; duration: 260; easing.type: Easing.OutCubic }
    }
    onClosing: function(event) {
        event.accepted = false
        if (s.busy) closePending = true
        else backend.close()
    }

    Rectangle {
        id: sidebar
        width: win.compact ? 200 : 232
        anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.left: parent.left
        color: theme.bg
        Rectangle { width: 1; color: theme.border; anchors.right: parent.right; height: parent.height }
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 16; spacing: 4
            RowLayout {
                Layout.topMargin: 10; Layout.leftMargin: 6; Layout.bottomMargin: 28; spacing: 10
                Image { source: "../assets/icon.png"; sourceSize.width: 64; sourceSize.height: 64; Layout.preferredWidth: 32; Layout.preferredHeight: 32; smooth: true }
                FText { text: "FreeVideo"; font.pixelSize: theme.section; font.weight: Font.DemiBold; font.letterSpacing: -0.4 }
            }
            FNav { objectName: "navLauncher"; glyph: "play"; text: t("Launch", "启动"); Layout.fillWidth: true; selected: s.page === "launcher"; enabled: s.selected && !s.busy; onClicked: backend.action("launcher", false) }
            FNav { objectName: "navSetup"; glyph: "download"; text: t("Installation", "安装"); Layout.fillWidth: true; selected: s.page !== "launcher"; enabled: !s.busy && !s.portable; onClicked: backend.action("setup", false) }
            Item { Layout.fillHeight: true }
            Rectangle {
                Layout.fillWidth: true; Layout.bottomMargin: 8; implicitHeight: busyBody.implicitHeight + 28
                radius: theme.radiusMd; color: theme.surface; border.color: theme.border
                opacity: s.busy ? 1 : 0; visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
                ColumnLayout {
                    id: busyBody; x: 14; y: 14; width: parent.width - 28; spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        FText { text: t("Working", "正在处理"); font.pixelSize: theme.micro; font.weight: Font.DemiBold; Layout.fillWidth: true }
                        FText { text: s.elapsed; font.pixelSize: theme.micro; color: theme.muted }
                    }
                    FMeter { Layout.fillWidth: true; fraction: win.fraction(s.overall); active: true; subdued: true }
                    FText { text: s.overall.label || t("Preparing…", "正在准备…"); font.pixelSize: theme.micro; color: theme.muted; Layout.fillWidth: true; maximumLineCount: 1; elide: Text.ElideRight }
                }
            }
            FNav { glyph: "terminal"; text: t("Terminal", "终端"); Layout.fillWidth: true; selected: terminalOpen; onClicked: terminalOpen = !terminalOpen }
            FNav { objectName: "settingsButton"; glyph: "settings"; text: t("Settings", "设置"); Layout.fillWidth: true; onClicked: settingsOpen = true }
            FText { text: s.version; color: theme.disabled; font.pixelSize: 11; Layout.leftMargin: 12; Layout.topMargin: 12 }
        }
    }

    Rectangle {
        id: content
        anchors.left: sidebar.right; anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: terminalPanel.top
        color: theme.canvas
        Item {
            id: header; height: shortWindow ? 52 : 64; anchors.top: parent.top; width: parent.width
            // The setup guide sits over the centre of the page column; on a
            // narrow window it gives way to the controls on the right.
            Row {
                id: steps
                objectName: "setupSteps"
                visible: s.page !== "launcher"
                spacing: win.compact ? 8 : 12
                anchors.verticalCenter: parent.verticalCenter
                x: Math.max(20, Math.min((parent.width - width) / 2, headerTools.x - width - 16))
                Repeater {
                    model: [t("ComfyUI", "ComfyUI"), t("Models", "模型"), t("Install", "安装")]
                    delegate: Row {
                        required property string modelData; required property int index
                        spacing: win.compact ? 8 : 12
                        Item {
                            visible: index > 0; width: win.compact ? 14 : 32; height: 2; anchors.verticalCenter: parent.verticalCenter
                            Rectangle { anchors.verticalCenter: parent.verticalCenter; width: parent.width; height: 1; color: theme.border }
                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter; height: 1; color: theme.accentDim
                                width: index <= win.step ? parent.width : 0
                                Behavior on width { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }
                            }
                        }
                        Row {
                            spacing: 8; anchors.verticalCenter: parent.verticalCenter
                            Rectangle {
                                width: 20; height: 20; radius: 10; anchors.verticalCenter: parent.verticalCenter
                                color: index < win.step ? theme.accentSubtle : index === win.step ? theme.accent : "transparent"
                                border.width: index > win.step ? 1 : 0; border.color: theme.sheen
                                scale: index === win.step ? 1 : 0.9
                                Behavior on color { ColorAnimation { duration: 220 } }
                                Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutBack } }
                                FText { anchors.centerIn: parent; text: index < win.step ? "✓" : String(index + 1); font.pixelSize: 11; font.weight: Font.DemiBold
                                        color: index === win.step ? theme.bg : index < win.step ? theme.accent : theme.muted }
                            }
                            FText { anchors.verticalCenter: parent.verticalCenter; text: modelData; font.pixelSize: theme.micro + 1
                                    font.weight: index === win.step ? Font.DemiBold : Font.Normal; color: index === win.step ? theme.text : theme.muted }
                        }
                    }
                }
            }
            Row {
                id: headerTools
                anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter
                spacing: 12
                Rectangle {
                    visible: s.status === "open"; anchors.verticalCenter: parent.verticalCenter
                    height: 26; width: connected.implicitWidth + 30; radius: 13; color: theme.successSubtle
                    Rectangle {
                        id: liveDot; x: 11; anchors.verticalCenter: parent.verticalCenter; width: 7; height: 7; radius: 4; color: theme.success
                        SequentialAnimation on opacity {
                            running: liveDot.visible; loops: Animation.Infinite
                            NumberAnimation { to: 0.35; duration: 1400; easing.type: Easing.InOutSine }
                            NumberAnimation { to: 1; duration: 1400; easing.type: Easing.InOutSine }
                        }
                    }
                    FText { id: connected; x: 23; anchors.verticalCenter: parent.verticalCenter; text: t("ComfyUI connected", "ComfyUI 已连接"); font.pixelSize: theme.micro; color: theme.success }
                }
                // Both languages stay visible; the current one is marked.
                FSegmented {
                    objectName: "languageSwitch"; compact: true; anchors.verticalCenter: parent.verticalCenter
                    width: 104; current: s.zh ? "zh" : "en"
                    options: [{value: "zh", label: "中文"}, {value: "en", label: "EN"}]
                    onPicked: function(value) { if (value !== current) backend.edit("language", value) }
                    Accessible.name: t("Language", "语言")
                }
            }
        }
        ScrollView {
            id: scroll; objectName: "pageScroll"
            anchors.top: header.bottom; anchors.bottom: footer.top; width: parent.width
            clip: true; contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                id: page
                width: Math.min(scroll.availableWidth - (win.compact ? 36 : 64), 820)
                x: (scroll.availableWidth-width)/2
                spacing: shortWindow ? 14 : 20
                transform: Translate { id: pageShift }
                Item { height: shortWindow ? 0 : 16 }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 8; Layout.bottomMargin: shortWindow ? 0 : 10
                    FText { objectName: "pageHeading"; horizontalAlignment: Text.AlignHCenter; text: s.page === "launcher" ? t("Your workspace", "你的工作空间") : s.page === "comfy" ? t("Set up FreeVideo", "安装 FreeVideo") : s.page === "models" ? t("Set up your models", "准备模型") : s.status === "review" ? t("Ready to install", "确认安装") : s.busy ? t("Setting things up", "正在准备 FreeVideo") : t("Continue your setup", "继续安装"); font.pixelSize: win.compact ? 26 : theme.hero; font.weight: Font.DemiBold; font.letterSpacing: -0.6; Layout.fillWidth: true }
                }

                Rectangle {
                    id: failureCard
                    objectName: "failureCard"; visible: !!s.error; Layout.fillWidth: true
                    implicitHeight: failureBody.implicitHeight + 36; radius: theme.radiusMd
                    color: theme.dangerSubtle; border.color: theme.dangerLine
                    // A new problem settles in above the page, like other state cards.
                    transform: Translate { id: failureShift }
                    onVisibleChanged: if (visible) failureIn.restart()
                    ParallelAnimation {
                        id: failureIn
                        NumberAnimation { target: failureCard; property: "opacity"; from: 0; to: 1; duration: 220; easing.type: Easing.OutCubic }
                        NumberAnimation { target: failureShift; property: "y"; from: -6; to: 0; duration: 300; easing.type: Easing.OutCubic }
                    }
                    ColumnLayout {
                        id: failureBody; x: 18; y: 18; width: parent.width - 36; spacing: 10
                        FText { text: s.failure.title || t("Something went wrong", "出现问题"); color: theme.danger; font.weight: Font.DemiBold; Layout.fillWidth: true }
                        FText { visible: !!(s.failure.detail || s.failure.action); text: s.failure.detail || s.failure.action; color: theme.text; Layout.fillWidth: true }
                        Flow {
                            Layout.fillWidth: true; spacing: 8
                            FButton { visible: s.failure.kind === "download"; text: t("Change source", "切换下载源"); onClicked: { settingsTab = "downloads"; settingsOpen = true } }
                            FButton { objectName: "copyError"; text: t("Copy full details", "复制完整详情"); onClicked: backend.copy(s.error) }
                            FButton { objectName: "showError"; text: errorDetailsOpen ? t("Hide details", "收起详情") : t("Show details", "查看详情"); flat: true; onClicked: errorDetailsOpen = !errorDetailsOpen }
                            FButton { objectName: "exportError"; text: t("Export report", "导出报告"); enabled: s.report.status !== "running"; onClicked: backend.exportReport() }
                        }
                        ScrollView {
                            visible: errorDetailsOpen; Layout.fillWidth: true; Layout.preferredHeight: Math.min(160, errorText.implicitHeight+10); clip: true
                            TextArea { id: errorText; objectName: "failureDetails"; text: s.error; readOnly: true; selectByMouse: true; wrapMode: Text.Wrap; color: theme.muted; font.family: theme.mono; font.pixelSize: theme.micro; background: null; textFormat: TextEdit.PlainText }
                        }
                    }
                }

                FText {
                    visible: s.report.status !== "idle"; Layout.fillWidth: true; font.pixelSize: theme.micro
                    color: s.report.status === "error" ? theme.danger : theme.muted
                    text: s.report.status === "running" ? t("Exporting full logs…", "正在导出完整日志…") :
                        s.report.status === "error" ? t("Export failed: ", "导出失败：") + s.report.error :
                        t("Report saved locally: ", "报告已保存到本机：") + s.report.path +
                        (s.report.collection_errors ? t("\nSome diagnostics were unavailable; see manifest.json in the ZIP.", "\n部分诊断信息未能收集，原因记录在 ZIP 内的 manifest.json。") : "")
                }

                ColumnLayout {
                    objectName: "comfyPage"
                    visible: s.page === "comfy"; Layout.fillWidth: true; spacing: shortWindow ? 14 : 20
                    RowLayout {
                        Layout.fillWidth: true; spacing: 12
                        FChoice {
                            objectName: "newComfyMethod"; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 1; Layout.fillHeight: true
                            text: t("Install ComfyUI", "帮我安装 ComfyUI")
                            detail: t("Prepare ComfyUI and its environment.", "准备 ComfyUI 和运行环境。")
                            checked: s.form.new_comfy; enabled: !s.busy
                            onClicked: backend.edit("new_comfy", true)
                        }
                        FChoice {
                            objectName: "existingComfyMethod"; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 1; Layout.fillHeight: true
                            text: t("Use existing ComfyUI", "使用已有 ComfyUI")
                            detail: t("Connect your existing installation.", "选择已有安装目录。")
                            checked: !s.form.new_comfy; enabled: !s.busy
                            onClicked: backend.edit("new_comfy", false)
                        }
                    }
                    FCard {
                        Layout.fillWidth: true; padding: shortWindow ? 16 : 22; spacing: 10
                        RowLayout {
                            objectName: "environmentMethods"; visible: s.form.new_comfy; Layout.fillWidth: true; spacing: 12
                            FText { text: t("Install method", "安装方式"); font.weight: Font.DemiBold; Layout.fillWidth: true }
                            FSegmented {
                                objectName: "environmentMethod"; Layout.preferredWidth: Math.min(340, parent.width * .72)
                                current: s.form.environment_method; enabled: !s.busy
                                options: [{value: "auto", label: t("Automatic", "自动安装")}, {value: "manual", label: t("Third-party download", "第三方下载")}]
                                onPicked: function(value) { backend.edit("environment_method", value) }
                                Accessible.name: t("Install method", "安装方式")
                            }
                        }
                        FDivider { visible: s.form.new_comfy; Layout.fillWidth: true; Layout.topMargin: 4; Layout.bottomMargin: 4 }
                        FText { text: s.form.new_comfy ? t("Install location", "安装位置") : t("ComfyUI folder", "ComfyUI 目录"); font.weight: Font.DemiBold }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            FField { objectName: "installationPath"; Layout.fillWidth: true; text: s.form.new_comfy ? s.form.destination : s.form.comfy; placeholderText: t("Choose a folder", "选择文件夹"); enabled: !s.busy; onEditingFinished: backend.edit(s.form.new_comfy ? "destination" : "comfy", text) }
                            FButton { text: t("Browse…", "浏览…"); implicitHeight: theme.height + 4; onClicked: backend.browse(s.form.new_comfy ? "destination" : "comfy") }
                        }
                        FText { visible: !s.form.new_comfy; text: t("Uses your existing Python when available. A separate environment loads only FreeVideo.", "优先使用已有 Python；独立环境仅加载 FreeVideo。"); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                    }
                    FCard {
                        objectName: "runtimeImportCard"; visible: manualEnvironment; Layout.fillWidth: true; padding: shortWindow ? 16 : 20; spacing: 10
                        RowLayout {
                            Layout.fillWidth: true
                            FText { text: t("Import environment package", "导入运行环境包"); font.weight: Font.DemiBold; Layout.fillWidth: true }
                            FButton { objectName: "clearRuntime"; visible: usingRuntime; text: t("Use automatic download", "改用自动下载"); flat: true; enabled: !s.busy; onClicked: backend.clearRuntime() }
                        }
                        FText { objectName: "runtimeHelp"; Layout.fillWidth: true; color: theme.muted; font.pixelSize: theme.micro
                            text: usingRuntime ? t("Environment ready. Import the model packages in the next step.", "运行环境已就绪，下一步导入模型包。") : t("Download the Environment ZIP from Quark, then import it here. Model packages come next.", "从夸克下载「运行环境」ZIP，在这里导入。模型包放在下一步。") }
                        Rectangle {
                            Layout.fillWidth: true; implicitHeight: runtimeContents.implicitHeight + 24; radius: theme.radiusSm
                            color: runtimeDrop.containsDrag ? theme.accentSubtle : theme.bg; border.color: runtimeDrop.containsDrag ? theme.accent : theme.sheen
                            DropArea { id: runtimeDrop; objectName: "runtimeDrop"; anchors.fill: parent; enabled: !s.busy
                                onDropped: function(drop) { if (drop.hasUrls) { backend.importPackages(drop.urls); drop.acceptProposedAction() } }
                            }
                            ColumnLayout {
                                id: runtimeContents; anchors.centerIn: parent; width: parent.width - 24; spacing: 8
                                FText { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; color: usingRuntime ? theme.success : theme.muted; font.pixelSize: theme.micro
                                    text: usingRuntime ? t("✓ Environment imported", "✓ 运行环境已导入") : t("Drop the Environment ZIP here — no extraction needed", "将「运行环境」ZIP 拖到这里，无需解压") }
                                Flow {
                                    Layout.alignment: Qt.AlignHCenter; Layout.maximumWidth: parent.width; spacing: 8
                                    FButton { objectName: "importRuntime"; text: usingRuntime ? t("Replace package…", "更换环境包…") : t("Choose environment ZIP…", "选择运行环境包…"); enabled: !s.busy; implicitHeight: theme.heightSm; onClicked: backend.browseRuntimePackage() }
                                    Repeater {
                                        model: cloudLinks
                                        delegate: FButton { required property var modelData; required property int index; objectName: "runtimeShare-" + index; text: t("Quark download ↗", "夸克网盘下载 ↗"); flat: true; implicitHeight: theme.heightSm; onClicked: backend.link(modelData.url) }
                                    }
                                }
                            }
                        }
                        FMeter { visible: ["running", "preparing"].indexOf(s.offline.status) >= 0; Layout.fillWidth: true; fraction: win.fraction(s.offline); active: visible }
                        FText { visible: !!s.offline.detail; text: s.offline.detail + (number(s.offline.total) ? " · " + bytes(s.offline.done) + " / " + bytes(s.offline.total) : ""); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                        FText { visible: s.offline.models > 0; text: t("Model packages also saved for the next step: ", "同时导入的模型包将在下一步使用：") + s.offline.models; color: theme.success; font.pixelSize: theme.micro; Layout.fillWidth: true }
                    }
                }

                ColumnLayout {
                    objectName: "modelsPage"
                    visible: s.page === "models"; Layout.fillWidth: true; spacing: 16
                    FCard {
                        objectName: "modelLibraries"; visible: !usingRuntime; Layout.fillWidth: true; padding: 20; spacing: 10
                        RowLayout {
                            Layout.fillWidth: true; spacing: 12
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 4
                                FText { text: t("Reuse models · optional", "复用已有模型（可选）"); font.weight: Font.DemiBold; Layout.fillWidth: true }
                                FText { text: t("Add folders to find matching models in their subfolders.", "添加总目录，自动匹配子文件夹中的模型。"); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                            }
                            FButton { objectName: "addModelFolder"; text: t("Add folder", "添加目录"); enabled: !s.busy; onClicked: backend.browse("model_dirs") }
                        }
                        Repeater {
                            model: s.form.model_dirs
                            delegate: Rectangle {
                                required property string modelData; required property int index
                                Layout.fillWidth: true; implicitHeight: 42; radius: theme.radiusSm; color: theme.bg
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 4; spacing: 10
                                    FIcon { kind: "folder"; ink: theme.muted; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
                                    FText { text: modelData; font.pixelSize: theme.micro + 1; Layout.fillWidth: true; elide: Text.ElideMiddle; maximumLineCount: 1 }
                                    FButton { text: t("Remove", "移除"); flat: true; implicitHeight: theme.heightSm; font.pixelSize: theme.micro; enabled: !s.busy; onClicked: backend.removeFolder(index) }
                                }
                            }
                        }
                    }
                    RowLayout {
                        visible: usingRuntime; Layout.fillWidth: true
                        FText { objectName: "runtimeReadyOnModels"; text: t("✓ Environment ready · add your model packages", "✓ 运行环境已就绪 · 继续导入模型包"); color: theme.success; Layout.fillWidth: true }
                        FButton { text: t("Change", "更改"); flat: true; enabled: !s.busy; onClicked: backend.action("back", false) }
                    }
                    FText { visible: !usingRuntime; text: t("How would you like to get the rest?", "选择下载方式"); font.pixelSize: theme.strong; font.weight: Font.DemiBold; Layout.fillWidth: true; Layout.topMargin: 6 }
                    RowLayout {
                        visible: !usingRuntime; Layout.fillWidth: true; spacing: 12
                        FChoice {
                            objectName: "automaticMethod"; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 1; Layout.fillHeight: true
                            text: t("Automatic download", "自动下载")
                            detail: t("Download only what's missing.", "自动补齐缺少的文件。")
                            checked: !offlineSelected; enabled: !s.busy
                            onClicked: backend.edit("model_method", "auto")
                        }
                        FChoice {
                            objectName: "offlineMethod"; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 1; Layout.fillHeight: true
                            text: t("Offline packages", "离线包安装")
                            detail: t("Download from Quark or the web, then import.", "从夸克或网页下载后导入。")
                            checked: offlineSelected; enabled: !s.busy
                            onClicked: backend.edit("model_method", "manual")
                        }
                    }
                    FCard {
                        objectName: "automaticDownloadCard"; visible: !offlineSelected; reveal: true; Layout.fillWidth: true; padding: 20; spacing: 6
                        RowLayout {
                            Layout.fillWidth: true; spacing: 12
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 4
                                FText { text: t("Download source", "下载源"); font.weight: Font.DemiBold; Layout.fillWidth: true }
                            }
                            FButton { objectName: "downloadSource"; text: s.source_name + "  ›"; flat: true; implicitHeight: theme.heightSm; font.pixelSize: theme.micro + 1; onClicked: { settingsTab = "downloads"; settingsOpen = true } }
                        }
                    }
                    FCard {
                        objectName: "offlineImportCard"; visible: offlineSelected; reveal: true; Layout.fillWidth: true; padding: 20; spacing: 14
                        GridLayout {
                            Layout.fillWidth: true; columns: win.compact ? 1 : 2; columnSpacing: 24; rowSpacing: 18
                            ColumnLayout {
                                Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 10
                                FText { text: t("1. Download your packages", "1. 下载离线包"); font.weight: Font.DemiBold; Layout.fillWidth: true }
                                Repeater {
                                    model: cloudLinks
                                    delegate: FButton { required property var modelData; required property int index; objectName: "offlineShare-" + index; text: t("Download from Quark ↗", "打开夸克网盘 ↗"); onClicked: backend.link(modelData.url) }
                                }
                                FText { text: t("Get Common models and the pack for your GPU (RTX 30/40 or RTX 50).", "下载「公用模型」和对应显卡包（30/40 系或 50 系）。"); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                                FButton { objectName: "otherModelLinks"; text: t("Alternative download sources", "其他下载渠道") + (otherModelLinksOpen ? "  −" : "  +"); flat: true; implicitHeight: theme.heightSm - 2; leftPadding: 0; font.pixelSize: theme.micro + 1; onClicked: otherModelLinksOpen = !otherModelLinksOpen }
                                FText { visible: otherModelLinksOpen; text: t("The same models are also available from Hugging Face or ModelScope.", "同一套模型，也可从 Hugging Face 或魔搭下载。"); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                                Flow {
                                    visible: otherModelLinksOpen; Layout.fillWidth: true; spacing: 8
                                    Repeater {
                                        model: ["video", "encoder", "decoder"]
                                        delegate: FButton {
                                            required property string modelData
                                            text: modelData === "video" ? t("Video model ↗", "视频模型 ↗") : modelData === "encoder" ? t("Text encoder ↗", "文本编码器 ↗") : t("Video & audio decoders ↗", "音视频解码器 ↗")
                                            implicitHeight: theme.heightSm; font.pixelSize: theme.micro + 1; onClicked: { modelInfo = modelData; modelInfoOpen = true }
                                        }
                                    }
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 10
                                FText { text: t("2. Import when downloaded", "2. 下载完成后导入"); font.weight: Font.DemiBold; Layout.fillWidth: true }
                                Rectangle {
                                    Layout.fillWidth: true; implicitHeight: Math.max(130, dropContents.implicitHeight + 32); radius: theme.radiusMd
                                    color: packageDrop.containsDrag ? theme.accentSubtle : theme.bg
                                    border.color: packageDrop.containsDrag ? theme.accent : theme.sheen; border.width: packageDrop.containsDrag ? 2 : 1
                                    DropArea {
                                        id: packageDrop; objectName: "packageDrop"; anchors.fill: parent; enabled: !s.busy
                                        onDropped: function(drop) { if (drop.hasUrls) { backend.importPackages(drop.urls); drop.acceptProposedAction() } }
                                    }
                                    ColumnLayout {
                                        id: dropContents; anchors.centerIn: parent; width: parent.width - 28; spacing: 10
                                        FIcon { kind: "download"; ink: packageDrop.containsDrag ? theme.accent : theme.muted; Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                                        FText { text: t("Drop model ZIPs here — no extraction needed", "将模型 ZIP 拖到这里，无需解压"); Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; font.pixelSize: theme.micro; color: theme.muted }
                                        FButton { objectName: "importPackages"; text: t("Choose model packages…", "选择模型包…"); enabled: !s.busy; Layout.alignment: Qt.AlignHCenter; Layout.maximumWidth: parent.width; implicitHeight: theme.height; onClicked: backend.browsePackages() }
                                    }
                                }
                            }
                        }
                        FMeter { visible: ["running", "preparing"].indexOf(s.offline.status) >= 0; Layout.fillWidth: true; fraction: win.fraction(s.offline); active: visible }
                        FText { visible: !!s.offline.detail; text: s.offline.detail + (number(s.offline.total) ? " · " + bytes(s.offline.done) + " / " + bytes(s.offline.total) : ""); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                    }
                    FText { visible: s.offline.models > 0; text: "✓  " + t("Model packages: ", "已导入模型包：") + s.offline.models; color: theme.success; font.pixelSize: theme.micro; Layout.fillWidth: true }
                }

                FCard {
                    visible: s.page === "progress" && (s.busy || s.status === "review"); reveal: true
                    Layout.fillWidth: true; padding: 22; spacing: 14
                    RowLayout {
                        Layout.fillWidth: true; spacing: 16
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 4
                            FText { text: s.status === "review" ? t("Installation plan", "安装计划") : s.overall.label || s.progress.label || t("Checking your installation", "正在检查安装"); font.pixelSize: theme.strong + 1; font.weight: Font.DemiBold; Layout.fillWidth: true }
                            FText { visible: !!s.summary; text: s.summary; color: theme.muted; font.pixelSize: theme.micro + 1; Layout.fillWidth: true }
                        }
                        FText { visible: s.busy; opacity: overallMeter.known ? 1 : 0; Behavior on opacity { NumberAnimation { duration: 200 } } text: Math.round(overallMeter.shown*100)+"%"; font.features: { "tnum": 1 }; font.pixelSize: 28; font.weight: Font.DemiBold; font.letterSpacing: -0.5; Layout.alignment: Qt.AlignTop }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 10; visible: s.busy
                        FMeter { id: overallMeter; objectName: "overallProgress"; Layout.fillWidth: true; fraction: win.fraction(s.overall); active: s.busy }
                        RowLayout {
                            Layout.fillWidth: true
                            FText { text: t("Overall progress", "整体进度") + (number(s.overall.total) ? " · " + Math.floor(s.overall.done || 0) + " / " + s.overall.total : ""); font.pixelSize: theme.micro; color: theme.muted; Layout.fillWidth: true }
                            FText { visible: !!s.elapsed; text: t("Elapsed ", "已用时 ") + s.elapsed; font.pixelSize: theme.micro; color: theme.muted }
                        }
                        Rectangle { Layout.fillWidth: true; Layout.topMargin: 4; Layout.bottomMargin: 4; height: 1; color: theme.border }
                        FText { text: s.detail; visible: !!s.detail; Layout.fillWidth: true; font.pixelSize: theme.micro + 1 }
                        FMeter { Layout.fillWidth: true; fraction: win.fraction(s.progress); active: s.busy; subdued: true }
                        FText { visible: !!s.progress_text; text: s.progress_text; color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                    }
                    ColumnLayout {
                        visible: s.status === "review"; Layout.fillWidth: true; spacing: 12
                        GridLayout {
                            Layout.fillWidth: true; columns: 2; columnSpacing: 16; rowSpacing: 8
                            FText { text: "ComfyUI"; color: theme.muted; font.pixelSize: theme.micro + 1 }
                            FText { text: s.review.comfy; font.pixelSize: theme.micro + 1; Layout.fillWidth: true; elide: Text.ElideMiddle; maximumLineCount: 1 }
                            FText { text: "FreeVideo"; color: theme.muted; font.pixelSize: theme.micro + 1 }
                            FText { text: s.review.engine; font.pixelSize: theme.micro + 1; Layout.fillWidth: true; elide: Text.ElideMiddle; maximumLineCount: 1 }
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: theme.border }
                        FCheck { objectName: "installConsent"; text: s.consent; checked: accepted; onToggled: accepted = checked; Layout.fillWidth: true }
                        FButton { text: t("Read licenses ↗", "查看许可证 ↗"); flat: true; implicitHeight: theme.heightSm - 2; leftPadding: 32; font.pixelSize: theme.micro + 1; onClicked: backend.link("https://huggingface.co/OpenVDN/vdn-minimax-h3-edge/blob/main/LICENSE") }
                    }
                }

                FCard {
                    Layout.fillWidth: true; visible: s.page === "progress"
                    padding: 20; spacing: 0
                    Repeater {
                        // Keep the rows alive when counters or elapsed time change.
                        // Replacing a JS-array model recreates every meter and
                        // restarts its fill animation on each status refresh.
                        model: s.models.length
                        delegate: ColumnLayout {
                            required property int index
                            property var modelData: win.s.models[index]
                            Layout.fillWidth: true; spacing: 10
                            Rectangle { visible: index > 0; Layout.fillWidth: true; height: 1; color: theme.border; Layout.topMargin: 14; Layout.bottomMargin: 12 }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 14
                                Rectangle {
                                    implicitWidth: 38; implicitHeight: 38; radius: theme.radiusSm
                                    color: modelData.state === "ready" ? theme.successSubtle : theme.raised
                                    FIcon { anchors.centerIn: parent; width: 20; height: 20; ink: modelData.state === "ready" ? theme.success : theme.accent; kind: modelData.id === "text" ? "text" : modelData.id === "decoder" ? "decoder" : "video" }
                                }
                                ColumnLayout {
                                    spacing: 3; Layout.fillWidth: true
                                    FText { text: modelData.title; font.weight: Font.DemiBold; Layout.fillWidth: true }
                                    FText { text: modelData.detail + (modelData.rate ? "  ·  " + modelData.rate : ""); font.pixelSize: theme.micro; Layout.fillWidth: true; color: modelData.state === "ready" ? theme.success : theme.muted }
                                }
                                FText { visible: modelData.total > 0; text: modelData.state === "ready" ? bytes(modelData.total) : bytes(modelData.done) + " / " + bytes(modelData.total); font.pixelSize: theme.micro; color: theme.muted; font.features: { "tnum": 1 } }
                                FButton { text: t("Download links ↗", "下载地址 ↗"); implicitHeight: theme.heightSm; flat: true; font.pixelSize: theme.micro + 1; visible: s.form.model_method === "manual"; onClicked: { modelInfo = modelData.id === "text" ? "encoder" : modelData.id; modelInfoOpen = true } }
                            }
                            FMeter { objectName: "modelProgress-" + modelData.id; visible: modelData.total > 0 && modelData.state !== "ready"; Layout.fillWidth: true; Layout.leftMargin: 52; fraction: modelData.done / Math.max(1,modelData.total); subdued: true }
                        }
                    }
                }

                ColumnLayout {
                    objectName: "launcherPage"
                    visible: s.page === "launcher"; Layout.fillWidth: true; spacing: 16
                    FCard {
                        objectName: "updateBanner"
                        visible: updateOffered || (!!s.update.phase && s.update.phase !== "review")
                        reveal: true; Layout.fillWidth: true; padding: 18; spacing: 10
                        color: theme.accentSubtle; border.color: theme.accentDim
                        RowLayout {
                            Layout.fillWidth: true; spacing: 14
                            FIcon { kind: "download"; ink: theme.accent; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                FText { objectName: "updateHeadline"; text: updateHeadline(); font.weight: Font.DemiBold; Layout.fillWidth: true }
                                FText { visible: text !== ""; text: updateExplanation(); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                            }
                            FButton { objectName: "cancelUpdateButton"; visible: s.update.phase === "waiting"; flat: true; text: t("Cancel update", "取消更新"); onClicked: { manualUpdate = false; backend.dismissUpdate() } }
                            FButton {
                                objectName: "engineUpdateButton"; primary: true; visible: !s.update.phase; enabled: !s.busy
                                text: s.update.status === "ready" && s.update.candidate ? t("Restart & update", "重启并更新") : t("Update now", "立即更新")
                                onClicked: { manualUpdate = !!s.update.candidate; backend.update("") }
                            }
                        }
                        FMeter { visible: s.update.phase === "downloading"; Layout.fillWidth: true; fraction: s.update.progress && s.update.progress.total ? s.update.progress.done / s.update.progress.total : -1; active: visible }
                    }
                    FCard {
                        Layout.fillWidth: true; padding: 26; spacing: 18
                        RowLayout {
                            spacing: 20; Layout.fillWidth: true
                            Item {
                                Layout.preferredWidth: 64; Layout.preferredHeight: 64
                                Image { anchors.fill: parent; source: "../assets/icon.png"; sourceSize.width: 128; sourceSize.height: 128 }
                                // Ready: a check settles onto the icon.
                                Rectangle {
                                    width: 24; height: 24; radius: 12; color: theme.success
                                    border.width: 3; border.color: theme.surface
                                    x: parent.width - width + 4; y: parent.height - height + 4
                                    scale: s.status === "open" ? 1 : 0; visible: scale > 0
                                    Behavior on scale { NumberAnimation { duration: 380; easing.type: Easing.OutBack } }
                                    FText { anchors.centerIn: parent; text: "✓"; color: theme.bg; font.pixelSize: 12; font.weight: Font.Bold }
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 6
                                FText { text: s.status === "open" ? t("FreeVideo is ready", "FreeVideo 已就绪") : s.busy ? t("Starting ComfyUI", "正在启动 ComfyUI") : s.status === "restart-required" ? t("Restart ComfyUI", "请重启 ComfyUI") : s.status === "failed" ? t("Startup interrupted", "启动未完成") : t("Ready to launch", "准备就绪"); font.pixelSize: theme.section + 2; font.weight: Font.DemiBold; Layout.fillWidth: true }
                                FText { text: s.status === "open" ? s.url : s.status === "restart-required" ? t("Restart your running ComfyUI to load the updated nodes, then connect again.", "重启正在运行的 ComfyUI 以载入更新后的节点，再点击重新连接。") : t("Your models and environment are connected.", "已连接你的模型与运行环境。"); color: theme.muted; Layout.fillWidth: true }
                            }
                        }
                        FMeter { visible: s.busy; Layout.fillWidth: true; fraction: win.fraction(s.overall); active: s.busy }
                        RowLayout {
                            visible: s.status === "open"; Layout.fillWidth: true; spacing: 8
                            FButton { text: t("Copy address", "复制地址"); onClicked: backend.copy(s.url) }
                            FButton { text: t("Show terminal", "查看终端"); flat: true; onClicked: terminalOpen = true }
                        }
                    }
                    FCard {
                        Layout.fillWidth: true; padding: 22; spacing: 12
                        FText { text: t("Installation", "安装信息"); font.weight: Font.DemiBold }
                        GridLayout {
                            Layout.fillWidth: true; columns: 2; columnSpacing: 16; rowSpacing: 8
                            FText { text: "ComfyUI"; color: theme.muted; font.pixelSize: theme.micro + 1 }
                            FText { text: s.form.comfy; font.pixelSize: theme.micro + 1; Layout.fillWidth: true; elide: Text.ElideMiddle; maximumLineCount: 1 }
                            FText { text: "FreeVideo"; color: theme.muted; font.pixelSize: theme.micro + 1 }
                            FText { text: s.form.engine; font.pixelSize: theme.micro + 1; Layout.fillWidth: true; elide: Text.ElideMiddle; maximumLineCount: 1 }
                        }
                        RowLayout {
                            spacing: 12; Layout.topMargin: 4
                            FButton { text: t("Create desktop shortcut", "创建桌面快捷方式"); implicitHeight: theme.height; font.pixelSize: theme.micro + 1; enabled: s.can_shortcut && !s.busy; onClicked: backend.action("shortcut", false) }
                            FText { text: ["created","present"].indexOf(s.shortcut.status) >= 0 ? "✓  " + t("Shortcut ready", "快捷方式已就绪") : ""; color: theme.success; font.pixelSize: theme.micro }
                        }
                        FCheck { visible: s.needs_consent; text: s.consent; checked: accepted; onToggled: accepted = checked; Layout.fillWidth: true }
                        FButton { visible: s.needs_consent; text: t("Read licenses ↗", "查看许可证 ↗"); flat: true; implicitHeight: theme.heightSm - 2; leftPadding: 32; font.pixelSize: theme.micro + 1; onClicked: backend.link("https://huggingface.co/OpenVDN/vdn-minimax-h3-edge/blob/main/LICENSE") }
                    }
                }

                Item { height: 18 }
            }
        }
        Rectangle {
            id: footer; height: shortWindow ? 64 : 76; anchors.bottom: parent.bottom; width: parent.width; color: theme.canvas
            Rectangle { height: 1; width: parent.width; color: theme.border }
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 24; anchors.rightMargin: 24; spacing: 10
                FButton { objectName: "backButton"; text: t("Back", "上一步"); flat: true; visible: s.page === "models" || s.page === "progress"; enabled: !s.busy; onClicked: backend.action("back", false) }
                Item { Layout.fillWidth: true }
                FButton { visible: s.busy; text: t("Pause", "暂停"); onClicked: backend.action("stop", false) }
                FButton { objectName: "launchInstalledButton"; visible: updateFirst; flat: true; text: t("Launch current version", "启动当前版本"); onClicked: backend.action("primary", accepted) }
                FButton {
                    objectName: "primaryButton"; primary: true; implicitWidth: Math.max(160, contentItem.implicitWidth+40); implicitHeight: theme.heightLg
                    text: s.busy ? t("Working…", "正在处理…") : primaryText()
                    enabled: !s.busy && !(s.page === "launcher" && s.needs_consent && !accepted)
                             && !(s.page === "progress" && s.status === "review" && (!accepted || !!s.error))
                    onClicked: { if (needsRuntime) { backend.browseRuntimePackage(); return } if (needsPackages) { backend.browsePackages(); return } if (updateFirst) { backend.update(""); return } backend.action(s.page === "launcher" && s.status === "open" ? "browser" : "primary", accepted); if (s.busy && s.page === "launcher") terminalOpen = true }
                }
            }
        }
    }

    Rectangle {
        id: terminalPanel; visible: height > 0; color: theme.bg; clip: true
        anchors.bottom: parent.bottom; anchors.left: sidebar.right; anchors.right: parent.right
        height: terminalOpen ? Math.min(300, win.height * 0.36) : 0
        Behavior on height { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
        Rectangle { height: 1; width: parent.width; color: theme.border }
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 14; anchors.topMargin: 10; spacing: 8
            RowLayout {
                Layout.fillWidth: true; spacing: 6
                FIcon { kind: "terminal"; ink: theme.muted; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
                FText { text: t("Terminal", "终端"); font.pixelSize: theme.micro; font.weight: Font.DemiBold; color: theme.muted; Layout.rightMargin: 8 }
                ComboBox {
                    visible: s.logs.length > 1; model: s.logs; textRole: "label"; Layout.fillWidth: true; Layout.maximumWidth: 260; implicitHeight: 30
                    font.pixelSize: theme.micro
                    palette.button: theme.raised; palette.buttonText: theme.text; palette.window: theme.surface; palette.windowText: theme.text
                    palette.base: theme.surface; palette.text: theme.text; palette.highlight: theme.accentSubtle; palette.highlightedText: theme.text; palette.dark: theme.muted; palette.mid: theme.border; palette.light: theme.raised
                    onActivated: backend.terminal(s.logs[currentIndex].path)
                }
                Item { Layout.fillWidth: true }
                FButton { text: "×"; Accessible.name: t("Close terminal", "收起终端"); flat: true; implicitWidth: 30; implicitHeight: 28; leftPadding: 4; rightPadding: 4; onClicked: terminalOpen = false }
            }
            Flow {
                Layout.fillWidth: true; spacing: 6
                FButton { text: t("Copy full log", "复制完整日志"); flat: true; implicitHeight: 28; leftPadding: 8; rightPadding: 8; font.pixelSize: theme.micro; onClicked: backend.copyLog() }
                FButton { text: t("Export report", "导出报告"); flat: true; implicitHeight: 28; leftPadding: 8; rightPadding: 8; font.pixelSize: theme.micro; enabled: s.report.status !== "running"; onClicked: backend.exportReport() }
                FButton { text: t("Clear", "清空"); flat: true; implicitHeight: 28; implicitWidth: 64; leftPadding: 8; rightPadding: 8; font.pixelSize: theme.micro; onClicked: backend.clearTerminal() }
            }
            FText { Layout.fillWidth: true; font.pixelSize: theme.micro; color: theme.muted; text: t("Recent output is shown here. Copy or export to get the full redacted log.", "这里显示最近的输出；复制或导出可获取完整脱敏日志。") }
            ScrollView {
                id: terminalScroll; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                TextArea {
                    id: terminalText; objectName: "terminalText"; text: s.log || t("Process output will appear here.", "进程启动后，输出会显示在这里。"); textFormat: TextEdit.PlainText
                    readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; color: s.log ? theme.text : theme.disabled; font.family: theme.mono; font.pixelSize: 12; background: null
                    selectionColor: theme.accentDim
                    onTextChanged: if (!activeFocus) cursorPosition = length
                }
            }
        }
    }

    FPopup {
        id: settings; objectName: "settingsDialog"
        visible: settingsOpen; onClosed: settingsOpen = false
        width: Math.min(700, win.width-50); height: Math.min(700, win.height-50)
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        ColumnLayout {
            anchors.fill: parent; spacing: 18
            RowLayout { Layout.fillWidth: true; FText { text: t("Settings", "设置"); font.pixelSize: theme.section + 2; font.weight: Font.DemiBold; Layout.fillWidth: true } FButton { text: "×"; Accessible.name: t("Close settings", "关闭设置"); implicitWidth: 36; implicitHeight: theme.height; leftPadding: 4; rightPadding: 4; flat: true; onClicked: settingsOpen = false } }
            FSegmented {
                Layout.fillWidth: true; current: settingsTab; onPicked: function(value) { settingsTab = value }
                options: [{value: "downloads", label: t("Downloads", "下载")}, {value: "general", label: t("Preferences", "偏好")}, {value: "advanced", label: t("Environment", "环境")}]
            }
            ScrollView {
                id: settingsScroll
                Layout.fillWidth: true; Layout.fillHeight: true; contentWidth: availableWidth; clip: true
                ColumnLayout {
                    width: settingsScroll.availableWidth; spacing: 18
                    ColumnLayout {
                        Layout.fillWidth: true; visible: settingsTab === "downloads"; spacing: 20
                        FGroup {
                            Layout.fillWidth: true; title: t("Connection", "连接模式")
                            FSegmented {
                                objectName: "proxyMode"; Layout.fillWidth: true; current: s.proxy_mode
                                onPicked: function(value) { backend.selectProxyMode(value) }
                                options: [{value: "auto", label: t("Auto (recommended)", "自动（推荐）")}, {value: "proxy", label: t("Proxy only", "仅代理")}, {value: "direct", label: t("Direct only", "仅直连")}]
                            }
                            FText {
                                text: s.proxy_mode === "proxy" ? t("Use the current proxy. No direct fallback.", "沿用当前代理，不尝试直连。") : s.proxy_mode === "direct" ? t("Download directly, ignoring proxies.", "忽略代理，直接下载。") : t("Compare proxy and direct connections automatically.", "自动测速，择优使用代理或直连。")
                                color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true
                            }
                        }
                        FGroup {
                            Layout.fillWidth: true; title: t("Download source", "下载源")
                            FSegmented {
                                Layout.fillWidth: true; current: s.source; onPicked: function(value) { backend.selectSource(value) }
                                options: [{value: "auto", label: t("Automatic", "自动选择")}, {value: "official", label: "Hugging Face"}, {value: "hf-mirror", label: t("HF Mirror", "HF 镜像")}, {value: "modelscope", label: t("ModelScope", "魔搭")}]
                            }
                            FText { text: t("Switching also moves the current download. Verified partial data is retained wherever resuming is supported.", "切换会应用于当前下载；支持续传时，继续使用已校验的片段。"); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                            FDivider {}
                            RowLayout {
                                Layout.fillWidth: true; spacing: 12
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    FText { text: t("Connection speed", "连接速度"); Layout.fillWidth: true }
                                    FText { text: s.probe.status === "running" ? t("Testing sources: ", "正在测速：") + (s.probe.progress?.done || 0) + "/" + (s.probe.progress?.total || "…") : t("Compare sources on this computer.", "比较本机下载源速度。"); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                                }
                                FButton { text: s.probe.status === "running" ? t("Testing…", "测速中…") : t("Test speed", "测速"); implicitHeight: theme.heightSm; font.pixelSize: theme.micro + 1; enabled: s.probe.status !== "running"; onClicked: backend.speedTest() }
                            }
                            FText { visible: !!s.probe.error; text: s.probe.error || ""; font.pixelSize: theme.micro; color: theme.danger; Layout.fillWidth: true }
                            FText { visible: !!s.probe.network_unavailable; text: t("No sources reachable. Check your network or connection mode.", "下载源均无法连接，请检查网络或切换连接模式。"); font.pixelSize: theme.micro; color: theme.danger; Layout.fillWidth: true }
                            Repeater {
                                model: s.speeds
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true; Layout.preferredHeight: 26; spacing: 12
                                    FText { text: modelData.group; color: theme.muted; font.pixelSize: theme.micro; Layout.preferredWidth: 96 }
                                    FText { text: modelData.source; font.pixelSize: theme.micro; Layout.fillWidth: true; elide: Text.ElideRight; maximumLineCount: 1 }
                                    FText { text: modelData.ok ? modelData.rate : t("Unavailable", "不可用"); color: modelData.ok ? theme.success : theme.disabled; font.pixelSize: theme.micro; font.weight: Font.Medium }
                                }
                            }
                        }
                        FGroup {
                            Layout.fillWidth: true; title: t("Hugging Face token · optional", "Hugging Face 令牌 · 可选")
                            FField { Layout.fillWidth: true; echoMode: TextInput.Password; placeholderText: s.token_set ? t("Token set for this session", "本次已设置令牌") : "hf_…"; enabled: !s.busy; onEditingFinished: backend.edit("token", text) }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 12
                                FText { text: t("Use your account's download quota. Kept for this session only.", "使用账号下载额度，仅本次打开有效。"); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                                FButton { text: t("Get a token ↗", "获取令牌 ↗"); flat: true; implicitHeight: theme.heightSm - 2; font.pixelSize: theme.micro + 1; onClicked: backend.link("https://huggingface.co/settings/tokens") }
                            }
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; visible: settingsTab === "general"; spacing: 20
                        FGroup {
                            Layout.fillWidth: true; title: t("General", "通用")
                            RowLayout {
                                Layout.fillWidth: true; spacing: 12
                                FText { text: t("Language", "语言"); Layout.fillWidth: true }
                                FSegmented {
                                    Layout.preferredWidth: 200; current: s.zh ? "zh" : "en"; onPicked: function(value) { if (value !== current) backend.edit("language", value) }
                                    options: [{value: "zh", label: "简体中文"}, {value: "en", label: "English"}]
                                }
                            }
                            FDivider {}
                            RowLayout {
                                Layout.fillWidth: true; spacing: 12
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    FText { text: t("Updates", "更新"); Layout.fillWidth: true }
                                    FText { text: t("Settings are kept in Documents across updates.", "设置保存在文档目录中，更新后保留。"); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                                }
                                FButton { objectName: "checkUpdatesButton"; text: t("Check now", "检查更新"); implicitHeight: theme.heightSm; font.pixelSize: theme.micro + 1; onClicked: { manualUpdate = true; backend.checkUpdates("") } }
                            }
                        }
                        FGroup {
                            Layout.fillWidth: true; title: t("Compatibility", "兼容性")
                            RowLayout {
                                Layout.fillWidth: true
                                FText { text: t("Level", "档位"); Layout.fillWidth: true }
                                FText { text: s.compatibility.available ? String(s.compatibility.level || 0) + " / 3" : "—"; color: s.compatibility.available ? theme.accent : theme.disabled; font.weight: Font.DemiBold; font.features: { "tnum": 1 } }
                            }
                            FSlider { Layout.fillWidth: true; from: 0; to: 3; stepSize: 1; value: s.compatibility.level || 0; enabled: s.compatibility.available && !s.busy; snapMode: Slider.SnapAlways; onMoved: backend.compatibility(Math.round(value), s.compatibility.automatic) }
                            FText { text: s.compatibility.available ? t("Higher levels lower memory peaks and may generate more slowly.", "档位越高，瞬时负载越低，生成可能变慢。") : t("Available after hardware setup.", "完成硬件检查后可调整。"); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                            FDivider {}
                            FSwitch { Layout.fillWidth: true; text: t("Adjust automatically", "自动调整"); detail: t("Raise the level after an unexpected interruption.", "异常中断后自动提高档位。"); checked: !!s.compatibility.automatic; enabled: s.compatibility.available && !s.busy; onToggled: backend.compatibility(s.compatibility.level, checked) }
                        }
                        FButton { text: t("Qt notices & licenses ↗", "Qt 组件与许可证 ↗"); flat: true; implicitHeight: theme.heightSm; leftPadding: 4; font.pixelSize: theme.micro; onClicked: backend.link("https://doc.qt.io/qt-6/licenses-used-in-qt.html") }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; visible: settingsTab === "advanced"; spacing: 20; enabled: !s.busy && !s.portable
                        FGroup {
                            Layout.fillWidth: true; title: t("ComfyUI address", "ComfyUI 地址")
                            FField { Layout.fillWidth: true; text: s.form.url; onEditingFinished: backend.edit("url", text) }
                        }
                        FGroup {
                            Layout.fillWidth: true; title: t("Folders · optional", "目录 · 可选")
                            FText { text: t("Engine folder", "引擎目录"); font.pixelSize: theme.micro; color: theme.muted }
                            RowLayout { Layout.fillWidth: true; spacing: 8; FField { Layout.fillWidth: true; text: s.form.engine; placeholderText: t("Automatic", "自动选择"); onEditingFinished: backend.edit("engine", text) } FButton { text: t("Browse…", "浏览…"); implicitHeight: theme.height + 4; onClicked: backend.browse("engine") } }
                            FText { text: t("ComfyUI Python", "ComfyUI Python"); font.pixelSize: theme.micro; color: theme.muted; Layout.topMargin: 4 }
                            FField { Layout.fillWidth: true; text: s.form.python; placeholderText: t("Path to python.exe", "python.exe 的路径"); onEditingFinished: backend.edit("python", text) }
                        }
                        FGroup {
                            Layout.fillWidth: true; title: t("Maintenance", "维护")
                            FSwitch { Layout.fillWidth: true; text: t("Create a separate ComfyUI environment", "创建独立的 ComfyUI 环境"); checked: s.form.separate; onToggled: backend.edit("separate", checked) }
                            FDivider {}
                            FSwitch { Layout.fillWidth: true; text: t("Repair the engine installation", "修复引擎安装"); checked: s.form.repair; onToggled: backend.edit("repair", checked) }
                        }
                    }
                    Rectangle {
                        visible: !!s.error; Layout.fillWidth: true; implicitHeight: settingsError.implicitHeight + 28; radius: theme.radiusMd; color: theme.dangerSubtle; border.color: theme.dangerLine
                        ColumnLayout {
                            id: settingsError; x: 14; y: 14; width: parent.width - 28; spacing: 8
                            FText { text: s.failure.title + "\n" + (s.failure.action || s.failure.detail); color: theme.danger; font.pixelSize: theme.micro + 1; Layout.fillWidth: true }
                            Flow {
                                Layout.fillWidth: true; spacing: 8
                                FButton { text: t("Copy full details", "复制完整详情"); implicitHeight: theme.heightSm; font.pixelSize: theme.micro + 1; onClicked: backend.copy(s.error) }
                                FButton { text: t("Export report", "导出报告"); implicitHeight: theme.heightSm; font.pixelSize: theme.micro + 1; enabled: s.report.status !== "running"; onClicked: backend.exportReport() }
                            }
                            FText {
                                visible: s.report.status !== "idle"; Layout.fillWidth: true; font.pixelSize: theme.micro; color: theme.muted
                                text: s.report.status === "running" ? t("Exporting full logs…", "正在导出完整日志…") :
                                    s.report.status === "error" ? s.report.error : t("Saved: ", "已保存：") + s.report.path
                            }
                        }
                    }
                }
            }
        }
    }

    FPopup {
        id: modelDialog; objectName: "modelDialog"
        visible: modelInfoOpen; onClosed: modelInfoOpen = false
        width: Math.min(540, win.width-50)
        height: Math.min(modelContents.implicitHeight + padding*2, win.height-48)
        contentItem: ScrollView {
            id: modelScroll; clip: true; contentWidth: availableWidth
            ColumnLayout {
            id: modelContents; width: modelScroll.availableWidth
            spacing: 12
            FText { text: t("Download models", "下载模型"); font.pixelSize: theme.section + 2; font.weight: Font.DemiBold }
            FText { text: modelInfo === "video" ? t("Choose your GPU variant on the model page and keep its folder structure.", "按模型页说明选择显卡版本，保留下载的目录结构。") : modelInfo === "decoder" ? t("Download the vae and audio_vae folders.", "下载 vae 和 audio_vae 文件夹。") : t("Download the text encoder to your model folder.", "下载文本编码器，放入模型目录。"); color: theme.muted; Layout.fillWidth: true; Layout.bottomMargin: 4 }
            Repeater { model: modelLinks[modelInfo]; delegate: FButton { required property var modelData; required property int index; objectName: "modelLink-" + index; text: t(modelData.label, modelData.label_zh) + " ↗"; Layout.fillWidth: true; onClicked: backend.link(modelData.url) } }
            Repeater { model: cloudLinks; delegate: FButton { required property var modelData; text: t("Quark · ", "夸克 · ") + modelData.label + " ↗"; Layout.fillWidth: true; onClicked: backend.link(modelData.url) } }
            FText { text: t("When your download finishes, import a FreeVideo ZIP or choose the folder containing your models.", "下载完成后，导入 FreeVideo ZIP 或选择存放模型的文件夹。"); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true; Layout.topMargin: 6 }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                FButton { text: t("Close", "关闭"); flat: true; onClicked: modelInfoOpen = false }
                Item { Layout.fillWidth: true }
                FButton { text: t("Choose model folder", "选择模型目录"); onClicked: { modelInfoOpen = false; backend.browse("model_dirs") } }
                FButton { text: t("Import ZIPs", "导入 ZIP"); primary: true; onClicked: { modelInfoOpen = false; backend.browsePackages() } }
            }
            }
        }
    }

    FPopup {
        id: updateDialog; visible: !s.busy && (s.update.remind || manualUpdate)
        objectName: "updateDialog"
        width: Math.min(540,win.width-40); closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onClosed: { manualUpdate = false; backend.dismissUpdate() }
        height: Math.min(updateContents.implicitHeight + padding*2, win.height-48)
        contentItem: ScrollView {
            id: updateScroll; clip: true; contentWidth: availableWidth
            ColumnLayout {
            id: updateContents; width: updateScroll.availableWidth
            spacing: 14
            FText { text: s.update.candidate ? t("Update available", "有可用更新") : s.update.engine ? t("Engine update", "引擎更新") : t("Updates", "更新"); font.pixelSize: theme.section + 2; font.weight: Font.DemiBold }
            FText { objectName: "updateDialogText"; text: s.update.phase === "waiting" ? t("A video is still generating. FreeVideo restarts and updates as soon as it finishes.", "还有视频正在生成，完成后会自动重启并更新。") : s.update.status === "ready" && s.update.candidate ? t("Downloaded. Restart to finish; models and settings are kept.", "下载完成，重启即可完成更新，模型和设置都会保留。") : s.update.candidate ? t("FreeVideo ", "FreeVideo ") + s.update.candidate.version + t(" is available (current ", " 已发布（当前 ") + s.update.current + t("). Updating keeps your models and settings. FreeVideo restarts after the download; running videos finish first.", "）。更新会保留模型和设置，下载完成后自动重启；正在生成的视频会先完成。") : s.update.engine ? t("This launcher already includes engine ", "启动器已带有新版引擎 ") + s.update.current + t(" (installed ", "（已安装 ") + (s.update.installed || "—") + (s.status === "open" ? t("). Updating takes about a minute, restarts ComfyUI once and keeps your models and settings.", "）。更新约需 1 分钟，会重启一次 ComfyUI，模型和设置都会保留。") : t("). Updating takes about a minute, then FreeVideo starts; models and settings are kept.", "）。更新约需 1 分钟，完成后自动启动，模型和设置都会保留。")) : s.update.status === "current" ? t("You're up to date.", "已是最新版本。") : s.update.status === "development" ? t("Running from source. Update with Git.", "当前从源码运行，请通过 Git 更新。") : s.update.status === "error" ? t("Couldn't check for updates. Try again below.", "暂时无法检查更新，请重试。") : t("Checking the latest release…", "正在检查最新版本…"); color: theme.muted; Layout.fillWidth: true }
            FMeter { Layout.fillWidth: true; visible: s.update.status === "downloading"; active: true; fraction: s.update.progress && s.update.progress.total ? s.update.progress.done/s.update.progress.total : -1 }
            FText { visible: !!s.update.error; text: s.update.error || ""; color: theme.danger; Layout.fillWidth: true; font.pixelSize: theme.micro }
            FField { id: githubToken; visible: !!s.update.error; Layout.fillWidth: true; echoMode: TextInput.Password; placeholderText: t("GitHub token · optional", "GitHub Token · 可选") }
            RowLayout {
                Layout.fillWidth: true; Layout.topMargin: 8
                FButton { objectName: "updateLaterButton"; text: s.update.candidate || s.update.engine ? t("Later", "稍后更新") : t("Close", "关闭"); flat: true; onClicked: { manualUpdate = false; backend.dismissUpdate() } }
                Item { Layout.fillWidth: true }
                FButton { objectName: "updateNowButton"; text: s.update.status === "ready" && s.update.candidate ? t("Restart & update", "重启并更新") : s.update.candidate || s.update.engine ? t("Update now", "立即更新") : t("Check again", "重新检查"); primary: true; enabled: ["checking","downloading"].indexOf(s.update.status) < 0 && s.update.phase !== "waiting"; onClicked: { manualUpdate = !!s.update.candidate || !s.update.engine; backend.update(githubToken.text) } }
            }
            }
        }
    }

    FDialog {
        visible: closePending
        title: t("Pause and close?", "暂停并退出？")
        text: t("The current task will stop. Downloaded files and your setup are retained for next time.", "会停止当前任务，已下载文件和安装进度会保留，下次可以继续。")
        acceptText: t("Pause and close", "暂停并退出"); rejectText: t("Keep working", "继续处理"); destructive: true
        onAccepted: backend.close()
        onRejected: closePending = false
    }
    FDialog {
        visible: !!s.notice
        title: t("Compatibility settings", "兼容性设置")
        text: s.notice || ""
        acceptText: t("OK", "知道了")
        onAccepted: backend.dismissNotice()
    }
}
