import QtQuick
GText {
    id: control
    property string copyValue: text
    property string description: "Copy " + copyValue
    property color normalColor: Theme.paper
    readonly property bool canCopy: copyValue.trim().length > 0
    color: pointer.pressed || feedback.running ? Theme.activeText : pointer.containsMouse || activeFocus ? Theme.actionInk : normalColor
    activeFocusOnTab: canCopy
    Accessible.role: Accessible.Button
    Accessible.name: description
    Accessible.onPressAction: copy()
    function copy() {
        if (!canCopy) return;
        backend.copyText(copyValue);
        feedback.restart();
    }
    Keys.onReturnPressed: copy()
    Keys.onEnterPressed: copy()
    Keys.onSpacePressed: copy()
    Timer { id: feedback; interval: 650 }
    MouseArea {
        id: pointer
        anchors.fill: parent
        enabled: control.canCopy
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: control.copy()
        onEntered: HoverHelp.begin(control, control.description)
        onExited: HoverHelp.end(control)
    }
    onVisibleChanged: if (!visible) HoverHelp.end(control)
    Component.onDestruction: HoverHelp.end(control)
}
