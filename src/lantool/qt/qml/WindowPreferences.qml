pragma Singleton
import QtQuick
import QtCore
QtObject {
 id: preferences
 property Settings store: Settings { category: "Window" }
 readonly property var choices: [
  {key:"1280x720",label:"1280 × 720 · HD",width:1280,height:720},
  {key:"1366x768",label:"1366 × 768",width:1366,height:768},
  {key:"1600x900",label:"1600 × 900",width:1600,height:900},
  {key:"1920x1080",label:"1920 × 1080 · Full HD",width:1920,height:1080},
  {key:"2560x1440",label:"2560 × 1440 · QHD",width:2560,height:1440},
  {key:"3200x1800",label:"3200 × 1800 · QHD+",width:3200,height:1800},
  {key:"3840x2160",label:"3840 × 2160 · 4K UHD",width:3840,height:2160}
 ]
 readonly property var hostChoices: choices.map(function(choice) {
  let w=Math.round(choice.height*9/21);
  return {key:String(choice.height),label:w+" × "+choice.height,width:w,height:choice.height};
 })
 property string clientResolution: "1920x1080"
 property string hostResolution: "1080"
 property bool hasClientPreference: false
 property bool hasHostPreference: false
 property bool fillScreen: false
 property int effectiveWidth: 1920
 property int effectiveHeight: 1080
 property int effectiveHostWidth: 463
 property int effectiveHostHeight: 1080
 signal resolutionRequested(bool host,int width,int height)
 function setFillScreen(enabled) {
  fillScreen=enabled;
  if(!backend.state.preview) { store.setValue("fillScreen",enabled); store.sync(); }
 }
 function indexFor(key,host) { let options=host?hostChoices:choices; for(let i=0;i<options.length;i++) if(options[i].key===key) return i; return -1; }
 function requestCurrent(host) {
  let options=host?hostChoices:choices, i=indexFor(host?hostResolution:clientResolution,host);
  if(i>=0) resolutionRequested(host,options[i].width,options[i].height);
 }
 function apply(key,host) {
  let i=indexFor(key,host); if(i<0) return;
  if(host) { hostResolution=key; hasHostPreference=true; }
  else { clientResolution=key; hasClientPreference=true; }
  if(!backend.state.preview) { store.setValue(host?"hostResolution":"resolution",key); store.sync(); }
  requestCurrent(host);
 }
 function reload() {
  if(backend.state.preview && !backend.state.tutorial) return;
  store.sync(); let saved=String(store.value("resolution",""));
  let savedFill=store.value("fillScreen",false);
  fillScreen=(savedFill===true || savedFill==="true");
  let savedHost=String(store.value("hostResolution",""));
  if(indexFor(saved,false)>=0) {
   let changed=!hasClientPreference || saved!==clientResolution;
   clientResolution=saved; hasClientPreference=true;
   if(changed) requestCurrent(false);
  }
  if(indexFor(savedHost,true)>=0) {
   let changed=!hasHostPreference || savedHost!==hostResolution;
   hostResolution=savedHost; hasHostPreference=true;
   if(changed) requestCurrent(true);
  }
 }
 Component.onCompleted: reload()
}
