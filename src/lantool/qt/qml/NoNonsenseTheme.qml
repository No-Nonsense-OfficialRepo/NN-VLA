import QtQuick
QtObject {
 property var overrides: ({})
 readonly property color dark: overrides.dark || "#0A0A0A"
 readonly property color content: overrides.content || "#121212"
 readonly property color panel: overrides.panel || "#181818"
 readonly property color insetPanel: overrides.insetPanel || "#1F1F1F"
 readonly property color border: overrides.border || "#2A2A2A"
 readonly property color paper: overrides.paper || "#EDE9E1"
 readonly property color secondary: overrides.secondary || "#A8A39A"
 readonly property color muted: overrides.muted || "#85817B"
 readonly property color brass: overrides.brass || "#B08D57"
 readonly property color selectedGold: overrides.selectedGold || "#C49A5C"
 readonly property color pressedGold: overrides.pressedGold || "#8E6E3B"
 readonly property color green: overrides.green || "#6F8A5E"
 readonly property color warning: overrides.warning || "#B9873D"
 readonly property color red: overrides.red || "#8B3A3A"
 readonly property color info: overrides.info || "#5E748A"
 readonly property color hover: insetPanel
 readonly property string ui: "Inter"
 readonly property string heading: "Cinzel"
 readonly property string display: "Cormorant Garamond"
}
