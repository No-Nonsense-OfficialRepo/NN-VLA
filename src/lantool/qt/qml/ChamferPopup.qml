import QtQuick
// Popup background with 45 degree chamfers on all four corners (no rounding).
// A glow line runs along the bottom edge and fades out at both ends, like the buttons.
Canvas {
 id: shape
 property color fillColor: Theme.dark
 property color edgeColor: Theme.border
 property color glowColor: Theme.brass
 property real bevel: 14
 onFillColorChanged: requestPaint()
 onEdgeColorChanged: requestPaint()
 onGlowColorChanged: requestPaint()
 onBevelChanged: requestPaint()
 onWidthChanged: requestPaint()
 onHeightChanged: requestPaint()
 Component.onCompleted: requestPaint()
 onPaint: {
  let c = getContext("2d"), w = width, h = height, k = bevel;
  c.clearRect(0, 0, w, h);
  c.beginPath();
  c.moveTo(k, 0);
  c.lineTo(w - k, 0);
  c.lineTo(w, k);
  c.lineTo(w, h - k);
  c.lineTo(w - k, h);
  c.lineTo(k, h);
  c.lineTo(0, h - k);
  c.lineTo(0, k);
  c.closePath();
  c.fillStyle = String(fillColor);
  c.fill();
  c.strokeStyle = String(edgeColor);
  c.lineWidth = 1;
  c.stroke();

  // Glow along the bottom edge: eased falloff to zero at both ends, soft shadow halo.
  let y = h - 6, x0 = k, x1 = w - k, gc = glowColor;
  let fade = c.createLinearGradient(x0, 0, x1, 0);
  for (let i = 0; i <= 16; i++) {
   let t = i / 16, d = Math.abs(t - .5) * 2;
   fade.addColorStop(t, String(Qt.rgba(gc.r, gc.g, gc.b, 1 - Math.pow(d, 3))));
  }
  c.save();
  c.lineCap = "round";
  c.shadowColor = String(Qt.rgba(gc.r, gc.g, gc.b, .5));
  c.shadowBlur = 10;
  c.strokeStyle = fade;
  c.lineWidth = 2;
  c.beginPath();
  c.moveTo(x0, y);
  c.lineTo(x1, y);
  c.stroke();
  c.restore();
 }
}
