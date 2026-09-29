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
  readonly property var desktop: service && service.desktopState ? service.desktopState
    : ({ status: "", error: "", hub: null, paused: false, theme: "" })
  readonly property bool ready: !!service && service.loaded
  readonly property color foreground: Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.4)
  readonly property bool hasError: !!(desktop.error || (service && service.lastError))
  readonly property int controlSize: Style.space(28)
  readonly property var previewColors: ["primary", "secondary", "tertiary", "background", "surface", "text", "raised", "error", "selection", "link"]
  property bool adding: false
  property string formError: ""
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function isHub(row) { return Model.sameAccount(desktop.hub, row) }
  function followsDesktop(row) {
    return !!service && service.desktopEnabled && isHub(row) && !desktop.paused && Model.canAuto(row)
  }
  function toggleDesktop(row) {
    if (!ready || !service.desktopEnabled || row.authenticationRequired || service.rowBusy(row)) return
    if (followsDesktop(row)) service.desktopCommand({ action: "pause" })
    else {
      if (!row.automatic) service.control("enable", row)
      service.desktopCommand(isHub(row) ? { action: "resume" } : { action: "hub", id: row.id })
    }
  }
  function shipDetail(row) {
    var status = service ? service.rowStatus(row) : ""
    if (!service || !service.desktopEnabled || !isHub(row)) return status
    if (desktop.error) return desktop.error
    if (status) return status
    if (desktop.paused) return "Desktop sync paused"
    if (!row.automatic) return "Ship sync paused"
    return desktop.status === "Following shared appearance" ? "Desktops synced" : desktop.status
  }
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
    active: root.opened || root.hasError
    tooltipText: "Network Theme: " + (root.currentPalette ? root.currentPalette.name : "Loading")
    iconComponent: Component {
      ThemeIcon { ink: root.hasError ? Color.urgent : button.foreground }
    }
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
          objectName: "panelContent"
          width: flick.width
          spacing: Style.space(8)
          Ui.PanelHero {
            objectName: "paletteSection"
            title: "Network Theme"
            meta: root.currentPalette ? root.currentPalette.name.replace(/-/g, " ") : "Ship-backed desktop appearance"
            detail: root.currentPalette ? (root.currentPalette.dark ? "DARK" : "LIGHT") : ""
            iconComponent: Component {
              ThemeIcon {
                size: Style.space(36)
                ink: root.hasError ? Color.urgent : root.foreground
                colored: true
                palette: root.currentPalette
              }
            }
            trailingControl: Component {
              IconAction {
                objectName: "refresh"
                glyph: "refresh"
                bordered: false
                enabled: !!root.service
                tooltipText: "Refresh palette and ship status"
                onClicked: if (root.service) root.service.refresh()
              }
            }
          }
          RowLayout {
            objectName: "paletteStrip"
            width: parent.width
            spacing: Style.space(4)
            visible: !!root.currentPalette
            Repeater {
              model: root.previewColors
              Rectangle {
                required property string modelData
                objectName: "swatch-" + modelData
                Layout.fillWidth: true
                Layout.minimumWidth: 1
                Layout.preferredHeight: Style.space(18)
                color: root.currentPalette && root.currentPalette[modelData] ? root.currentPalette[modelData] : "transparent"
                border.width: 1
                border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.22)
                radius: Math.min(Style.cornerRadius, height / 4)
                readonly property string description: modelData + " · " + (root.currentPalette && root.currentPalette[modelData] ? root.currentPalette[modelData] : "Auto")
                Accessible.name: description
                HoverHandler { id: swatchHover }
                Controls.ToolTip { visible: swatchHover.hovered; text: parent.description; delay: 250 }
              }
            }
          }
          Ui.PanelSeparator { width: parent.width }
          RowLayout {
            objectName: "shipsHeader"
            width: parent.width
            Ui.PanelSectionHeader { objectName: "shipsSection"; Layout.fillWidth: true; text: "SHIPS" }
            Action {
              objectName: "addUrbit"
              text: "+ urbit"
              fontSize: Style.font.caption
              horizontalPadding: Style.space(7)
              verticalPadding: Style.space(3)
              visible: !root.adding
              enabled: root.ready && root.account.ships.length < 64
              onClicked: { root.adding = true; urlField.forceActiveFocus() }
            }
          }
          Body { visible: root.ready && !root.account.ships.length; text: "Add a ship to share your desktop appearance."; color: root.dim }
          Repeater {
            model: root.account.ships
            Column {
              id: shipRow
              required property var modelData
              width: content.width
              spacing: Style.space(2)
              RowLayout {
                width: parent.width
                spacing: Style.space(6)
                Text {
                  objectName: "shipName-" + shipRow.modelData.id
                  Layout.fillWidth: true
                  Layout.minimumWidth: 0
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
                Text {
                  visible: !!root.service && root.service.desktopEnabled
                  text: "Sync desktops"
                  textFormat: Text.PlainText
                  color: root.followsDesktop(shipRow.modelData) ? root.foreground : root.dim
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
                Ui.ToggleSwitch {
                  objectName: "desktop-" + shipRow.modelData.id
                  visible: !!root.service && root.service.desktopEnabled
                  Layout.preferredWidth: root.controlSize
                  Layout.preferredHeight: root.controlSize
                  trackHeight: Style.space(14)
                  trackWidth: Style.space(26)
                  cursorPad: Style.space(1)
                  checked: root.followsDesktop(shipRow.modelData)
                  enabled: root.ready && !shipRow.modelData.authenticationRequired && !root.service.rowBusy(shipRow.modelData)
                  activeFocusOnTab: true
                  hasCursor: activeFocus
                  property string tooltipText: "Sync this computer's theme, font and effects through " + shipRow.modelData.ship + ". Only one ship can be selected."
                  Accessible.role: Accessible.CheckBox
                  Accessible.name: "Sync desktops through " + shipRow.modelData.ship
                  Accessible.checked: checked
                  Accessible.onToggleAction: if (enabled) toggled()
                  Keys.onSpacePressed: if (enabled) toggled()
                  Keys.onReturnPressed: if (enabled) toggled()
                  onToggled: root.toggleDesktop(shipRow.modelData)
                  onActiveFocusChanged: if (activeFocus) root.reveal(this)
                  Controls.ToolTip { visible: parent.containsMouse; text: parent.tooltipText; delay: 400 }
                }
                IconAction {
                  objectName: "automatic-" + shipRow.modelData.id
                  text: Model.toggleIcon(shipRow.modelData)
                  tooltipText: Model.toggleLabel(shipRow.modelData) + " for " + shipRow.modelData.ship
                    + ". Includes Talon colors and desktop sync when this ship is selected."
                  enabled: root.ready && !root.service.rowBusy(shipRow.modelData) && !shipRow.modelData.authenticationRequired
                  onClicked: root.service.control(shipRow.modelData.automatic ? "pause" : "enable", shipRow.modelData)
                  Row {
                    objectName: "pauseBars-" + shipRow.modelData.id
                    anchors.centerIn: parent
                    spacing: Style.space(3)
                    visible: shipRow.modelData.automatic
                    Rectangle { width: Style.space(2); height: Style.space(10); color: root.foreground }
                    Rectangle { width: Style.space(2); height: Style.space(10); color: root.foreground }
                  }
                }
                IconAction {
                  objectName: "disconnect-" + shipRow.modelData.id
                  text: "×"
                  tooltipText: "Remove " + shipRow.modelData.ship + ": log out and forget this ship"
                  enabled: root.ready && !root.service.rowBusy(shipRow.modelData)
                  onClicked: root.service.control("disconnect", shipRow.modelData)
                }
              }
              RowLayout {
                width: parent.width
                visible: shipDetail.text !== "" || retryShip.visible
                Text {
                  id: shipDetail
                  objectName: "shipStatus-" + shipRow.modelData.id
                  Layout.fillWidth: true
                  text: root.shipDetail(shipRow.modelData)
                  textFormat: Text.PlainText
                  wrapMode: Text.WordWrap
                  color: root.isHub(shipRow.modelData) && root.desktop.error ? Color.urgent : root.dim
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
                IconAction {
                  id: retryShip
                  objectName: "retry-" + shipRow.modelData.id
                  visible: root.isHub(shipRow.modelData) && (root.desktop.error !== "" || root.desktop.status === "Reconnecting")
                  glyph: "refresh"
                  tooltipText: "Retry desktop sync"
                  onClicked: root.service.desktopCommand({ action: "retry" })
                }
              }
            }
          }
          Body {
            visible: text !== ""
            color: Color.urgent
            text: root.formError || (root.service ? root.service.lastError : "")
              || (!root.account.ships.some(root.isHub) ? root.desktop.error : "")
          }
          Column {
            width: parent.width
            spacing: Style.space(8)
            visible: root.adding
            onVisibleChanged: if (!visible) root.clearSecret()
            Body { text: "The first ship shares this desktop's colors, font and effects—or adopts its existing profile. All added ships receive Talon colors, replacing Talon's accent override." }
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
    Accessible.name: tooltipText || text
    opacity: enabled ? 1 : 0.45
    onActiveFocusChanged: if (activeFocus) root.reveal(this)
  }
  component IconAction: Action {
    id: iconAction
    property string glyph: ""
    implicitWidth: root.controlSize
    implicitHeight: root.controlSize
    Layout.preferredWidth: root.controlSize
    Layout.preferredHeight: root.controlSize
    horizontalPadding: 0
    verticalPadding: 0
    fontSize: Style.font.title
    Canvas {
      anchors.centerIn: parent
      width: Style.space(14)
      height: width
      visible: iconAction.glyph === "refresh"
      property color ink: iconAction.foreground
      antialiasing: true
      onInkChanged: requestPaint()
      onWidthChanged: requestPaint()
      onPaint: {
        var ctx = getContext("2d")
        ctx.reset()
        ctx.clearRect(0, 0, width, height)
        ctx.scale(width / 24, height / 24)
        ctx.strokeStyle = ink
        ctx.lineWidth = 1.8
        ctx.lineCap = "round"
        ctx.lineJoin = "round"
        ctx.beginPath()
        ctx.moveTo(4, 11)
        ctx.bezierCurveTo(4, 6, 8, 3, 12, 3)
        ctx.bezierCurveTo(16, 3, 19, 5, 21, 8)
        ctx.moveTo(21, 3); ctx.lineTo(21, 8); ctx.lineTo(16, 8)
        ctx.moveTo(20, 13)
        ctx.bezierCurveTo(20, 18, 16, 21, 12, 21)
        ctx.bezierCurveTo(8, 21, 5, 19, 3, 16)
        ctx.moveTo(3, 21); ctx.lineTo(3, 16); ctx.lineTo(8, 16)
        ctx.stroke()
      }
    }
  }
}
