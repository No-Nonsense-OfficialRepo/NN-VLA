pragma Singleton
import QtQuick
import QtCore
QtObject {
 id: theme
 readonly property string defaultName: "No-Nonsense"
 readonly property string currentName: defaultName
 readonly property var names: [defaultName,"Pearl","Dark","Neon","Custom"]
 readonly property var editableColors: [
  {key:"dark", label:"App background"}, {key:"content", label:"Content and fields"},
  {key:"panel", label:"Panel surface"}, {key:"insetPanel", label:"Raised surface"},
  {key:"border", label:"Borders"}, {key:"paper", label:"Primary text"},
  {key:"secondary", label:"Secondary text"}, {key:"muted", label:"Muted text"},
  {key:"brass", label:"Accent"}, {key:"selectedGold", label:"Accent hover"},
  {key:"pressedGold", label:"Accent pressed"}, {key:"green", label:"Success"},
  {key:"warning", label:"Warning"}, {key:"red", label:"Error"}, {key:"info", label:"Info"}
 ]
 property var customColors: ({})
 property QtObject noNonsensePalette: NoNonsenseTheme {}
 property QtObject customPalette: NoNonsenseTheme { overrides: theme.customColors }
 property QtObject pearl: PearlTheme {}
 property QtObject darkPalette: DarkTheme {}
 property QtObject neon: NeonTheme {}
 property Settings preferences: Settings { category: "Appearance" }
 function paletteFor(name) { return name==="Custom" ? customPalette : name==="Dark" ? darkPalette : name==="Neon" ? neon : name==="Pearl" ? pearl : noNonsensePalette; }
 function valid(name) { return names.indexOf(name)>=0; }
 function validatedColors(values) {
  let result={};
  if (!values || typeof values !== "object") return result;
  for (const entry of editableColors) {
   const value=values[entry.key];
   if (typeof value==="string" && /^#[0-9a-f]{6}$/i.test(value)) result[entry.key]=value;
  }
  return result;
 }
 function saveCustom(values) {
  // NN VLA deliberately uses the fixed No-Nonsense palette.
 }
 function apply(name) {
  // Fixed palette.
 }
 function reload() {
  // Fixed palette.
 }
 readonly property QtObject active: paletteFor(currentName)
 readonly property QtObject editablePalette: currentName==="Custom" ? customPalette : noNonsensePalette
 readonly property bool usesPaletteTokens: currentName===defaultName || currentName==="Custom"
 readonly property bool light: currentName==="Pearl"
 readonly property color paper: active.paper
 readonly property color brass: active.brass
 readonly property color selectedGold: active.selectedGold
 readonly property color red: active.red
 readonly property color dark: active.dark
 readonly property color panel: active.panel
 readonly property color insetPanel: active.insetPanel
 readonly property color border: active.border
 readonly property color muted: active.muted
 readonly property color green: active.green
 readonly property color hover: active.hover
 readonly property color secondary: usesPaletteTokens ? editablePalette.secondary : paper
 readonly property color warning: usesPaletteTokens ? editablePalette.warning : "#C29A10"
 readonly property color info: usesPaletteTokens ? editablePalette.info : "#2F7BD9"
 readonly property color pressedGold: editablePalette.pressedGold
 readonly property string ui: active.ui
 readonly property string heading: active.heading
 readonly property string display: active.display
 readonly property string code: "Consolas"
 readonly property real fontScale: 0.72
 function scaledFont(size) { return Math.max(1, Math.round(size * fontScale)); }
 readonly property int typeCaption: scaledFont(16)
 readonly property int typeSmall: scaledFont(17)
 readonly property int typeBody: scaledFont(18)
 readonly property int typeControl: scaledFont(17)
 readonly property int typeSection: scaledFont(24)
 readonly property int typeHeading: scaledFont(34)
 readonly property int typePage: scaledFont(48)
 function alpha(color,opacity) { return Qt.rgba(color.r,color.g,color.b,opacity); }
 readonly property string assetRoot: currentName==="Pearl" ? "../assets/" :
                                    currentName==="Neon" ? "../assets/themes/neon/" : "../assets/themes/dark/"
 readonly property string iconPath: assetRoot+"controls/"
 readonly property string emblemPath: "../assets/no-nonsense-monogram.png"
 readonly property string geometryPath: assetRoot+"geometry.svg"
 readonly property url backgroundSource: usesPaletteTokens ? Qt.resolvedUrl("../assets/no-nonsense-glass.png") :
                                           Qt.resolvedUrl(assetRoot+(assetRoot==="../assets/" ? "pearl-glass.png" : "glass.png"))
 readonly property color iconColor: light ? "#535D66" : paper
 readonly property color actionInk: light ? "#C3A064" : brass
 readonly property color idleTrace: light ? "#FFFFFF" : alpha(paper,.5)
 readonly property color activeText: light ? "#9D7123" : selectedGold
 readonly property color controlIdle: idleTrace
 readonly property color controlHover: actionInk
 readonly property color controlClicked: selectedGold
 readonly property color textGlow: light ? "#D7A54C" : selectedGold
 readonly property color textHalo: light ? "#55DCB65D" : alpha(selectedGold,.33)
 readonly property color buttonPressed: usesPaletteTokens ? alpha(pressedGold,.15) : light ? "#1F78828C" : alpha(brass,.15)
 readonly property color buttonTop: light ? "#47FFFFFF" : alpha(paper,.07)
 readonly property color buttonBottom: light ? "#05FFFFFF" : alpha(paper,.01)
 readonly property color buttonCloud: light ? "#47FFFFFF" : alpha(brass,.07)
 readonly property color buttonCloudMid: light ? "#1FFFFFFF" : alpha(paper,.025)
 readonly property color buttonShadow: light ? "#0D414B55" : "#40000000"
 readonly property color diamondIdle: light ? "#A7B2BC" : brass
 readonly property color diamondSelected: light ? "#FFBD00" : selectedGold
 readonly property color diamondHot: light ? "#FFF6CE" : paper
 readonly property color frostTop: light ? "#A3FFFFFF" : alpha(paper,.09)
 readonly property color frostMid: light ? "#36FFFFFF" : alpha(paper,.025)
 readonly property color frostPool: light ? "#6BFFFFFF" : alpha(brass,.10)
 readonly property color frostPoolMid: light ? "#2EFFFFFF" : alpha(brass,.035)
 readonly property color diamondPressed: light ? "#0F555D66" : alpha(brass,.13)
 readonly property color diamondGrain: light ? "#0B5A6671" : alpha(paper,.035)
 readonly property color fieldSurface: usesPaletteTokens ? editablePalette.content : light ? "#35FFFFFF" : insetPanel
 readonly property color selection: light ? "#D9C7A3" : brass
 readonly property color toggleActive: light ? "#B99A65" : brass
 readonly property color toggleIdle: light ? "#C5C9CD" : border
 readonly property color toggleThumb: light ? "#FFFFFF" : paper
 readonly property color descriptionSurface: light ? "#EDF3F4F5" : alpha(dark,.97)
 readonly property color sidebarSurface: usesPaletteTokens ? panel : light ? "#35FFFFFF" : alpha(dark,.25)
 readonly property color selectedRow: light ? "#35B99A65" : hover
 readonly property color rowHover: light ? "#D1D5D9" : hover
 readonly property color detailsOverlay: light ? "#EAF4F5F5" : alpha(dark,.93)
 readonly property color closingOverlay: light ? "#E6F4F5F5" : alpha(dark,.96)
 readonly property color connectionRing: usesPaletteTokens ? border : light ? "#B69A73" : brass
 readonly property color presenceDot: light ? "#74B58C" : green
}
