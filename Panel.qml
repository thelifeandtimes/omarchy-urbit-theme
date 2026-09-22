import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import qs.Commons
import qs.Ui as Ui
import "Model.js" as Model

Ui.Panel {
  id: root
  moduleName: "thelifeandtimes.urbit-theme"
  ipcTarget: "thelifeandtimes.urbit-theme"
  property var service: bar && bar.shell && typeof bar.shell.serviceFor === "function"
    ? bar.shell.serviceFor(moduleName) : null
  readonly property var account: service ? service.account : Model.emptyState()
  readonly property var currentPalette: service ? service.currentPalette : null
  readonly property bool ready: !!service && service.loaded
  readonly property color foreground: Color.foreground
  property bool adding: false
  property string formError: ""
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function clearSecret() {
    // Assignment also resets Qt's undo history.
    if (codeField) codeField.text = ""
  }
  function cancelLogin() {
    clearSecret()
    adding = false
    urlField.text = ""
    formError = ""
  }
  function submitLogin() {
    formError = ""
    try {
      if (!service || !service.canLogin) {
        formError = "Wait for syncing to finish, then enter your +code. Credentials are never queued."
        return
      }
      if (!Model.safeUrl(urlField.text.trim())) {
        formError = "Enter a ship URL, for example https://ship.example."
        return
      }
      if (!codeField.text) { formError = "Enter the ship's +code."; return }
      if (service.login(urlField.text.trim(), codeField.text)) cancelLogin()
      else formError = "Sign-in is unavailable. Wait for the current operation."
    } finally { clearSecret() }
  }
  function reveal(item) {
    if (!opened) return
    var p = item.mapToItem(content, 0, 0)
    if (p.y < flick.contentY) flick.contentY = Math.max(0, p.y)
    else if (p.y + item.height > flick.contentY + flick.height)
      flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, p.y + item.height - flick.height))
  }
  onOpenedChanged: { cancelLogin(); if (opened && service) service.refresh() }
  onServiceChanged: cancelLogin()
  onVisibleChanged: if (!visible) cancelLogin()
  onAddingChanged: if (!adding) clearSecret()
  Component.onDestruction: clearSecret()
  Connections {
    target: root.service
    function onCanLoginChanged() { if (!root.service.canLogin) root.clearSecret() }
  }

  Ui.BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "~"
    active: root.opened || root.account.ships.some(function(row) { return Model.canAuto(row) })
    tooltipText: "Urbit Theme: " + (root.service ? root.service.statusText : "Loading")
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton || buttonCode === Qt.MiddleButton) {
        if (root.service) root.service.refresh()
      } else root.toggle()
    }
  }
  Ui.KeyboardPanel {
    owner: root
    anchorItem: button
    bar: root.bar
    open: root.opened
    focusTarget: form
    contentWidth: fittedContentWidth(Style.space(440))
    contentHeight: fittedContentHeight(content.implicitHeight, Style.space(720))
    FocusScope {
      id: form
      anchors.fill: parent
      Keys.onEscapePressed: root.close()
      Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }
        Column {
          id: content
          width: flick.width
          spacing: Style.space(12)
          Ui.PanelSectionHeader { objectName: "paletteSection"; text: "OMARCHY PALETTE" }
          Body {
            text: root.currentPalette ? root.currentPalette.name + (root.currentPalette.dark ? " / dark" : " / light")
              : "Palette preview is unavailable."
          }
          RowLayout {
            width: parent.width
            spacing: Style.space(6)
            visible: !!root.currentPalette
            Repeater {
              model: ["primary", "secondary", "tertiary", "background", "surface"]
              ColumnLayout {
                required property string modelData
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                Rectangle {
                  Layout.fillWidth: true
                  height: Style.space(30)
                  color: root.currentPalette ? root.currentPalette[modelData] : "transparent"
                  border.width: 1
                  border.color: root.foreground
                  radius: Style.cornerRadius
                }
                Text {
                  Layout.fillWidth: true
                  text: modelData
                  textFormat: Text.PlainText
                  color: root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }
            }
          }
          Action {
            objectName: "refresh"
            text: "Refresh Preview"
            enabled: !!root.service
            tooltipText: "Refresh palette and ship status without publishing"
            onClicked: if (root.service) root.service.refresh()
          }
          Ui.PanelSeparator { width: parent.width }
          Ui.PanelSectionHeader { objectName: "shipsSection"; text: "SHIPS" }
          Body { visible: root.ready && !root.account.ships.length; text: "No ships added." }
          Repeater {
            model: root.account.ships
            Column {
              id: shipRow
              required property var modelData
              width: content.width
              spacing: Style.space(4)
              RowLayout {
                width: parent.width
                Text {
                  objectName: "shipName-" + shipRow.modelData.id
                  Layout.fillWidth: true
                  text: shipRow.modelData.ship
                  textFormat: Text.PlainText
                  color: root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.bodySmall
                  elide: Text.ElideRight
                  property string tooltipText: shipRow.modelData.url
                  HoverHandler { id: shipHover }
                  Controls.ToolTip {
                    objectName: "shipUrlTooltip-" + shipRow.modelData.id
                    visible: shipHover.hovered
                    text: shipRow.modelData.url
                  }
                  Accessible.name: text
                }
                Action {
                  id: syncButton
                  objectName: "automatic-" + shipRow.modelData.id
                  text: Model.toggleIcon(shipRow.modelData)
                  implicitWidth: Style.space(36)
                  implicitHeight: Style.space(36)
                  tooltipText: Model.toggleLabel(shipRow.modelData) + " for " + shipRow.modelData.ship
                  Accessible.name: tooltipText
                  enabled: root.ready && !root.service.rowBusy(shipRow.modelData) && !shipRow.modelData.authenticationRequired
                  onClicked: root.service.control(shipRow.modelData.automatic ? "pause" : "enable", shipRow.modelData)
                  Row {
                    objectName: "pauseBars-" + shipRow.modelData.id
                    anchors.centerIn: parent
                    spacing: Style.space(4)
                    visible: shipRow.modelData.automatic
                    Rectangle { width: Style.space(3); height: Style.space(13); color: root.foreground }
                    Rectangle { width: Style.space(3); height: Style.space(13); color: root.foreground }
                  }
                }
                Action {
                  objectName: "disconnect-" + shipRow.modelData.id
                  text: "X"
                  tooltipText: "Remove " + shipRow.modelData.ship + ": log out and forget this ship"
                  Accessible.name: tooltipText
                  enabled: root.ready && !root.service.rowBusy(shipRow.modelData)
                  onClicked: root.service.control("disconnect", shipRow.modelData)
                }
              }
              Body {
                objectName: "shipStatus-" + shipRow.modelData.id
                text: root.service ? root.service.rowStatus(shipRow.modelData) : ""
                visible: text !== ""
                font.pixelSize: Style.font.caption
              }
            }
          }
          Body {
            visible: text !== ""
            color: Color.urgent
            text: root.formError || (root.service ? root.service.lastError : "")
          }
          Action {
            objectName: "addUrbit"
            text: "+ urbit"
            visible: !root.adding
            enabled: root.ready && root.account.ships.length < 64
            onClicked: { root.adding = true; urlField.forceActiveFocus() }
          }
          Column {
            width: parent.width
            spacing: Style.space(8)
            visible: root.adding
            onVisibleChanged: if (!visible) root.clearSecret()
            Body { text: "Add & Sync enables this same palette on all Talon clients for this ship and disables Talon's separate accent override." }
            Body { text: "Ship URL" }
            Ui.TextField {
              id: urlField
              objectName: "shipUrl"
              width: parent.width
              placeholderText: "https://ship.example"
              maximumLength: 2048
              inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoPredictiveText
              Accessible.name: "Ship URL"
              onAccepted: if (codeField.enabled) codeField.forceActiveFocus()
              onActiveFocusChanged: if (activeFocus) root.reveal(this)
            }
            Body { text: "+code" }
            Ui.TextField {
              id: codeField
              objectName: "shipCode"
              width: parent.width
              enabled: !!root.service && root.service.canLogin
              password: true
              passwordMaskDelay: 0
              maximumLength: 512
              placeholderText: "Your ship's +code"
              inputMethodHints: Qt.ImhHiddenText | Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
              Accessible.name: "Ship +code"
              onAccepted: root.submitLogin()
              onActiveFocusChanged: if (activeFocus) root.reveal(this)
              onVisibleChanged: if (!visible) text = ""
              Component.onDestruction: text = ""
            }
            Body {
              text: root.service && root.service.canLogin ? "Your +code is not saved. The session is stored in Secret Service."
                : "Wait for syncing to finish before entering +code. Credentials are never queued."
            }
            Flow {
              width: parent.width
              spacing: Style.space(8)
              Action {
                objectName: "signIn"
                text: "Add & Sync"
                enabled: !!root.service && root.service.canLogin
                onClicked: root.submitLogin()
              }
              Action { objectName: "cancelLogin"; text: "Cancel"; onClicked: root.cancelLogin() }
            }
          }
          Body {
            visible: root.account.ships.length > 0
            font.pixelSize: Style.font.caption
            text: "Pause then resume to resend the palette. Removing a ship logs out and forgets its session; published Talon settings stay unchanged. A running operation finishes first."
          }
        }
      }
    }
  }
  component Body: Text {
    width: parent.width
    textFormat: Text.PlainText
    wrapMode: Text.WordWrap
    color: root.foreground
    font.family: Style.font.family
    font.pixelSize: Style.font.bodySmall
  }
  component Action: Ui.Button {
    focusable: true
    bordered: true
    opacity: enabled ? 1 : 0.45
    onActiveFocusChanged: if (activeFocus) root.reveal(this)
  }
}
