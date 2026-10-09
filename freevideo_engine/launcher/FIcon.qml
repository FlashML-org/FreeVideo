import QtQuick

Canvas {
    property string kind: "folder"
    property color ink: theme.accent
    property color knockout: theme.bg
    implicitWidth: 24; implicitHeight: 24
    onKindChanged: requestPaint()
    onInkChanged: requestPaint()
    onKnockoutChanged: requestPaint()
    onPaint: {
        var c = getContext("2d"); c.reset(); c.scale(width/24, height/24)
        c.strokeStyle = ink; c.lineWidth = 1.6; c.lineCap = "round"; c.lineJoin = "round"; c.beginPath()
        if (kind === "folder") { c.moveTo(3,7); c.lineTo(3,19); c.lineTo(21,19); c.lineTo(21,7); c.lineTo(12,7); c.lineTo(10,4); c.lineTo(3,4); c.closePath() }
        else if (kind === "play") { c.moveTo(8,4); c.lineTo(20,12); c.lineTo(8,20); c.closePath() }
        else if (kind === "terminal") { c.rect(2,4,20,16); c.moveTo(6,9); c.lineTo(9,12); c.lineTo(6,15); c.moveTo(12,15); c.lineTo(17,15) }
        else if (kind === "download") { c.moveTo(12,3); c.lineTo(12,15); c.moveTo(7,10); c.lineTo(12,15); c.lineTo(17,10); c.moveTo(4,16); c.lineTo(4,21); c.lineTo(20,21); c.lineTo(20,16) }
        else if (kind === "settings") { c.moveTo(5,3); c.lineTo(5,21); c.moveTo(12,3); c.lineTo(12,21); c.moveTo(19,3); c.lineTo(19,21); c.stroke(); c.beginPath(); c.fillStyle=knockout; c.rect(2,7,6,4); c.rect(9,14,6,4); c.rect(16,6,6,4); c.fill() }
        else if (kind === "text") { c.moveTo(5,5); c.lineTo(19,5); c.moveTo(12,5); c.lineTo(12,20); c.moveTo(8,20); c.lineTo(16,20) }
        else if (kind === "disk") { c.rect(3,7,18,10); c.moveTo(3,13); c.lineTo(21,13); c.moveTo(16.5,15.2); c.lineTo(17.5,15.2) }
        else if (kind === "decoder") { c.rect(3,5,18,14); c.moveTo(8,9); c.lineTo(5,12); c.lineTo(8,15); c.moveTo(16,9); c.lineTo(19,12); c.lineTo(16,15) }
        else { c.rect(3,4,18,16); c.moveTo(9,8); c.lineTo(16,12); c.lineTo(9,16); c.closePath() }
        c.stroke()
    }
}
