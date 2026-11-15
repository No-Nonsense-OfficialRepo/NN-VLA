import QtQuick
import QtQuick.Controls
Button {
 id: control
 property bool iconOnly: width < 72 && !!iconName
 property bool leftAligned: false
 property bool primary: false
 property bool selected: false
 property string edge: "center"
 property string glyph: ""
 property string labelFontFamily: Theme.ui
 property int labelFontSize: Theme.typeControl
 // Optional status colour (e.g. the Play/Install state). Transparent means the normal launcher look.
 property color accent: "transparent"
 readonly property bool accented: accent.a > 0
 property int iconSize: iconOnly ? 18 : 16
 implicitWidth: 150; implicitHeight: 42
 padding: 10; hoverEnabled: true
 opacity: enabled ? 1 : .5
  Accessible.name: text
 property string helpText: HoverHelp.describe(control.Accessible.name)
 onHoveredChanged: { if(hovered) HoverHelp.begin(control,helpText); else HoverHelp.end(control); }
 onPressed: HoverHelp.end(control)
 onVisibleChanged: if(!visible) HoverHelp.end(control)
 Component.onDestruction: HoverHelp.end(control)
 property string iconName: ActionIcons.forLabel(text)
 contentItem: Item {
  transformOrigin: Item.Center
  scale: control.enabled && control.hovered ? 1.05 : 1
  Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
  transform: Translate { y: control.down ? 1 : 0 }
  Image { x: control.iconOnly ? (parent.width-width)/2 : 8; anchors.verticalCenter: parent.verticalCenter; width: control.iconSize; height: width; visible: !!control.iconName; source: control.iconName ? Theme.iconPath+control.iconName+".svg" : "" }
  Text { id: buttonLabel; visible: !control.iconOnly; anchors.fill: parent; anchors.leftMargin: control.iconName ? 28 : 0
   text: control.text; font.family: control.labelFontFamily; font.pixelSize: control.labelFontSize; font.weight: Font.DemiBold
   color: control.selected ? Theme.brass : Theme.paper; horizontalAlignment: control.leftAligned ? Text.AlignLeft : Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
  }
 }
 // The canvas is larger than the button by 'pad' on every side, so the glow can spill out past the edge.
 background: Item {
  width: control.width; height: control.height
  transformOrigin: Item.Center
  scale: control.enabled && control.hovered ? 1.05 : 1
  Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
  Canvas {
   id: trace
   readonly property int pad: 24
   x: -pad; y: -pad
   width: parent.width + 2*pad; height: parent.height + 2*pad
   property bool lit: control.primary || control.selected || control.hovered || control.activeFocus || control.down || control.accented
   property color ink: control.accented ? (control.hovered || control.down || control.activeFocus ? Qt.lighter(control.accent, 1.35) : control.accent) : control.down || control.selected ? Theme.selectedGold : lit ? Theme.actionInk : Theme.idleTrace
   Connections { target: Theme; function onCurrentNameChanged() { trace.requestPaint(); } }
   onLitChanged: requestPaint()
   onInkChanged: requestPaint()
   onWidthChanged: requestPaint()
   onHeightChanged: requestPaint()
   Connections { target: control; function onDownChanged() { trace.requestPaint(); } function onEdgeChanged() { trace.requestPaint(); } function onActiveFocusChanged() { trace.requestPaint(); } }
   onPaint: {
    let P=pad,c=getContext("2d"),w=width-2*P,h=height-2*P,right=control.edge==="right",center=control.edge==="center",left=!right&&!center;
    c.setTransform(1,0,0,1,0,0);
    c.clearRect(0,0,width,height);
    c.translate(P,P);
    
    // Body. Chamfers only on the bottom corners the edge borders: left = bottom-left, centre = both bottom, right = bottom-right.
    c.beginPath();c.moveTo(2,2);c.lineTo(w-2,2);
    if(right||center){c.lineTo(w-2,h-13);c.lineTo(w-13,h-2);} else {c.lineTo(w-2,h-2);}
    if(right){c.lineTo(2,h-2);} else {c.lineTo(14,h-2);c.quadraticCurveTo(9,h-2,6,h-6);c.lineTo(2,h-12);}
    c.closePath();
    // Body fill fades out away from the border: fully transparent on the side opposite the edge, solid on the border side.
    let bodyFill=function(col){let gr=left?c.createLinearGradient(w,0,0,h):right?c.createLinearGradient(0,0,w,h):c.createLinearGradient(0,0,0,h);for(let i=0;i<=8;i++){let t=i/8;gr.addColorStop(t,String(Qt.rgba(col.r,col.g,col.b,col.a*Math.pow(t,2))));}return gr;};
    c.fillStyle=bodyFill(control.down?Theme.buttonPressed:Theme.buttonTop);c.fill();
    if(control.accented){c.fillStyle=bodyFill(Qt.rgba(control.accent.r,control.accent.g,control.accent.b,control.hovered?.34:.22));c.fill();}
    c.save();c.clip();let cloud=c.createRadialGradient(w*.28,h*.8,0,w*.28,h*.8,w*.8);cloud.addColorStop(0,String(Theme.buttonCloud));cloud.addColorStop(.35,String(Theme.buttonCloudMid));cloud.addColorStop(1,"rgba(255,255,255,0)");c.fillStyle=cloud;c.fillRect(0,0,w,h);c.restore();
    function path() {
     c.beginPath();
     let bevel=Math.min(9,(w-6)/4,(h-6)/2);
     if(center) { c.moveTo(3,h-3-bevel);c.lineTo(3+bevel,h-3);c.lineTo(w-3-bevel,h-3);c.lineTo(w-3,h-3-bevel); }
     else if(right) { c.moveTo(w-3,3);c.lineTo(w-3,h-3-bevel);c.lineTo(w-3-bevel,h-3);c.lineTo(3,h-3); }
     else { c.moveTo(3,3);c.lineTo(3,h-3-bevel);c.lineTo(3+bevel,h-3);c.lineTo(w-3,h-3); }
    }
    // Eased falloff: full strength at the middle, staying strong for a while, then dropping quickly to zero at the ends.
    let fade=c.createLinearGradient(center?0:(right?w:0),center?h:0,center?w:(right?0:w),h);
    if(control.iconOnly){let lg=center?w:Math.sqrt(w*w+h*h),fe=Math.min(.45,16/Math.max(lg,1));for(let i=0;i<=24;i++){let t=i/24,a=t<fe?Math.pow(t/fe,2):t>1-fe?Math.pow((1-t)/fe,2):1;fade.addColorStop(t,String(Qt.rgba(ink.r,ink.g,ink.b,a)));}}else{for(let i=0;i<=16;i++){let t=i/16,d=Math.abs(t-.5)*2;fade.addColorStop(t,String(Qt.rgba(ink.r,ink.g,ink.b,1-Math.pow(d,3))));}}
    c.save();c.translate(0,3);path();c.strokeStyle=String(Theme.buttonShadow);c.lineWidth=2;c.stroke();c.restore();
    // Soft glow (blurred shadow instead of stacked strokes, which showed as bands).
    if(lit) {c.save();c.shadowColor=String(Qt.rgba(ink.r,ink.g,ink.b,control.hovered||control.down||control.activeFocus?.85:.5));c.shadowBlur=control.activeFocus?12:10;path();c.strokeStyle=fade;c.lineWidth=control.hovered||control.down||control.activeFocus||!(control.primary||control.selected)?3:1.6;c.stroke();c.shadowBlur=control.hovered||control.down||control.activeFocus?14:10;c.lineWidth=control.hovered||control.down||control.activeFocus||!(control.primary||control.selected)?4:2.2;c.stroke();c.restore();}
    path();c.strokeStyle=fade;c.lineWidth=control.activeFocus?1.8:1.1;c.stroke();
   }
  }
 }

}
