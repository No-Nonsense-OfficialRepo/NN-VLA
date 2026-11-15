import QtQuick
Text {
    color: font.pixelSize >= 16 || font.weight >= Font.DemiBold ? Theme.paper : Theme.secondary
    font.family: Theme.ui
    font.pixelSize: Theme.typeBody
    font.weight: Font.Normal
    textFormat: Text.PlainText
}
