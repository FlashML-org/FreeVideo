import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ApplicationWindow {
    id: win
    title: unifiedChrome ? "" : "FreeVideo"
    readonly property bool unifiedChrome: Qt.platform.os === "osx"
    color: unifiedChrome ? theme.canvas : theme.bg
    width: 1280; height: 860
    property var s: initialState
    Connections { target: backend; function onChanged() { win.s = backend.state } }
    property bool settingsOpen: false
    property bool terminalOpen: false
    property bool confirmArchives: false  // the in-place "delete the imported ZIPs?" step
    property string settingsTab: "downloads"
    property string modelInfo: "video"
    property bool modelInfoOpen: false
    property bool closePending: false
    property bool cleanupConfirm: false
    property bool releaseConfirm: false
    property bool accepted: false
    property bool manualUpdate: false
    property bool releaseNotesOpen: false
    property string previousStatus: ""
    property string previousReview: ""
    property bool compact: width < 1000
    property bool shortWindow: height < 700
    property bool otherModelLinksOpen: false
    readonly property bool manualEnvironment: s.offline.runtime_supported !== false && s.form.new_comfy && s.form.environment_method === "manual"
    readonly property bool usingRuntime: manualEnvironment && s.offline.runtime
    readonly property bool needsRuntime: s.page === "comfy" && manualEnvironment && !s.offline.runtime
    // An imported environment can take its models from packages or download them.
    readonly property bool offlineSelected: s.form.model_method === "manual"
    readonly property bool needsPackages: s.page === "models" && offlineSelected && s.offline.models === 0 && (usingRuntime || s.form.model_dirs.length === 0)
    readonly property int step: s.page === "comfy" ? 0 : s.page === "models" ? 1 : 2
    property string previousPage: ""
    property bool errorDetailsOpen: false
    property string previousError: ""
    function t(en, zh) { return s.zh ? zh : en }
    function releaseVersion(value) { return value && value.product_version ? "v" + value.product_version : value && value.version || "—" }
    function releaseSummary(value) { return value && value.release_notes ? value.release_notes[s.zh ? "zh" : "en"].summary : "" }
    readonly property var currentRelease: s.update.current_release || {version: s.update.current}
    // One line for the current step: the installer's text when it has one (a download and its size,
    // a resumed download, a check), else the step's own detail (keeping the original file), else nothing.
    readonly property string stepText: s.progress_text || s.detail || ""
    // While something downloads, the installer gives the seconds left from its bytes and speed; nothing otherwise.
    readonly property string remainingText: !number(s.remaining_seconds) || s.remaining_seconds <= 0 ? ""
        : s.remaining_seconds < 60 ? t("Less than a minute left", "预计还需不到 1 分钟")
        : t("About " + Math.round(s.remaining_seconds / 60) + "\u00a0min left", "预计还需约 " + Math.round(s.remaining_seconds / 60) + "\u00a0分钟")
    readonly property var availableRelease: s.update.candidate || (s.update.engine ? currentRelease : null)
    readonly property var availableEarlier: s.update.candidate ? (s.update.candidate_earlier || []) : s.update.engine ? (s.update.engine_earlier || []) : []
    function sourceName(value) { return ({"auto": t("Automatic", "自动选择"), "official": "Hugging Face", "hf-mirror": t("HF Mirror", "HF 镜像"), "modelscope": t("ModelScope", "魔搭")})[value] || value }
    function number(n) { return typeof n === "number" && isFinite(n) }
    function fraction(row) { return row && number(row.total) && row.total > 0 && number(row.done) && row.done <= row.total ? row.done / row.total : -1 }
    function bytes(n) {
        // macOS counts in decimal units like Finder: 1.26 TB, 64.4 GB.
        if (s.decimal_sizes) {
            var units = [[1e12, " TB", 2], [1e9, " GB", 2], [1e6, " MB", 1]]
            for (var i = 0; i < units.length; i++)
                if (n >= units[i][0]) return parseFloat((n/units[i][0]).toFixed(units[i][2])) + units[i][1]
            return Math.round(n/1e3) + " KB"
        }
        return n >= 1073741824 ? (n/1073741824).toFixed(1)+" GiB" : (n/1048576).toFixed(1)+" MiB"
    }
    readonly property var disk: s.disk || null
    readonly property var diskChoices: disk ? disk.others : []
    readonly property var bestDisk: diskChoices.filter(function(d) { return d.enough })[0] || null
    property bool diskDialogOpen: false
    function diskLine() {
        var d = disk
        if (!d) return ""
        var en = "\"" + d.name + "\"", zh = "“" + d.name + "”"
        if (d.problem === "fat32") return t(en + " uses FAT32, which limits each file to 4 GB, too small for the model files.", zh + "是 FAT32 格式，单个文件最大 4 GB，放不下模型。")
        if (d.problem === "exfat") return t(en + " uses exFAT, where the FreeVideo environment cannot run.", zh + "是 exFAT 格式，装不了 FreeVideo 的运行环境。")
        if (d.problem === "disconnected") return t(en + " is not connected. Connect it to continue.", zh + "没有连接，请接上这块硬盘。")
        if (d.problem === "read-only") return t(en + " is read-only on this Mac.", zh + "在这台 Mac 上是只读的。")
        if (d.short) return t(en + " has " + d.free + " available; this installation needs about " + d.need + ".",
                              zh + "可用 " + d.free + "，这次安装需要约 " + d.need + "。")
        return t(d.name + " · " + d.free + " available", d.name + " · 可用 " + d.free)
            + (d.need ? t(" · about " + d.need + " needed", " · 需要约 " + d.need) : "")
    }
    // The label before diskLine() when the line states the disk rather than a problem.
    function diskLabel() { return disk && !disk.problem && !disk.short ? t("Disk:", "所在硬盘：") : "" }
    // On a Mac, a disk problem is fixed by choosing another disk, not by reporting a fault.
    readonly property bool diskFailure: !!disk && (s.failure.kind || "").indexOf("disk") === 0
    readonly property bool updateOffered: s.update.engine || (!!s.update.candidate && ["available", "downloading", "ready", "error", "cancelled"].indexOf(s.update.status) >= 0)
    // An engine update needs no download; offer it as the way to launch.
    readonly property bool updateFirst: s.page === "launcher" && s.update.engine && !s.update.phase && !s.busy && s.status !== "open" && s.status !== "restart-required" && !s.needs_consent && !s.repair_offered
    function percent(row) { return row && row.total ? Math.floor(100 * row.done / row.total) + "%" : "" }
    readonly property var upgrade: s.model_upgrade || ({})
    readonly property bool upgradeShown: !upgrade.dismissed && ["available", "low-disk", "downloading", "downloaded", "switching", "releasing", "releasable", "complete", "failed"].indexOf(upgrade.status) >= 0
    function upgradeHeadline() {
        var u = upgrade
        if (u.status === "available") return t("A faster model is available for your GPU", "你的显卡有更快的模型可用")
        if (u.status === "low-disk") return t("Not enough disk space for the faster model", "磁盘空间不够，暂时无法升级到更快的模型")
        if (u.status === "downloading") return t("Downloading the faster model", "正在下载更快的模型")
        if (u.status === "downloaded" && u.waiting === "comfy") return t("Switching after ComfyUI is closed", "关闭 ComfyUI 后切换")
        if (u.status === "downloaded" || (u.status === "switching" && !s.busy)) return t("Switching after the current video", "当前视频完成后切换")
        if (u.status === "switching") return t("Switching to the faster model", "正在切换到更快的模型")
        if (u.status === "releasing") return u.waiting ? t("Removing the old model after the current video", "当前视频完成后删除旧模型") : t("Removing the old model", "正在删除旧模型")
        if (u.status === "releasable") return u.in_use === "int8_convrot" ? t("The old model is still on disk", "旧模型还占着磁盘空间")
                                                                           : t("Some model files are not in use", "有没在使用的模型文件")
        if (u.status === "complete" && u.in_use) return u.in_use === "int8_convrot" ? t("The old model was removed", "旧模型已删除")
                                                                             : t("Unused model files were removed", "没在使用的模型文件已删除")
        if (u.status === "complete") return t("Upgraded to the faster model", "已升级到更快的模型")
        if (u.status === "failed") return u.step === "release" ? t("The old model was not removed", "旧模型没有删除") : t("The upgrade stopped", "升级未完成")
        return ""
    }
    function upgradeBlocker(code) {
        if (code === "prepared-folder-redirected") return t("The model folder is linked to another location, and FreeVideo never deletes through a link.", "模型文件夹被链接到了别的位置，FreeVideo 不会通过链接删除文件。")
        if (code === "unexpected-active-variant") return t("FreeVideo is not using the int8 model, so the old model stays.", "当前使用的不是 int8 模型，旧模型保留。")
        if (code === "active-variant-incomplete") return t("Some int8 files are missing, so the old model stays.", "int8 模型文件不完整，旧模型保留。")
        return t("The model in use is not a standard FreeVideo model, so nothing was deleted.", "当前使用的模型不是标准安装的模型，为安全起见没有删除任何文件。")
    }
    function upgradeExplanation() {
        var u = upgrade
        var lora = u.lora_variants && u.lora_variants.length
        var speed = u.architecture === "ampere" ? t("about twice as fast on this GPU", "在这张显卡上预计快约 2 倍")
                                                 : t("about 10–30% faster on this GPU", "在这张显卡上预计快约 10–30%")
        if (u.status === "available")
            return (u.resized ? t("Recounted with the other model files this installation still needs. ", "下载量已按实际重新计算，包含这份安装还缺的其他模型文件。") : "")
                + t("The int8 model is ", "int8 模型") + speed + t(" and closer to the original quality. It downloads ", "，画质也更接近原版。需要下载约 ") + bytes(u.download_bytes)
                + (lora ? t("; your old model stays for the fused LoRAs that int8 cannot merge yet.", "；你有需要合并的 LoRA，int8 暂不支持，旧模型会保留，不释放空间。")
                        : t(", then removes the old model and frees about ", "，完成后删除旧模型，释放约 ") + bytes(u.release_bytes) + t(".", "。"))
                + t(" You can keep generating while it downloads.", "下载期间可以继续生成。")
        if (u.status === "low-disk")
            return t("It needs about ", "需要约 ") + bytes(u.required_bytes) + t(" free; ", " 可用空间，目前剩 ") + bytes(u.free_bytes)
                + t(" is free. Clean the download cache in Settings → Storage or free some space, then reopen FreeVideo.", "。可以在 设置 → 存储 清理下载缓存，或者腾出空间后重新打开 FreeVideo。")
        if (u.status === "downloading")
            return u.progress && u.progress.unit === "bytes" && u.progress.total ? bytes(u.progress.done) + " / " + bytes(u.progress.total) + t(" · you can keep generating", " · 可以继续生成")
                                                                                   : t("Checking the download…", "正在检查下载内容…")
        if (u.status === "downloaded" && u.waiting === "comfy")
            return t("This ComfyUI was not opened by the launcher, so FreeVideo cannot restart it. Close it and the switch starts on its own; until then the current model keeps working.",
                     "这个 ComfyUI 不是启动器打开的，FreeVideo 没法重启它。关闭它后会自动开始切换，在此之前当前模型照常可用。")
        if (u.status === "downloaded" || u.status === "switching")
            return t("ComfyUI restarts once and generation pauses for about 2 minutes while FreeVideo switches; settings and videos are kept.", "切换约需 2 分钟，ComfyUI 会重启一次，期间暂停生成，设置和视频都会保留。")
        if (u.status === "releasable" && u.in_use === "int8_convrot")
            return t("FreeVideo now uses the int8 model. Removing the old model frees about ", "现在用的是 int8 模型，删除不再使用的旧模型可以释放约 ") + bytes(u.release_bytes)
                + t("; only its own files are removed.", "，只删除旧模型自己的文件。")
        if (u.status === "releasable")
            return t("These model files are not in use, for example an int8 download this GPU could not run. Removing them frees about ", "这些模型文件没有在使用，比如这张显卡没能运行的 int8 下载。删除可以释放约 ") + bytes(u.release_bytes)
                + t("; the model in use is not touched.", "，正在使用的模型不受影响。")
        if (u.status === "releasing") return t("Only the old model's own files are removed.", "只删除旧模型自己的文件。")
        if (u.status === "complete")
            return (u.released_bytes ? t("Freed ", "已释放 ") + bytes(u.released_bytes) + t(".", "。") : "")
                + (lora ? t(" The old model stays for your fused LoRAs.", "旧模型为需要合并的 LoRA 保留。") : "")
        if (u.status === "failed")
            return (u.blockers && u.blockers.length ? upgradeBlocker(u.blockers[0]) + t(" ", "") : u.error ? u.error.split("\n")[0] + "\n" : "")
                + (u.step === "release" ? t("The int8 model is in use; the old files stay on disk for now.", "int8 模型已在使用，旧模型文件暂时保留在磁盘上。")
                   : u.step === "switch" && !u.kept ? t("The previous model's files are all kept. Retry on the setup page restores it; you can upgrade again afterwards.", "原来的模型文件都还在。在安装页面点「重试」即可恢复，之后可以再次升级。")
                   : t("Your previous model is unchanged and still works.", "原来的模型没有变化，可以继续使用。"))
        return ""
    }
    function updateHeadline() {
        var phase = s.update.phase
        if (phase === "downloading") return t("Downloading update ", "正在下载更新 ") + percent(s.update.progress)
        if (phase === "waiting") return t("Updating after the current video finishes", "当前视频生成完成后自动更新")
        if (phase === "restarting") return t("Restarting FreeVideo…", "正在重启 FreeVideo…")
        if (phase === "engine") return t("Updating the engine…", "正在更新引擎…")
        if (phase === "checking") return t("Checking for updates…", "正在检查更新…")
        if (s.update.status === "error") return t("Update incomplete", "更新未完成")
        if (s.update.candidate) return t("FreeVideo ", "FreeVideo ") + releaseVersion(s.update.candidate) + t(" is available", " 可以更新")
        return t("New engine ", "新版引擎 ") + releaseVersion(currentRelease) + t(" is ready", " 已就绪")
    }
    function updateExplanation() {
        var phase = s.update.phase
        if (phase === "downloading") return t("FreeVideo restarts when the download finishes. Models and settings are kept.", "下载完成后自动重启，模型和设置都会保留。")
        if (phase === "waiting") return t("Running videos are not interrupted. FreeVideo restarts on its own afterwards.", "不会中断正在生成的视频，完成后自动重启更新。")
        if (phase === "restarting" || phase === "engine") return t("ComfyUI restarts once; open FreeVideo pages refresh automatically.", "ComfyUI 会重启一次，已打开的 FreeVideo 页面会自动刷新。")
        if (phase === "checking") return ""
        if (s.update.status === "error" && s.update.error) return s.update.error
        if (s.update.candidate) return t("Current version ", "当前版本 ") + releaseVersion(currentRelease) + t(". Updating keeps your models and settings and takes about a minute.", "。更新会保留模型和设置，约需 1 分钟。")
        return t("Installed engine ", "已安装引擎 ") + (s.update.installed || "—") + t(". Updating takes about a minute and keeps your models and settings.", "。更新约需 1 分钟，模型和设置都会保留。")
    }
    function primaryText() {
        if (s.retry_kind) return retryText()
        if (needsRuntime) return t("Choose environment package", "选择运行环境包")
        if (s.page === "comfy") return t("Continue", "继续")
        if (needsPackages) return t("Choose offline packages", "选择离线包")
        if (s.page === "models") return usingRuntime && !offlineSelected ? t("Download models & continue", "下载模型并继续") : t("Check & continue", "检查并继续")
        if (updateFirst) return t("Update & launch", "更新并启动")
        if (s.page === "launcher") return s.status === "open" ? t("Open FreeVideo", "打开 FreeVideo") : s.status === "restart-required" ? t("Connect again", "重新连接") : t("Launch FreeVideo", "启动 FreeVideo")
        return s.status === "review" ? t("Install & launch", "安装并启动") : s.status === "restart-required" ? t("Connect again", "重新连接") : t("Check & resume", "检查并继续")
    }
    function retryText() {
        if (s.retry_kind === "check") return t("Check again", "重新检查")
        if (s.retry_kind === "launch") return t("Retry launch", "重试启动")
        if (s.retry_kind === "prepare" && usingRuntime && !offlineSelected) return t("Retry download", "重试下载")
        if (s.retry_kind === "import" || s.retry_kind === "prepare") return t("Retry import", "重试导入")
        return t("Retry installation", "重试安装")
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
        if (s.busy && !s.cleanup.busy) closePending = true
        else backend.close()
    }

    Rectangle {
        id: sidebar
        objectName: "sidebar"
        width: win.compact ? 200 : 232
        anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.left: parent.left
        color: win.color
        Rectangle { width: 1; color: theme.border; anchors.right: parent.right; height: parent.height }
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 16; spacing: 4
            // The FreeVideo wordmark, as in the creative workspace header.
            Image {
                objectName: "brandWordmark"
                source: "../assets/wordmark.png"; fillMode: Image.PreserveAspectFit
                Layout.preferredWidth: 150; Layout.preferredHeight: 25
                Layout.topMargin: 14; Layout.leftMargin: 6; Layout.bottomMargin: 31
                smooth: true; mipmap: true
                Accessible.role: Accessible.Graphic; Accessible.name: "FreeVideo"
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
            FButton { objectName: "versionInfoButton"; text: releaseVersion(currentRelease); flat: true; font.pixelSize: 11; Layout.topMargin: 12; Accessible.name: t("Version & release notes", "版本与更新说明"); onClicked: releaseNotesOpen = true }
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
                        // Repair sets up again from the launcher's own engine, so it brings the update along.
                        FText { objectName: "repairUpdatesEngine"; visible: !!s.update.with_repair; Layout.fillWidth: true; color: theme.text
                                text: t("Repair also updates the engine to " + releaseVersion(currentRelease) + ".", "修复时会一并更新到新版引擎 " + releaseVersion(currentRelease) + "。") }
                        Flow {
                            Layout.fillWidth: true; spacing: 8
                            FButton { objectName: "moveToDisk"; visible: diskFailure && s.form.new_comfy && !!bestDisk; enabled: !s.busy; primary: true
                                      text: bestDisk ? t("Install on \"" + bestDisk.name + "\"", "改装到“" + bestDisk.name + "”") : ""
                                      onClicked: backend.useDisk(bestDisk.path) }
                            FButton { objectName: "chooseDisk"; visible: diskFailure && s.form.new_comfy && diskChoices.length > (bestDisk ? 1 : 0); enabled: !s.busy
                                      text: t("Other disks…", "其他硬盘…"); onClicked: diskDialogOpen = true }
                            FButton { objectName: "diskCleanupButton"; visible: s.failure.kind === "disk"; enabled: s.can_cleanup; text: t("Clean download cache", "清理下载缓存"); onClicked: { settingsTab = "general"; settingsOpen = true; backend.cleanupDownloads(false) } }
                            FButton { visible: s.failure.kind === "download"; text: t("Change source", "切换下载源"); onClicked: { settingsTab = "downloads"; settingsOpen = true } }
                            FButton { objectName: "repairCardButton"; primary: true; visible: !!s.repair_offered; text: t("Repair", "修复"); onClicked: backend.action("repair", false) }
                            FButton { objectName: "copyError"; visible: !diskFailure; text: t("Copy full details", "复制完整详情"); onClicked: backend.copy(s.error) }
                            FButton { objectName: "showError"; text: errorDetailsOpen ? t("Hide details", "收起详情") : t("Show details", "查看详情"); flat: true; onClicked: errorDetailsOpen = !errorDetailsOpen }
                            FButton { objectName: "exportError"; visible: !diskFailure; text: t("Export report", "导出报告"); enabled: s.report.status !== "running"; onClicked: backend.exportReport() }
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
                            objectName: "environmentMethods"; visible: s.form.new_comfy && s.offline.runtime_supported !== false; Layout.fillWidth: true; spacing: 12
                            FText { text: t("Install method", "安装方式"); font.weight: Font.DemiBold; Layout.fillWidth: true }
                            FSegmented {
                                objectName: "environmentMethod"; Layout.preferredWidth: Math.min(340, parent.width * .72)
                                current: s.form.environment_method; enabled: !s.busy
                                options: [{value: "auto", label: t("Automatic", "自动安装")}, {value: "manual", label: t("Third-party download", "第三方下载")}]
                                onPicked: function(value) { backend.edit("environment_method", value) }
                                Accessible.name: t("Install method", "安装方式")
                            }
                        }
                        FDivider { visible: s.form.new_comfy && s.offline.runtime_supported !== false; Layout.fillWidth: true; Layout.topMargin: 4; Layout.bottomMargin: 4 }
                        FText { text: s.form.new_comfy ? t("Install location", "安装位置") : t("ComfyUI folder", "ComfyUI 目录"); font.weight: Font.DemiBold }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            FField { objectName: "installationPath"; Layout.fillWidth: true; text: s.form.new_comfy ? s.form.destination : s.form.comfy; placeholderText: t("Choose a folder", "选择文件夹"); enabled: !s.busy; onEditingFinished: backend.edit(s.form.new_comfy ? "destination" : "comfy", text) }
                            FButton { text: t("Browse…", "浏览…"); implicitHeight: theme.height + 4; onClicked: backend.browse(s.form.new_comfy ? "destination" : "comfy") }
                        }
                        FText { visible: !s.form.new_comfy; text: t("Uses your existing Python when available. A separate environment loads only FreeVideo.", "优先使用已有 Python；独立环境仅加载 FreeVideo。"); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                        RowLayout {
                            objectName: "installDisk"; visible: !!disk; Layout.fillWidth: true; spacing: 8
                            FIcon { kind: "disk"; ink: disk && (disk.problem || disk.short) ? theme.danger : theme.muted; Layout.preferredWidth: 16; Layout.preferredHeight: 16; Layout.alignment: Qt.AlignVCenter }
                            // A full-width colon carries its own space; pull the value up to it.
                            FText { id: diskLabelText; objectName: "installDiskLabel"; visible: !!diskLabel(); text: diskLabel(); color: theme.muted; font.pixelSize: theme.micro + 1; wrapMode: Text.NoWrap
                                    Layout.alignment: Qt.AlignBaseline; Layout.rightMargin: s.zh ? -(8 + Math.round(font.pixelSize / 2) - 2) : -4 }
                            FText { objectName: "installDiskText"; text: diskLine(); color: disk && (disk.problem || disk.short) ? theme.danger : theme.muted; font.pixelSize: theme.micro + 1; Layout.fillWidth: true; Layout.alignment: Qt.AlignBaseline }
                            // The label's right edge lines up with Browse; the hover fill reaches into the card's padding.
                            FButton { objectName: "changeDisk"; visible: s.form.new_comfy && diskChoices.length > 0; enabled: !s.busy; flat: true; implicitHeight: theme.heightSm; font.pixelSize: theme.micro + 1
                                      leftPadding: 10; rightPadding: 10; Layout.rightMargin: -10; Layout.alignment: Qt.AlignBaseline
                                      text: t("Choose another disk", "换一块硬盘"); onClicked: diskDialogOpen = true }
                        }
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
                        FText { objectName: "runtimeReadyOnModels"; text: t("✓ Environment ready · now the models", "✓ 运行环境已就绪 · 接下来准备模型"); color: theme.success; Layout.fillWidth: true }
                        FButton { text: t("Change", "更改"); flat: true; enabled: !s.busy; onClicked: backend.action("back", false) }
                    }
                    FText { text: usingRuntime ? t("Where should the models come from?", "选择模型来源") : t("How would you like to get the rest?", "选择下载方式"); font.pixelSize: theme.strong; font.weight: Font.DemiBold; Layout.fillWidth: true; Layout.topMargin: 6 }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 12
                        FChoice {
                            objectName: "automaticMethod"; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 1; Layout.fillHeight: true
                            text: t("Automatic download", "自动下载")
                            detail: usingRuntime ? t("Download the models into this offline installation.", "把模型下载到这个离线安装里。") : t("Download only what's missing.", "自动补齐缺少的文件。")
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
                        FText { visible: usingRuntime; text: t("An interrupted download resumes; downloaded files are kept.", "下载中断后可以接着下载，已下载的文件不会丢。"); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                        FMeter { visible: usingRuntime && s.offline.status === "downloading"; Layout.fillWidth: true; fraction: win.fraction(s.offline); active: visible }
                        FText { visible: usingRuntime && s.offline.status === "downloading"; text: t("Downloading models", "下载模型") + (number(s.offline.total) ? " · " + bytes(s.offline.done) + " / " + bytes(s.offline.total) : ""); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
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
                                FText { objectName: "offlinePackageGuide"; text: s.offline.guide; color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
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
                    FCard {
                        visible: !usingRuntime; Layout.fillWidth: true; padding: 16
                        FSwitch {
                            objectName: "samplingCaches"; Layout.fillWidth: true
                            text: t("Prepare all quality levels", "提前下载全部质量档位")
                            detail: t("Optional sampling caches · ", "可选采样缓存 · ") + bytes(s.sampling_cache_bytes)
                            checked: !!s.form.sampling_caches; enabled: !s.busy
                            onToggled: backend.edit("sampling_caches", checked)
                        }
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
                            // The plan's line (GPU, download, disk peak) belongs to the review; while installing,
                            // the step line already gives the download, so the card does not repeat it.
                            FText { objectName: "planSummary"; visible: !!s.summary && !s.busy; text: s.summary; color: theme.muted; font.pixelSize: theme.micro + 1; Layout.fillWidth: true }
                        }
                        FText { visible: s.busy; opacity: overallMeter.known ? 1 : 0; Behavior on opacity { NumberAnimation { duration: 200 } } text: Math.round(overallMeter.shown*100)+"%"; font.features: { "tnum": 1 }; font.pixelSize: 28; font.weight: Font.DemiBold; font.letterSpacing: -0.5; Layout.alignment: Qt.AlignTop }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 10; visible: s.busy
                        FMeter { id: overallMeter; objectName: "overallProgress"; Layout.fillWidth: true; fraction: win.fraction(s.overall); active: s.busy }
                        // One percentage on the card and no step counts. The time row holds at most two times:
                        // the elapsed time and, while something downloads, one estimate of what is left.
                        RowLayout {
                            Layout.alignment: Qt.AlignRight; spacing: 20; visible: !!s.elapsed
                            FText { objectName: "elapsedText"; text: t("Elapsed ", "已用时 ") + s.elapsed; font.pixelSize: theme.micro; color: theme.muted }
                            FText { objectName: "remainingText"; visible: !!remainingText; text: remainingText; font.pixelSize: theme.micro; color: theme.muted }
                        }
                        Rectangle { visible: !!stepText; Layout.fillWidth: true; Layout.topMargin: 4; Layout.bottomMargin: 4; height: 1; color: theme.border }
                        // The step's numbers are in this one line; the bar under it shows the same progress without numbers.
                        FText { objectName: "stepText"; text: stepText; visible: !!stepText; Layout.fillWidth: true; font.pixelSize: theme.micro + 1 }
                        FMeter { objectName: "stepProgress"; visible: !!stepText && win.fraction(s.progress) >= 0; Layout.fillWidth: true; fraction: win.fraction(s.progress); active: s.busy; subdued: true }
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
                                    FText { text: modelData.detail; font.pixelSize: theme.micro; Layout.fillWidth: true; color: modelData.state === "ready" ? theme.success : theme.muted }
                                }
                                // What this group downloads, as in the plan; nothing for one already here.
                                FText { objectName: "modelSize-" + modelData.id; visible: modelData.download > 0; text: bytes(modelData.download); font.pixelSize: theme.micro; color: theme.muted; font.features: { "tnum": 1 } }
                                FButton { text: t("Download links ↗", "下载地址 ↗"); implicitHeight: theme.heightSm; flat: true; font.pixelSize: theme.micro + 1; visible: s.form.model_method === "manual"; onClicked: { modelInfo = modelData.id === "text" ? "encoder" : modelData.id; modelInfoOpen = true } }
                            }
                        }
                    }
                }

                ColumnLayout {
                    objectName: "launcherPage"
                    visible: s.page === "launcher"; Layout.fillWidth: true; spacing: 16
                    FCard {
                        id: updateBanner; objectName: "updateBanner"
                        visible: (updateOffered && !(s.update.with_repair && !s.update.candidate)) || (!!s.update.phase && s.update.phase !== "review")
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
                                // Repair and opening FreeVideo come first; the banner's colour already marks the update.
                                id: engineUpdate; objectName: "engineUpdateButton"; primary: !s.repair_offered && s.status !== "open"; visible: !s.update.phase; enabled: !s.busy
                                text: s.update.status === "error" ? t("Retry update", "重试更新") : s.update.status === "ready" && s.update.candidate ? t("Restart & update", "重启并更新") : t("Update now", "立即更新")
                                onClicked: { manualUpdate = !!s.update.candidate; backend.update("") }
                            }
                        }
                        FMeter { visible: s.update.phase === "downloading"; Layout.fillWidth: true; fraction: s.update.progress && s.update.progress.total ? s.update.progress.done / s.update.progress.total : -1; active: visible }
                        FText { visible: !s.update.phase && !!text; text: releaseSummary(availableRelease); Layout.fillWidth: true; color: theme.muted; font.pixelSize: theme.micro }
                        FButton { text: t("What's new", "更新内容"); flat: true; visible: !s.update.phase; implicitHeight: theme.heightSm; onClicked: releaseNotesOpen = true }
                    }
                    FCard {
                        objectName: "modelUpgradeCard"
                        visible: upgradeShown
                        reveal: true; Layout.fillWidth: true; padding: 18; spacing: 10
                        color: upgrade.status === "failed" || upgrade.status === "low-disk" ? theme.surface : theme.accentSubtle
                        border.color: upgrade.status === "failed" || upgrade.status === "low-disk" ? theme.border : theme.accentDim
                        RowLayout {
                            Layout.fillWidth: true; spacing: 14
                            FIcon { kind: upgrade.status === "releasable" ? "folder" : "download"; ink: upgrade.status === "failed" ? theme.muted : theme.accent; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                FText { objectName: "modelUpgradeHeadline"; text: upgradeHeadline(); font.weight: Font.DemiBold; Layout.fillWidth: true }
                                FText { objectName: "modelUpgradeText"; visible: text !== ""; text: upgradeExplanation(); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            }
                            FButton { objectName: "modelUpgradeLater"; visible: ["available", "low-disk", "releasable", "complete", "failed"].indexOf(upgrade.status) >= 0; flat: true
                                      text: upgrade.status === "complete" || upgrade.status === "low-disk" ? t("Close", "关闭") : t("Later", "稍后"); onClicked: backend.dismissModelUpgrade() }
                            FButton { objectName: "modelUpgradeStop"; visible: upgrade.status === "downloading"; flat: true; text: t("Stop download", "停止下载"); onClicked: backend.cancelModelUpgrade() }
                            FButton {
                                objectName: "modelUpgradeButton"; primary: true
                                visible: upgrade.status === "available" || upgrade.status === "releasable"
                                         || (upgrade.status === "failed" && (upgrade.step === "download" || upgrade.step === "release" || (upgrade.step === "switch" && upgrade.kept)))
                                enabled: !s.busy && !upgrade.busy
                                text: upgrade.status === "failed" ? t("Try again", "重试") : upgrade.status === "releasable" ? t("Remove…", "删除…") : t("Download & upgrade", "下载并升级")
                                onClicked: upgrade.status === "releasable" ? releaseConfirm = true : backend.upgradeModel()
                            }
                        }
                        FMeter {
                            // Waiting for the user to close ComfyUI is not progress.
                            visible: ["downloading", "downloaded", "switching", "releasing"].indexOf(upgrade.status) >= 0 && upgrade.waiting !== "comfy"; Layout.fillWidth: true; active: visible
                            fraction: upgrade.status === "downloading" && upgrade.progress && upgrade.progress.fraction !== null && upgrade.progress.fraction !== undefined ? upgrade.progress.fraction : -1
                        }
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
                                // A failed start stays failed while another task (an import, a cleanup) runs: the controller's own tasks clear it first.
                                FText { objectName: "statusTitle"; text: s.status === "open" ? t("FreeVideo is ready", "FreeVideo 已就绪") : s.status === "failed" ? t("Startup interrupted", "启动未完成") : s.busy ? t("Starting ComfyUI", "正在启动 ComfyUI") : s.status === "restart-required" ? t("Restart ComfyUI", "请重启 ComfyUI") : t("Ready to launch", "准备就绪"); font.pixelSize: theme.section + 2; font.weight: Font.DemiBold; Layout.fillWidth: true }
                                FText { objectName: "statusDetail"; text: s.status === "open" ? s.url : s.status === "restart-required" ? t("Restart your running ComfyUI to load the updated nodes, then connect again.", "重启正在运行的 ComfyUI 以载入更新后的节点，再点击重新连接。") : s.status === "failed" ? t("ComfyUI did not start. See the message above.", "ComfyUI 未能启动，原因见上方。") : t("Your models and environment are connected.", "已连接你的模型与运行环境。"); color: theme.muted; Layout.fillWidth: true }
                                FText { objectName: "portMovedNote"; visible: s.status === "open" && s.port_from > 0; text: t("Port %1 is in use by another program, so FreeVideo now uses port %2 and will keep using this address.", "端口 %1 已被其他程序占用，FreeVideo 改用端口 %2，以后也使用这个地址。").arg(s.port_from).arg(s.port_to); font.pixelSize: 13; color: theme.muted; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.topMargin: -2 }
                            }
                        }
                        FMeter { objectName: "statusMeter"; visible: s.busy && s.status !== "failed"; Layout.fillWidth: true; fraction: win.fraction(s.overall); active: visible }
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
                        FText { objectName: "oldVersions"; visible: !!s.old_versions; text: "✓  " + s.old_versions; color: theme.success; font.pixelSize: theme.micro; Layout.fillWidth: true }
                        FText { objectName: "copiedModels"; visible: !!s.copied_models; text: s.copied_models || ""; color: theme.muted; font.pixelSize: theme.micro; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        RowLayout {
                            visible: !!s.archives && !confirmArchives; spacing: 12; Layout.fillWidth: true
                            FText { objectName: "archivesOffer"; text: s.archives || ""; color: theme.muted; font.pixelSize: theme.micro; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            FButton { objectName: "deleteArchives"; implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding; Layout.rightMargin: -rightPadding; text: t("Delete ZIP files", "删除压缩包"); flat: true; implicitHeight: theme.heightSm - 2; font.pixelSize: theme.micro + 1; enabled: !s.busy; onClicked: confirmArchives = true }
                        }
                        // The user's own files, and not recoverable: confirm in place, no dialog.
                        RowLayout {
                            visible: !!s.archives && confirmArchives; spacing: 8; Layout.fillWidth: true
                            FText { objectName: "archivesConfirm"; text: s.archives_confirm || ""; color: theme.text; font.pixelSize: theme.micro; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            FButton { objectName: "confirmDeleteArchives"; text: t("Delete", "删除"); danger: true; implicitHeight: theme.heightSm - 2; font.pixelSize: theme.micro + 1; enabled: !s.busy; onClicked: { confirmArchives = false; backend.action("delete_archives", false) } }
                            FButton { objectName: "cancelDeleteArchives"; implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding; Layout.rightMargin: -rightPadding; text: t("Cancel", "取消"); flat: true; implicitHeight: theme.heightSm - 2; font.pixelSize: theme.micro + 1; onClicked: confirmArchives = false }
                        }
                        FText { objectName: "archivesDone"; visible: !!s.archives_done; text: "✓  " + (s.archives_done || ""); color: theme.success; font.pixelSize: theme.micro; Layout.fillWidth: true }
                        FText { objectName: "archivesFailed"; visible: !!s.archives_failed; text: s.archives_failed || ""; color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
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
                FButton { visible: s.busy && !s.cleanup.busy; text: t("Pause", "暂停"); onClicked: backend.action("stop", false) }
                // After a failed start, "Retry launch" already starts the installed version: one button, and the update banner leads.
                // Other retries (an import) start something else, so this one stays.
                FButton { objectName: "launchInstalledButton"; visible: updateFirst && s.retry_kind !== "launch"; flat: true; text: t("Launch current version", "启动当前版本"); onClicked: backend.action("primary", accepted) }
                FButton {
                    objectName: "primaryButton"; primary: !s.repair_offered && !(s.retry_kind === "launch" && engineUpdate.visible); implicitWidth: Math.max(160, contentItem.implicitWidth+40); implicitHeight: theme.heightLg
                    text: s.busy ? t("Working…", "正在处理…") : primaryText()
                    enabled: !s.busy && !(s.page === "launcher" && s.needs_consent && !accepted)
                             && !(s.page === "progress" && s.status === "review" && !s.retry_kind && (!accepted || !!s.error))
                    onClicked: { if (s.retry_kind) { backend.action("retry", accepted); return } if (needsRuntime) { backend.browseRuntimePackage(); return } if (needsPackages) { backend.browsePackages(); return } if (updateFirst) { backend.update(""); return } backend.action(s.page === "launcher" && s.status === "open" ? "browser" : "primary", accepted); if (s.busy && s.page === "launcher") terminalOpen = true }
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
                            FButton { text: t("Version & release notes", "版本与更新说明") + " · " + releaseVersion(currentRelease); flat: true; Layout.fillWidth: true; onClicked: releaseNotesOpen = true }
                        }
                        FGroup {
                            Layout.fillWidth: true; title: t("Storage", "存储")
                            RowLayout {
                                Layout.fillWidth: true; spacing: 12
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    FText { text: t("Download cache", "下载缓存"); Layout.fillWidth: true }
                                    FText {
                                        objectName: "cleanupStatus"; Layout.fillWidth: true; font.pixelSize: theme.micro
                                        color: s.cleanup.status === "error" ? theme.danger : theme.muted
                                        text: s.cleanup.status === "scanning" ? t("Checking…", "正在检查…") :
                                            s.cleanup.status === "cleaning" ? t("Cleaning…", "正在清理…") :
                                            s.cleanup.status === "ready" ? (s.cleanup.bytes > 0 ? t("About ", "约 ") + bytes(s.cleanup.bytes) + t(" can be freed. Models, environments and videos are kept.", " 可清理，保留模型、运行环境和视频。") : t("Nothing to clean.", "暂无可清理的下载缓存。")) :
                                            s.cleanup.status === "complete" ? t("Freed ", "已释放 ") + bytes(s.cleanup.released_bytes || 0) + (s.cleanup.skipped ? t(". Some files changed; check again.", "。部分文件已变化，请重新检查。") : t(".", "。")) :
                                            s.cleanup.status === "busy" ? t("Another task is using these files. Try again when it finishes.", "其他任务正在使用这些文件，完成后可重试。") :
                                            s.cleanup.status === "error" ? t("Cleanup could not finish. Check again to retry.", "清理未完成，可重新检查后再试。") :
                                            t("Installation files kept for repairs. Models, environments and videos are never removed.", "安装时缓存的文件，清理时不会删除模型、运行环境和视频。")
                                    }
                                }
                                FButton {
                                    objectName: "scanCacheButton"; visible: !(s.cleanup.status === "ready" && s.cleanup.bytes > 0)
                                    text: s.cleanup.status === "idle" ? t("Check", "检查") : t("Check again", "重新检查")
                                    implicitHeight: theme.heightSm; font.pixelSize: theme.micro + 1
                                    enabled: s.can_cleanup && ["scanning", "cleaning"].indexOf(s.cleanup.status) < 0
                                    onClicked: backend.cleanupDownloads(false)
                                }
                                FButton {
                                    objectName: "clearCacheButton"; visible: s.cleanup.status === "ready" && s.cleanup.bytes > 0
                                    text: t("Clean…", "清理…"); implicitHeight: theme.heightSm; font.pixelSize: theme.micro + 1
                                    enabled: s.can_cleanup; onClicked: cleanupConfirm = true
                                }
                            }
                            FText { visible: !!s.cleanup.error; text: s.cleanup.error || ""; color: theme.danger; font.pixelSize: theme.micro; Layout.fillWidth: true }
                            RowLayout {
                                objectName: "modelStorageRow"
                                visible: ["available", "low-disk", "downloading", "downloaded", "switching", "releasing", "releasable", "complete", "failed"].indexOf(upgrade.status) >= 0
                                Layout.fillWidth: true; spacing: 12
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    FText { text: t("Video model", "视频模型"); Layout.fillWidth: true }
                                    FText {
                                        objectName: "modelStorageStatus"; Layout.fillWidth: true; font.pixelSize: theme.micro; color: theme.muted; wrapMode: Text.WordWrap
                                        text: upgrade.status === "available" ? t("A faster int8 model is available: about ", "有更快的 int8 模型：下载约 ") + bytes(upgrade.download_bytes)
                                                  + (upgrade.release_bytes ? t(" to download, about ", "，完成后删除旧模型，释放约 ") + bytes(upgrade.release_bytes) + t(" freed afterwards.", "。") : t(" to download.", "。"))
                                            : upgrade.status === "releasable" ? (upgrade.in_use === "int8_convrot" ? t("The old model is no longer used; about ", "旧模型已不再使用，删除可释放约 ") : t("Model files not in use; about ", "有没在使用的模型文件，删除可释放约 ")) + bytes(upgrade.release_bytes) + t(" can be freed.", "。")
                                            : upgradeHeadline()
                                    }
                                }
                                FButton {
                                    objectName: "modelStorageButton"; visible: upgrade.status === "available" || upgrade.status === "releasable"
                                    text: upgrade.status === "releasable" ? t("Remove…", "删除…") : t("Upgrade", "升级")
                                    implicitHeight: theme.heightSm; font.pixelSize: theme.micro + 1
                                    enabled: !s.busy && !upgrade.busy
                                    onClicked: upgrade.status === "releasable" ? releaseConfirm = true : backend.upgradeModel()
                                }
                            }
                        }
                        FGroup {
                            Layout.fillWidth: true; title: t("Compatibility", "兼容性")
                            RowLayout {
                                Layout.fillWidth: true
                                FText { text: t("Level", "档位"); Layout.fillWidth: true }
                                // The level's name: the word before " · " in compatibility.LEVELS.
                                FText { objectName: "compatibilityLevel"; text: s.compatibility.available && s.compatibility.levels && s.compatibility.levels[s.compatibility.level || 0] ? t(s.compatibility.levels[s.compatibility.level || 0].en.split(" · ")[0], s.compatibility.levels[s.compatibility.level || 0].zh.split(" · ")[0]) : "—"; color: s.compatibility.available ? theme.accent : theme.disabled; font.weight: Font.DemiBold }
                            }
                            FSlider { Layout.fillWidth: true; from: 0; to: 3; stepSize: 1; value: s.compatibility.level || 0; enabled: s.compatibility.available && !s.busy; snapMode: Slider.SnapAlways; onMoved: backend.compatibility(Math.round(value), s.compatibility.automatic) }
                            FText { objectName: "compatibilityNote"; text: s.compatibility.available ? t("Applies from the next generation. Higher levels have the GPU handle less at a time, so generation is slower and the picture differs slightly; resolution, duration and steps stay the same.", "从下一次生成起生效。档位越高，显卡每次处理的数据越少，生成越慢，画面也会有细微差别；分辨率、时长和步数不变。") : t("Available after hardware setup.", "完成硬件检查后可调整。"); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                            FDivider {}
                            FSwitch { objectName: "compatibilityAutomatic"; Layout.fillWidth: true; text: t("Raise the level automatically after an unexpected interruption", "异常中断后自动提高兼容档位"); checked: !!s.compatibility.automatic; enabled: s.compatibility.available && !s.busy; onToggled: backend.compatibility(s.compatibility.level, checked) }
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
                            RowLayout {
                                Layout.fillWidth: true; spacing: 16
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    FText { text: t("Repair FreeVideo", "修复 FreeVideo"); Layout.fillWidth: true; color: s.can_repair ? theme.text : theme.disabled }
                                    FText {
                                        text: s.can_repair ? t("Checks this installation and reinstalls anything missing or changed. Models and videos are kept.", "检查这份安装，重新安装缺失或被改动的部分。模型和视频都会保留。")
                                                           : t("Available once FreeVideo is installed.", "安装完成后可用。")
                                        font.pixelSize: theme.micro; color: theme.muted; Layout.fillWidth: true
                                    }
                                }
                                FButton { objectName: "repairButton"; text: t("Repair", "修复"); enabled: s.can_repair; onClicked: { settingsOpen = false; backend.action("repair", false) } }
                            }
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
        objectName: "diskDialog"; visible: diskDialogOpen && !!disk; onClosed: diskDialogOpen = false
        width: Math.min(480, win.width - 40); closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        height: Math.min(diskContents.implicitHeight + padding * 2, win.height - 48)
        contentItem: ColumnLayout {
            id: diskContents; spacing: 12
            FText { text: t("Choose a disk", "选择安装硬盘"); font.pixelSize: theme.section + 2; font.weight: Font.DemiBold; Layout.fillWidth: true }
            FText { objectName: "diskDialogText"; Layout.fillWidth: true; color: theme.muted; font.pixelSize: theme.micro + 1
                    text: (disk && disk.need ? t("This installation needs about " + disk.need + ".\n", "这次安装需要约 " + disk.need + "。\n") : "")
                          + t("FreeVideo is installed in a FreeVideo folder on the disk you choose.", "FreeVideo 会装在所选硬盘的 FreeVideo 文件夹里。") }
            Repeater {
                model: diskChoices
                delegate: Rectangle {
                    required property var modelData
                    objectName: "diskRow-" + modelData.name
                    Layout.fillWidth: true; implicitHeight: Math.max(52, diskRowText.implicitHeight + 20); radius: theme.radiusSm
                    color: diskArea.containsMouse && modelData.enough ? theme.hover : theme.bg
                    border.color: diskArea.containsMouse && modelData.enough ? theme.sheen : theme.border
                    opacity: modelData.enough ? 1 : 0.55
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 12
                        FIcon { kind: "disk"; ink: modelData.enough ? theme.accent : theme.muted; Layout.preferredWidth: 18; Layout.preferredHeight: 18 }
                        ColumnLayout {
                            id: diskRowText; Layout.fillWidth: true; spacing: 2
                            FText { text: modelData.name; font.weight: Font.DemiBold; Layout.fillWidth: true }
                            FText { visible: !modelData.enough; Layout.fillWidth: true; color: theme.danger; font.pixelSize: theme.micro
                                    text: modelData.problem === "exfat" ? t("exFAT not supported", "不支持 exFAT")
                                        : modelData.problem === "fat32" ? t("FAT32 not supported", "不支持 FAT32")
                                        : t("Not enough space", "空间不够") }
                        }
                        FText { text: t(modelData.free + " available", "可用 " + modelData.free); color: theme.muted; font.pixelSize: theme.micro + 1; font.features: { "tnum": 1 }; wrapMode: Text.NoWrap; Layout.alignment: Qt.AlignVCenter }
                    }
                    MouseArea {
                        id: diskArea; anchors.fill: parent; hoverEnabled: true; enabled: modelData.enough && !s.busy
                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: { diskDialogOpen = false; backend.useDisk(modelData.path) }
                    }
                    Accessible.role: Accessible.Button; Accessible.name: modelData.name
                }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.topMargin: 4; spacing: 8
                FButton { text: t("Choose a folder…", "选择文件夹…"); flat: true; onClicked: { diskDialogOpen = false; backend.browse("destination") } }
                Item { Layout.fillWidth: true }
                FButton { text: t("Cancel", "取消"); onClicked: diskDialogOpen = false }
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
            FText { objectName: "videoModelGuide"; text: modelInfo === "video" ? s.video_model_guide : modelInfo === "decoder" ? t("Download the vae and audio_vae folders.", "下载 vae 和 audio_vae 文件夹。") : t("Download the text encoder to your model folder.", "下载文本编码器，放入模型目录。"); color: theme.muted; Layout.fillWidth: true; Layout.bottomMargin: 4 }
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
        // State changes also close it (an update started from the banner or a
        // page, or a task began); only a user dismissal counts as "Later".
        onClosed: { if (!s.busy && (s.update.remind || manualUpdate)) backend.dismissUpdate(); manualUpdate = false }
        height: Math.min(updateContents.implicitHeight + padding*2 + updateActions.implicitHeight + 14, win.height-48)
        contentItem: ColumnLayout {
            spacing: 14
            ScrollView {
            id: updateScroll; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
            ColumnLayout {
            id: updateContents; width: updateScroll.availableWidth
            spacing: 14
            FText { text: s.update.candidate ? t("Update available", "有可用更新") : s.update.engine ? t("Engine update", "引擎更新") : t("Updates", "更新"); font.pixelSize: theme.section + 2; font.weight: Font.DemiBold }
            FText { objectName: "updateDialogText"; text: s.update.phase === "waiting" ? t("A video is still generating. FreeVideo restarts and updates as soon as it finishes.", "还有视频正在生成，完成后会自动重启并更新。") : s.update.status === "ready" && s.update.candidate ? t("Downloaded. Restart to finish; models and settings are kept.", "下载完成，重启即可完成更新，模型和设置都会保留。") : s.update.candidate ? t("FreeVideo ", "FreeVideo ") + releaseVersion(s.update.candidate) + t(" is available (current ", " 已发布（当前 ") + releaseVersion(currentRelease) + t("). Updating keeps your models and settings. FreeVideo restarts after the download; running videos finish first.", "）。更新会保留模型和设置，下载完成后自动重启；正在生成的视频会先完成。") : s.update.engine ? t("This launcher already includes engine ", "启动器已带有新版引擎 ") + releaseVersion(currentRelease) + t(" (installed ", "（已安装 ") + (s.update.installed || "—") + (s.status === "open" ? t("). Updating takes about a minute, restarts ComfyUI once and keeps your models and settings.", "）。更新约需 1 分钟，会重启一次 ComfyUI，模型和设置都会保留。") : t("). Updating takes about a minute, then FreeVideo starts; models and settings are kept.", "）。更新约需 1 分钟，完成后自动启动，模型和设置都会保留。")) : s.update.status === "current" ? t("You're up to date.", "已是最新版本。") : s.update.status === "development" ? t("Running from source. Update with Git.", "当前从源码运行，请通过 Git 更新。") : s.update.status === "error" ? t("Couldn't check for updates. Try again below.", "暂时无法检查更新，请重试。") : t("Checking the latest release…", "正在检查最新版本…"); color: theme.muted; Layout.fillWidth: true }
            FMeter { Layout.fillWidth: true; visible: s.update.status === "downloading"; active: true; fraction: s.update.progress && s.update.progress.total ? s.update.progress.done/s.update.progress.total : -1 }
            FReleaseNotes { objectName: "updateReleaseNotes"; visible: !!availableRelease; Layout.fillWidth: true; release: availableRelease; earlier: availableEarlier; zh: s.zh; heading: t("What's new", "更新内容") + " · " + releaseVersion(availableRelease) }
            FButton { text: t("Version & release notes", "版本与更新说明"); flat: true; onClicked: releaseNotesOpen = true }
            FText { visible: !!s.update.error; text: s.update.error || ""; color: theme.danger; Layout.fillWidth: true; font.pixelSize: theme.micro }
            FField { id: githubToken; visible: !!s.update.error; Layout.fillWidth: true; echoMode: TextInput.Password; placeholderText: t("GitHub token · optional", "GitHub Token · 可选") }
            }
            }
            RowLayout {
                id: updateActions; Layout.fillWidth: true
                FButton { objectName: "updateLaterButton"; text: s.update.candidate || s.update.engine ? t("Later", "稍后更新") : t("Close", "关闭"); flat: true; onClicked: { manualUpdate = false; backend.dismissUpdate() } }
                Item { Layout.fillWidth: true }
                FButton { objectName: "updateNowButton"; text: s.update.status === "error" ? t("Retry update", "重试更新") : s.update.status === "ready" && s.update.candidate ? t("Restart & update", "重启并更新") : s.update.candidate || s.update.engine ? t("Update now", "立即更新") : t("Check again", "重新检查"); primary: true; enabled: ["checking","downloading"].indexOf(s.update.status) < 0 && s.update.phase !== "waiting"; onClicked: { manualUpdate = !!s.update.candidate || !s.update.engine; backend.update(githubToken.text) } }
            }
        }
    }

    FPopup {
        objectName: "releaseNotesDialog"; visible: releaseNotesOpen; onClosed: releaseNotesOpen = false
        width: Math.min(560, win.width - 40)
        height: Math.min(releaseContents.implicitHeight + padding * 2 + 60, win.height - 48)
        contentItem: ColumnLayout {
            spacing: 14
            ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true
                id: releaseScroll; clip: true; contentWidth: availableWidth
                ColumnLayout {
                    id: releaseContents; width: releaseScroll.availableWidth; spacing: 18
                    FText { text: t("Version & release notes", "版本与更新说明"); font.pixelSize: theme.section + 2; font.weight: Font.DemiBold; Layout.fillWidth: true }
                    FReleaseNotes { visible: !!s.update.candidate; Layout.fillWidth: true; release: s.update.candidate; earlier: s.update.candidate_earlier || []; zh: s.zh; heading: t("Available update", "可用更新") + " · " + releaseVersion(s.update.candidate) }
                    FDivider { visible: !!s.update.candidate }
                    FReleaseNotes { objectName: "currentReleaseNotes"; Layout.fillWidth: true; release: currentRelease; zh: s.zh; heading: t("Current version", "当前版本") + " · " + releaseVersion(currentRelease) }
                    FText { visible: !!s.update.installed; text: t("Installed engine build: ", "已安装引擎构建号：") + (s.update.installed || ""); color: theme.muted; font.pixelSize: theme.micro; Layout.fillWidth: true }
                }
            }
            FButton { objectName: "closeReleaseNotesButton"; text: t("Close", "关闭"); onClicked: releaseNotesOpen = false }
        }
    }

    FDialog {
        objectName: "cleanupConfirmation"; visible: cleanupConfirm
        title: t("Clean download cache?", "清理下载缓存？")
        text: t("About ", "将释放约 ") + bytes(s.cleanup.bytes || 0) + t(" will be freed. Future repairs may need to download these installation files again.", "。以后修复环境时，可能需要重新下载这些安装文件。")
        acceptText: t("Clean", "清理"); rejectText: t("Cancel", "取消")
        onAccepted: { cleanupConfirm = false; backend.cleanupDownloads(true) }
        onRejected: cleanupConfirm = false
    }
    FDialog {
        objectName: "releaseConfirmation"; visible: releaseConfirm
        title: upgrade.in_use === "int8_convrot" ? t("Remove the old model?", "删除旧模型？") : t("Remove unused model files?", "删除没在使用的模型文件？")
        text: (upgrade.in_use === "int8_convrot" ? t("The FP8 model files FreeVideo no longer uses are removed, about ", "将删除 FreeVideo 不再使用的 FP8 模型文件，释放约 ")
                                                 : t("The model files FreeVideo does not use are removed, about ", "将删除 FreeVideo 没在使用的模型文件，释放约 ")) + bytes(upgrade.release_bytes || 0)
              + (upgrade.in_use === "int8_convrot" ? t(". The int8 model, your videos and settings are not touched.", "。int8 模型、视频和设置都不受影响。")
                                                   : t(". The model in use, your videos and settings are not touched.", "。正在使用的模型、视频和设置都不受影响。"))
        acceptText: t("Remove", "删除"); rejectText: t("Cancel", "取消"); destructive: true
        onAccepted: { releaseConfirm = false; backend.upgradeModel() }
        onRejected: releaseConfirm = false
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
