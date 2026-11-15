import QtQuick
Canvas {
    id: trace
    property bool hovered: false
    property bool pressed: false
    property bool selected: false
    property color ink: pressed || selected ? Theme.controlClicked : hovered ? Theme.controlHover : Theme.controlIdle
    onInkChanged: requestPaint()
    onHoveredChanged: requestPaint()
    onPressedChanged: requestPaint()
    onSelectedChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        let c = getContext("2d"), w = width, y = height - 8;
        c.clearRect(0, 0, width, height);
        // Eased falloff: strong through the middle, dropping quickly to nothing at the ends.
        let fade = c.createLinearGradient(0, 0, w, 0);
        for (let i = 0; i <= 16; i++) { let t = i / 16, d = Math.abs(t - .5) * 2; fade.addColorStop(t, String(Theme.alpha(ink, 1 - Math.pow(d, 3)))); }
        c.strokeStyle = fade; c.lineCap = "round";
        // Soft glow from a blurred shadow (stacked strokes showed as bands).
        let strength = hovered || pressed || selected ? .55 : .2;
        c.save();
        c.shadowColor = String(Theme.alpha(ink, strength)); c.shadowBlur = 10; c.lineWidth = 3;
        c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke();
        c.restore();
        c.lineWidth = 1; c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke();
    }
}
