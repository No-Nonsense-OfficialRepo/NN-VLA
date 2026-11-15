import QtQuick
import QtQuick.Controls
TextField {
    id: control
    property string helpText: placeholderText || (readOnly ? "View this value." : "Edit this value.")
    HoverHint { description: control.helpText }
    implicitHeight: 38
    leftPadding: 10
    rightPadding: 10
    topPadding: 4
    bottomPadding: 4
    verticalAlignment: TextInput.AlignVCenter
    font.family: Theme.ui
    font.pixelSize: Theme.typeSmall
    font.weight: Font.Medium
    color: Theme.paper
    placeholderTextColor: Theme.muted
    selectionColor: Theme.selection
    selectedTextColor: Theme.usesPaletteTokens ? Theme.dark : Theme.paper
    background: Rectangle {
        radius: 3
        color: Theme.fieldSurface
        border.color: control.activeFocus ? Theme.brass : Theme.border
        border.width: 0
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: control.activeFocus ? 1.5 : .7; color: control.activeFocus ? Theme.brass : Theme.border }
    }
}
