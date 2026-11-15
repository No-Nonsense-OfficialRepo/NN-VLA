import QtQuick
Item {
 id: hint
 anchors.fill: parent
 property string description: ""
 HoverHandler {
  onHoveredChanged: {
   if(hovered) HoverHelp.begin(hint,hint.description);
   else HoverHelp.end(hint);
  }
 }
 onVisibleChanged: if(!visible) HoverHelp.end(hint)
 Component.onDestruction: HoverHelp.end(hint)
}
