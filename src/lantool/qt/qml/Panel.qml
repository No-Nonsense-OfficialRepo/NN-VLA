import QtQuick
// Panel with 45-degree chamfered corners, matching the launcher's buttons. Children are clipped to the shape.
Item {
    id: panel
    property color fill: Theme.panel
    property color stroke: Theme.border
    property real cut: 12
    property bool bottomHighlight: false
    default property alias content: holder.data

    Canvas {
        id: shape
        anchors.fill: parent
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            let c = getContext("2d"), w = width, h = height, k = Math.min(panel.cut, w / 2, h / 2);
            c.clearRect(0, 0, w, h);
            c.beginPath();
            c.moveTo(k, 0); c.lineTo(w - k, 0); c.lineTo(w, k); c.lineTo(w, h - k);
            c.lineTo(w - k, h); c.lineTo(k, h); c.lineTo(0, h - k); c.lineTo(0, k);
            c.closePath();
            c.fillStyle = String(panel.fill); c.fill();
            c.strokeStyle = String(panel.stroke); c.lineWidth = 1; c.stroke();
        }
    }
    onFillChanged: shape.requestPaint()
    onStrokeChanged: shape.requestPaint()
    onCutChanged: shape.requestPaint()

    Item {
        id: holder
        anchors.fill: parent
        clip: true
        Loader {
            active: panel.bottomHighlight
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.leftMargin: panel.cut; anchors.rightMargin: panel.cut
            height: 14
            sourceComponent: BottomGlow { ink: Theme.brass; opacity: .7 }
        }
    }
}
