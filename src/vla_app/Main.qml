import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "../lantool/qt/qml" as NN

ApplicationWindow {
    id: window
    visible: true
    title: "NN VLA"
    width: 320; height: 320
    minimumWidth: 320; maximumWidth: 320
    minimumHeight: 320; maximumHeight: 1400
    flags: Qt.Window | Qt.FramelessWindowHint
    color: NN.Theme.dark
    property var s: backend.state
    property string kickId: ""
    property string kickName: ""
    property string pendingAvatar: ""
    property string cropError: ""
    property real cropX: .5
    property real cropY: .5

    Rectangle { anchors.fill: parent; color: NN.Theme.dark;
                border.width: 1; border.color: NN.Theme.border }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 10; anchors.rightMargin: 10
        anchors.topMargin: 7; anchors.bottomMargin: 15
        spacing: 5

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 30
            MouseArea {
                anchors.fill: parent
                property real pressGlobalX
                property real pressGlobalY
                property real pressWindowX
                property real pressWindowY
                onPressed: function(mouse) {
                    const point = mapToGlobal(mouse.x, mouse.y)
                    pressGlobalX = point.x; pressGlobalY = point.y
                    pressWindowX = window.x; pressWindowY = window.y
                }
                onPositionChanged: function(mouse) {
                    if (!pressed) return
                    const point = mapToGlobal(mouse.x, mouse.y)
                    window.x = Math.round(pressWindowX + point.x - pressGlobalX)
                    window.y = Math.round(pressWindowY + point.y - pressGlobalY)
                }
            }
            RowLayout {
                anchors.fill: parent; spacing: 4
                Image { source: "../lantool/qt/assets/no-nonsense-monogram.png";
                        Layout.preferredWidth: 24; Layout.preferredHeight: 20;
                        fillMode: Image.PreserveAspectFit }
                NN.GText { text: "VLA"; font.family: NN.Theme.heading;
                           font.pixelSize: NN.Theme.scaledFont(24); Layout.fillWidth: true }
                NN.GUButton {
                    text: s.screen === "options" ? "Back" : "Options"
                    iconName: s.screen === "options" ? "back" : "settings"
                    iconOnly: true; Layout.preferredWidth: 30; Layout.preferredHeight: 28
                    onClicked: backend.show(s.screen === "options" ?
                                            (s.role === "host" ? "host" : s.role === "join" ? "join" : "start") : "options")
                }
                NN.GUButton { text: "Minimize"; iconName: "minimize"; iconOnly: true;
                              Layout.preferredWidth: 30; Layout.preferredHeight: 28;
                              onClicked: window.showMinimized() }
                NN.GUButton { text: "Close"; iconName: "close"; iconOnly: true;
                              Layout.preferredWidth: 30; Layout.preferredHeight: 28;
                              onClicked: window.close() }
            }
        }

        Item {
            Layout.fillWidth: true; Layout.fillHeight: true

            Flickable {
                id: startFlick
                anchors.fill: parent; contentWidth: width
                contentHeight: startColumn.implicitHeight
                clip: true; ScrollBar.vertical: ScrollBar {}
                visible: s.screen === "start"
                ColumnLayout { id: startColumn; width: startFlick.width - 6; spacing: 10
                NN.GText { text: "Choose a room role"; font.family: NN.Theme.heading;
                           font.pixelSize: NN.Theme.typeHeading; Layout.alignment: Qt.AlignHCenter }
                NN.GText { text: "Create a private virtual LAN or join one.";
                           color: NN.Theme.muted; Layout.alignment: Qt.AlignHCenter;
                           font.pixelSize: NN.Theme.typeSmall }
                NN.GUButton { text: "Host"; iconName: "room-start"; primary: true;
                              Layout.fillWidth: true; Layout.preferredHeight: 40;
                              onClicked: backend.show("host") }
                NN.GUButton { text: "Join"; iconName: "connect"; primary: true;
                              Layout.fillWidth: true; Layout.preferredHeight: 40;
                              onClicked: backend.show("join") }
                }
            }

            Flickable {
                id: hostFlick
                anchors.fill: parent; contentWidth: width
                contentHeight: hostColumn.implicitHeight
                clip: true; ScrollBar.vertical: ScrollBar {}
                visible: s.screen === "host"
                ColumnLayout { id: hostColumn; width: hostFlick.width - 6; spacing: 5
                NN.GText { text: "HOST"; font.family: NN.Theme.heading;
                           font.pixelSize: NN.Theme.typeSection }
                NN.GUButton { text: s.roomError ? "Retry" : s.connected ? "Close Room" : "Open Room";
                              iconName: s.roomError ? "refresh" : s.connected ? "room-stop" : "room-start";
                              accent: s.roomError ? NN.Theme.warning : s.connected ? NN.Theme.red : NN.Theme.green;
                              helpText: s.status + (s.roomError ? " Click to retry." :
                                                  s.connected ? " Close the room and disconnect everyone." : " Click to host a room.");
                              Layout.fillWidth: true; Layout.preferredHeight: 38;
                              enabled: !s.busy;
                              onClicked: s.connected ? backend.leaveRoom() : backend.openRoom(requirePassword.checked ? hostPassword.text : "") }
                RowLayout { Layout.fillWidth: true; visible: !s.connected; spacing: 10
                    CheckBox { id: requirePassword; objectName: "passwordCheck";
                        text: "Password"; palette.windowText: NN.Theme.paper
                        spacing: 6
                        indicator: Rectangle {
                            implicitWidth: 13; implicitHeight: 13; radius: 1
                            x: requirePassword.leftPadding
                            y: (requirePassword.height - height) / 2
                            color: requirePassword.checked ? NN.Theme.brass : NN.Theme.content
                            border.width: 1
                            border.color: requirePassword.checked ? NN.Theme.brass : NN.Theme.muted
                            NN.GText { anchors.centerIn: parent; text: "✓";
                                       visible: requirePassword.checked;
                                       color: NN.Theme.dark; font.pixelSize: 11 }
                        }
                    }
                    NN.GUField { id: hostPassword; Layout.fillWidth: true;
                                 placeholderText: "Room password";
                                 echoMode: TextInput.Password;
                                 enabled: requirePassword.checked; maximumLength: 256 }
                }
                RowLayout { Layout.fillWidth: true; Layout.preferredHeight: 28;
                            visible: s.connected
                    NN.GText { text: "KEY"; color: NN.Theme.muted;
                               font.pixelSize: NN.Theme.typeSmall }
                    Item { Layout.fillWidth: true }
                    NN.CopyText { text: s.roomKey || "—";
                                  copyValue: s.roomKey || "";
                                  description: "Copy room key";
                                  font.family: NN.Theme.code }
                }
                NN.GText { text: "USERS · " + (s.players || []).length;
                           visible: s.connected; font.family: NN.Theme.heading;
                           font.pixelSize: NN.Theme.typeSmall; color: NN.Theme.muted }
                Loader { Layout.fillWidth: true;
                         Layout.preferredHeight: Math.max(70, hostFlick.height - 135);
                         visible: s.connected;
                         sourceComponent: roster }
                }
            }

            Flickable {
                id: joinFlick
                anchors.fill: parent; contentWidth: width
                contentHeight: joinColumn.implicitHeight
                clip: true; ScrollBar.vertical: ScrollBar {}
                visible: s.screen === "join"
                ColumnLayout { id: joinColumn; width: joinFlick.width - 6; spacing: 5
                NN.GText { text: "JOIN"; font.family: NN.Theme.heading;
                           font.pixelSize: NN.Theme.typeSection }
                NN.GUButton { text: s.roomError ? "Retry" : s.connected ? "Leave Room" : "Join Host Automatically";
                              iconName: s.roomError ? "refresh" : s.connected ? "room-leave" : "search";
                              accent: s.roomError ? NN.Theme.warning : s.connected ? NN.Theme.red : NN.Theme.green;
                              helpText: s.status + (s.roomError ? " Click to retry." :
                                                  s.connected ? " Leave this room." : " Search for a LAN host.");
                              Layout.fillWidth: true; Layout.preferredHeight: 38;
                              enabled: !s.busy;
                              onClicked: s.connected ? backend.leaveRoom() :
                                         s.roomError ? backend.retryJoin() : backend.findHost() }
                RowLayout { Layout.fillWidth: true; spacing: 10; visible: !s.connected
                    NN.GUField { id: roomKeyField; Layout.fillWidth: true;
                                 placeholderText: "Room key";
                                 font.family: NN.Theme.code; enabled: !s.busy }
                    NN.GUButton { text: "Join"; iconName: "key";
                                  accent: NN.Theme.green;
                                  helpText: "Join using the entered room key.";
                                  Layout.preferredWidth: 80; Layout.preferredHeight: 38;
                                  enabled: !s.busy && roomKeyField.text.trim().length > 0;
                                  onClicked: backend.joinKey(roomKeyField.text) }
                }
                RowLayout { Layout.fillWidth: true; Layout.preferredHeight: 28;
                            visible: s.connected
                    NN.GText { text: "KEY"; color: NN.Theme.muted;
                               font.pixelSize: NN.Theme.typeSmall }
                    Item { Layout.fillWidth: true }
                    NN.CopyText { text: s.roomKey || "—";
                                  copyValue: s.roomKey || "";
                                  description: "Copy room key";
                                  font.family: NN.Theme.code }
                }
                NN.GText { text: "USERS · " + (s.players || []).length;
                           visible: s.connected; font.family: NN.Theme.heading;
                           font.pixelSize: NN.Theme.typeSmall; color: NN.Theme.muted }
                Loader { Layout.fillWidth: true;
                         Layout.preferredHeight: Math.max(70, joinFlick.height - 135);
                         visible: s.connected;
                         sourceComponent: roster }
                }
            }

            Flickable {
                id: optionsFlick
                anchors.fill: parent; contentWidth: width
                contentHeight: optionsColumn.implicitHeight
                clip: true; ScrollBar.vertical: ScrollBar {}
                visible: s.screen === "options"
                onVisibleChanged: if (visible) nameField.text = s.name
                ColumnLayout { id: optionsColumn; width: optionsFlick.width - 6; spacing: 7
                NN.GText { text: "OPTIONS"; font.family: NN.Theme.heading;
                           font.pixelSize: NN.Theme.typeSection }
                NN.GText { text: "USER NAME"; color: NN.Theme.muted;
                           font.pixelSize: NN.Theme.typeSmall }
                RowLayout { Layout.fillWidth: true; spacing: 5
                    NN.GUField { id: nameField; objectName: "profileNameField"; Layout.fillWidth: true;
                                 maximumLength: 32;
                                 onAccepted: backend.saveName(text) }
                    NN.GUButton { text: "Save"; iconName: "save";
                                  iconOnly: true; helpText: "Save player name.";
                                  Layout.preferredWidth: 36;
                                  onClicked: backend.saveName(nameField.text) }
                }
                NN.GUButton { text: "Change picture"; iconName: "image";
                              Layout.fillWidth: true;
                              onClicked: {
                                  let url = backend.chooseAvatar()
                                  if (!url) return
                                  window.pendingAvatar = url
                                  window.cropX = .5; window.cropY = .5; window.cropError = ""
                                  cropZoom.value = 1
                                  cropPopup.open()
                              } }
                NN.GUButton { text: "Network Diagnostic"; iconName: "search";
                              Layout.fillWidth: true; Layout.preferredHeight: 42;
                              onClicked: { backend.runDiagnostics(); diagnosticPopup.open() } }
                NN.GUButton { text: "Blocked users"; iconName: "lock";
                              visible: s.role === "host";
                              Layout.fillWidth: true; Layout.preferredHeight: 42;
                              onClicked: blacklistPopup.open() }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1;
                            color: NN.Theme.border }
                CheckBox { id: wipeCheck; objectName: "wipeCheck";
                           text: "Remove VLA on exit"; checked: s.wipeOnExit;
                           enabled: false; palette.windowText: NN.Theme.paper; spacing: 6;
                           onToggled: backend.setWipeOnExit(checked)
                    indicator: Rectangle {
                        implicitWidth: 13; implicitHeight: 13; radius: 1
                        x: wipeCheck.leftPadding
                        y: (wipeCheck.height - height) / 2
                        color: wipeCheck.checked ? NN.Theme.brass : NN.Theme.content
                        border.width: 1
                        border.color: wipeCheck.checked ? NN.Theme.brass : NN.Theme.muted
                        NN.GText { anchors.centerIn: parent; text: "✓";
                                   visible: wipeCheck.checked;
                                   color: NN.Theme.dark; font.pixelSize: 11 }
                    }
                }
                NN.GText { Layout.fillWidth: true; wrapMode: Text.WordWrap;
                           text: "Available when this app has its own native service.";
                           color: NN.Theme.muted; font.pixelSize: NN.Theme.typeSmall }
                }
            }
        }
    }

    NN.Panel {
        z: 40
        anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.margins: 14
        width: Math.min(290, window.width - 28)
        height: hoverDescription.implicitHeight + 16
        visible: NN.HoverHelp.description.length > 0
        fill: NN.Theme.panel; stroke: NN.Theme.border; cut: 7
        NN.GText { id: hoverDescription; anchors.fill: parent; anchors.margins: 8;
                   text: NN.HoverHelp.description; wrapMode: Text.WordWrap;
                   font.pixelSize: NN.Theme.typeSmall; maximumLineCount: 4;
                   elide: Text.ElideRight }
    }

    Popup {
        id: cropPopup
        objectName: "avatarCropPopup"
        anchors.centerIn: parent
        width: Math.min(290, window.width - 16)
        height: Math.min(380, window.height - 16)
        modal: true; focus: true; padding: 0
        background: NN.ChamferPopup {}
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 10; spacing: 3
            NN.GText { text: "POSITION PICTURE"; font.family: NN.Theme.heading;
                       font.pixelSize: NN.Theme.typeSection }
            NN.GText { text: cropImage.status === Image.Error ? "Picture could not be loaded." :
                             "Drag to pan · slide to zoom";
                       color: NN.Theme.muted; font.pixelSize: NN.Theme.typeSmall }
            Rectangle {
                id: cropFrame
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Math.min(220, cropPopup.width - 30, cropPopup.height - 178)
                Layout.preferredHeight: Layout.preferredWidth
                color: NN.Theme.dark; border.color: NN.Theme.brass; border.width: 1; clip: true
                Image {
                    id: cropImage
                    source: window.pendingAvatar
                    asynchronous: true; autoTransform: true; smooth: true
                    fillMode: Image.Stretch
                    readonly property real ratio: sourceSize.width > 0 && sourceSize.height > 0 ?
                                                  sourceSize.width / sourceSize.height : 1
                    width: Math.max(cropFrame.width, cropFrame.height * ratio) * cropZoom.value
                    height: Math.max(cropFrame.height, cropFrame.width / ratio) * cropZoom.value
                    x: (cropFrame.width - width) * window.cropX
                    y: (cropFrame.height - height) * window.cropY
                }
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.SizeAllCursor
                    property real lastX: 0
                    property real lastY: 0
                    onPressed: function(mouse) { lastX = mouse.x; lastY = mouse.y }
                    onPositionChanged: function(mouse) {
                        if (!pressed) return
                        if (cropImage.width > cropFrame.width)
                            window.cropX = Math.max(0, Math.min(1, window.cropX -
                                           (mouse.x - lastX) / (cropImage.width - cropFrame.width)))
                        if (cropImage.height > cropFrame.height)
                            window.cropY = Math.max(0, Math.min(1, window.cropY -
                                           (mouse.y - lastY) / (cropImage.height - cropFrame.height)))
                        lastX = mouse.x; lastY = mouse.y
                    }
                }
            }
            RowLayout { Layout.fillWidth: true; spacing: 6
                NN.GText { text: "Zoom"; color: NN.Theme.muted;
                           font.pixelSize: NN.Theme.typeSmall }
                Slider { id: cropZoom; Layout.fillWidth: true; from: 1; to: 3; value: 1 }
            }
            NN.GText { text: window.cropError; visible: !!window.cropError;
                       Layout.fillWidth: true; wrapMode: Text.WordWrap;
                       color: NN.Theme.red; font.pixelSize: NN.Theme.typeSmall }
            RowLayout { Layout.fillWidth: true; spacing: 6
                NN.GUButton { text: "Cancel"; objectName: "cropCancel";
                              Layout.fillWidth: true;
                              onClicked: cropPopup.close() }
                NN.GUButton { text: "Use picture"; objectName: "cropUsePicture";
                              Layout.fillWidth: true;
                              enabled: cropImage.status === Image.Ready;
                              onClicked: {
                                  let error = backend.setAvatarCrop(window.pendingAvatar, cropZoom.value,
                                                                    window.cropX, window.cropY)
                                  if (error) window.cropError = error
                                  else cropPopup.close()
                              } }
            }
        }
    }

    MouseArea {
        z: 20
        anchors.left: parent.left; anchors.right: parent.right
        anchors.bottom: parent.bottom; height: 13
        cursorShape: Qt.SizeVerCursor
        property real pressGlobalY
        property real pressHeight
        onPressed: function(mouse) {
            pressGlobalY = mapToGlobal(mouse.x, mouse.y).y
            pressHeight = window.height
        }
        onPositionChanged: function(mouse) {
            if (!pressed) return
            const delta = mapToGlobal(mouse.x, mouse.y).y - pressGlobalY
            window.height = Math.max(window.minimumHeight,
                                     Math.min(window.maximumHeight, Math.round(pressHeight + delta)))
        }
        Rectangle { anchors.horizontalCenter: parent.horizontalCenter;
                    anchors.bottom: parent.bottom; anchors.bottomMargin: 5;
                    width: 34; height: 2; radius: 1; color: NN.Theme.border }
    }

    Component {
        id: roster
        ListView {
            model: s.players || []
            clip: true; spacing: 4
            ScrollBar.vertical: ScrollBar {}
            delegate: NN.Panel {
                required property var modelData
                width: ListView.view.width; height: 40
                RowLayout { anchors.fill: parent; anchors.margins: 4; spacing: 4
                    Rectangle { Layout.preferredWidth: 22; Layout.preferredHeight: 22;
                                color: NN.Theme.insetPanel; radius: 3; clip: true
                        Image { anchors.fill: parent; source: modelData.avatar || "";
                                visible: !!modelData.avatar; fillMode: Image.PreserveAspectCrop }
                        NN.GText { anchors.centerIn: parent; visible: !modelData.avatar;
                                   text: String(modelData.name || "?").slice(0, 1).toUpperCase();
                                   font.family: NN.Theme.display }
                    }
                    NN.GText { text: modelData.name || "Player";
                               Layout.preferredWidth: 75; elide: Text.ElideRight;
                               font.pixelSize: NN.Theme.typeSmall }
                    NN.CopyText { text: modelData.ip || "—";
                                  copyValue: modelData.ip || "";
                                  description: "Copy VLA IP";
                                  Layout.fillWidth: true; elide: Text.ElideRight;
                                  font.family: NN.Theme.code; normalColor: NN.Theme.muted;
                                  font.pixelSize: NN.Theme.typeSmall }
                    NN.GText { text: modelData.ping_ms === null || modelData.ping_ms === undefined ? "—" : modelData.ping_ms + " ms";
                               Layout.preferredWidth: 42; color: NN.Theme.green;
                               font.pixelSize: NN.Theme.typeSmall;
                               horizontalAlignment: Text.AlignRight }
                    NN.GUButton { text: "Kick"; iconName: "close"; iconOnly: true;
                                  visible: s.role === "host" && !modelData.is_host;
                                  Layout.preferredWidth: 24; Layout.preferredHeight: 24;
                                  onClicked: { window.kickId = modelData.player_id;
                                               window.kickName = modelData.name; kickPopup.open() } }
                }
            }
        }
    }

    Popup { id: kickPopup; anchors.centerIn: parent; width: 290; height: 238;
            modal: true; focus: true; background: NN.ChamferPopup {}
        ColumnLayout { anchors.fill: parent; anchors.margins: 12; spacing: 7
            NN.GText { text: "Remove " + window.kickName + "?";
                       font.family: NN.Theme.heading; font.pixelSize: NN.Theme.typeSection }
            NN.GText { text: "Choose whether this user may rejoin.";
                       color: NN.Theme.muted }
            ColumnLayout { Layout.fillWidth: true; spacing: 4
                NN.GUButton { text: "Cancel"; Layout.fillWidth: true;
                              onClicked: kickPopup.close() }
                NN.GUButton { text: "Kick only"; Layout.fillWidth: true;
                              onClicked: { backend.kick(window.kickId, false); kickPopup.close() } }
                NN.GUButton { text: "Kick and block"; Layout.fillWidth: true;
                              accent: NN.Theme.red;
                              onClicked: { backend.kick(window.kickId, true); kickPopup.close() } }
            }
        }
    }

    Popup { id: blacklistPopup; anchors.centerIn: parent; width: 290; height: 275;
            modal: true; focus: true; background: NN.ChamferPopup {}
        ColumnLayout { anchors.fill: parent; anchors.margins: 12; spacing: 7
            NN.GText { text: "Room blacklist"; font.family: NN.Theme.heading;
                       font.pixelSize: NN.Theme.typeSection }
            NN.GText { text: "This list clears when the room closes."; color: NN.Theme.muted }
            ListView { Layout.fillWidth: true; Layout.fillHeight: true;
                       model: s.blacklist || []; clip: true; spacing: 5
                delegate: RowLayout { required property var modelData;
                    width: ListView.view.width; height: 38
                    NN.GText { text: modelData.name + " · " + modelData.ip;
                               Layout.fillWidth: true; elide: Text.ElideRight }
                    NN.GUButton { text: "Unblock"; Layout.preferredWidth: 95;
                                  onClicked: backend.unblock(modelData.id) }
                }
            }
            NN.GUButton { text: "Close"; Layout.alignment: Qt.AlignRight;
                          onClicked: blacklistPopup.close() }
        }
    }

    Popup { id: passwordPopup; anchors.centerIn: parent; width: 290; height: 175;
            visible: s.passwordPrompt; modal: true; focus: true;
            closePolicy: Popup.NoAutoClose; background: NN.ChamferPopup {}
        ColumnLayout { anchors.fill: parent; anchors.margins: 12; spacing: 7
            NN.GText { text: "Room password"; font.family: NN.Theme.heading;
                       font.pixelSize: NN.Theme.typeSection }
            NN.GUField { id: joinPassword; Layout.fillWidth: true;
                         echoMode: TextInput.Password; placeholderText: "Password";
                         onAccepted: backend.submitPassword(text) }
            NN.GUButton { text: "Join"; Layout.fillWidth: true;
                          onClicked: backend.submitPassword(joinPassword.text) }
        }
    }

    Popup { id: approvalPopup; anchors.centerIn: parent; width: 290; height: 180;
            visible: !!s.approval; modal: true; focus: true;
            closePolicy: Popup.NoAutoClose; background: NN.ChamferPopup {}
        ColumnLayout { anchors.fill: parent; anchors.margins: 12; spacing: 7
            NN.GText { text: "Allow player to join?"; font.family: NN.Theme.heading;
                       font.pixelSize: NN.Theme.typeSection }
            NN.GText { text: s.approval ? s.approval.name + " · " + s.approval.ip : "";
                       Layout.fillWidth: true; elide: Text.ElideRight }
            RowLayout { Layout.fillWidth: true
                NN.GUButton { text: "Deny and block"; Layout.fillWidth: true;
                              accent: NN.Theme.red;
                              onClicked: backend.decide(s.approval.id, false) }
                NN.GUButton { text: "Allow"; Layout.fillWidth: true;
                              onClicked: backend.decide(s.approval.id, true) }
            }
        }
    }

    Popup { id: diagnosticPopup; anchors.centerIn: parent; width: 290; height: 265;
            modal: true; focus: true; background: NN.ChamferPopup {}
        ColumnLayout { anchors.fill: parent; anchors.margins: 12; spacing: 7
            NN.GText { text: "Network Diagnostic"; font.family: NN.Theme.heading;
                       font.pixelSize: NN.Theme.typeSection }
            ListView { Layout.fillWidth: true; Layout.fillHeight: true;
                       model: s.diagnostic || []; clip: true; spacing: 10
                delegate: Column { required property var modelData;
                    width: ListView.view.width; spacing: 3
                    NN.GText { text: modelData.title; font.weight: Font.DemiBold }
                    NN.GText { text: modelData.detail; color: NN.Theme.muted;
                               width: parent.width; wrapMode: Text.WordWrap }
                }
            }
            RowLayout { Layout.fillWidth: true
                NN.GUButton { text: "Check again"; Layout.fillWidth: true;
                              onClicked: backend.runDiagnostics() }
                NN.GUButton { text: "Close"; Layout.fillWidth: true;
                              onClicked: diagnosticPopup.close() }
            }
        }
    }

    Popup { id: hostChoicePopup; anchors.centerIn: parent; width: 290; height: 250;
            visible: (s.hostChoices || []).length > 0;
            modal: true; focus: true; background: NN.ChamferPopup {}
        ColumnLayout { anchors.fill: parent; anchors.margins: 12; spacing: 7
            NN.GText { text: "Choose a LAN host"; font.family: NN.Theme.heading;
                       font.pixelSize: NN.Theme.typeSection }
            Repeater { model: s.hostChoices || []
                NN.GUButton { required property var modelData;
                              text: modelData.name + " · " + modelData.key;
                              Layout.fillWidth: true;
                              onClicked: backend.chooseHost(modelData.index) }
            }
        }
    }
}
