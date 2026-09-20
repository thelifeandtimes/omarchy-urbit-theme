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

  // The shell injects this after creation; an absent service must be inert.
  property var service: bar && bar.shell && typeof bar.shell.serviceFor === "function"
    ? bar.shell.serviceFor(moduleName) : null
  readonly property var account: service ? service.account : Model.emptyState()
  readonly property var currentPalette: service ? service.currentPalette : null
  readonly property bool ready: !!service && service.loaded
  readonly property bool busy: !!service && service.busy
  readonly property color foreground: Color.foreground
  readonly property string statusText: service ? service.statusText : "Urbit Theme service is loading"
  property string formError: ""
  property bool consent: false
  readonly property string accountIdentity: JSON.stringify(Model.identity(account))

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function clearSecret() {
    // Assignment, not clear()/remove(): Qt also resets the undo history.
    if (codeField) codeField.text = ""
  }
  function submitLogin() {
    formError = ""
    try {
      if (!service || !service.canLogin) return
      if (!Model.safeUrl(urlField.text.trim()) || !urlField.text.trim()) {
        formError = "Enter a ship URL, for example https://ship.example."
        return
      }
      if (!codeField.text) { formError = "Enter the ship's +code."; return }
      if (!service.login(urlField.text.trim(), codeField.text)) formError = "Sign-in is unavailable. Wait for the current operation."
    } finally { clearSecret() }
  }
  function reveal(item) {
    if (!opened) return
    var p = item.mapToItem(content, 0, 0)
    if (p.y < flick.contentY) flick.contentY = Math.max(0, p.y)
    else if (p.y + item.height > flick.contentY + flick.height)
      flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, p.y + item.height - flick.height))
  }

  onOpenedChanged: {
    clearSecret()
    formError = ""
    consent = false
    if (opened && service) service.refresh()
  }
  onServiceChanged: { clearSecret(); consent = false }
  onAccountIdentityChanged: { clearSecret(); consent = false; formError = "" }
  onVisibleChanged: if (!visible) { clearSecret(); consent = false }
  Component.onDestruction: clearSecret()

  Connections {
    target: root.service
    ignoreUnknownSignals: true
    function onConsentInvalidated() { root.clearSecret(); root.consent = false }
  }

  Ui.BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "~"
    active: root.opened || (root.account.automatic && !root.account.authenticationRequired)
    tooltipText: "Urbit Theme: " + root.statusText
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton || buttonCode === Qt.MiddleButton) {
        if (root.service) root.service.refresh()
      } else root.toggle()
    }
  }

  Ui.KeyboardPanel {
    id: panel
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
      // Do not use PanelKeyCatcher: Tab must traverse inputs, not switch panels.
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

          Ui.PanelHero {
            width: parent.width
            title: "Urbit Theme"
            meta: root.statusText
            detail: root.account.connected ? root.account.ship : "CONNECT YOUR SHIP"
            foreground: root.foreground
            fontFamily: Style.font.family
          }

          Body {
            visible: root.account.connected
            text: root.account.ship + "\n" + root.account.url
          }
          Body {
            visible: text !== ""
            color: Color.urgent
            text: root.formError || (root.service ? root.service.lastError : "")
          }
          Body {
            visible: root.account.authenticationRequired
            text: "Your session needs renewal. Disconnect below, then sign in again."
          }
          Body {
            visible: root.account.pending || (!!root.service && root.service.retrying)
            text: root.service && root.service.retrying ? "Publication pending. A bounded automatic retry is scheduled."
              : "Publication pending. Publish Now to try again, or Pause to cancel automatic intent."
          }

          Column {
            visible: !root.account.connected
            onVisibleChanged: if (!visible) root.clearSecret()
            width: parent.width
            spacing: Style.space(8)
            Ui.PanelSectionHeader { text: "SHIP SIGN-IN" }
            Body { text: "Ship URL" }
            Ui.TextField {
              id: urlField
              objectName: "shipUrl"
              width: parent.width
              enabled: !!root.service && root.service.canLogin
              placeholderText: "https://ship.example"
              maximumLength: 2048
              inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoPredictiveText
              Accessible.name: "Ship URL"
              onAccepted: codeField.forceActiveFocus()
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
            Action {
              objectName: "signIn"
              text: "Sign In"
              enabled: !!root.service && root.service.canLogin
              onClicked: root.submitLogin()
            }
            Body { text: "Sign-in does not publish. Only the session is stored in Secret Service; your +code is not saved." }
          }

          Ui.PanelSeparator { width: parent.width }
          Ui.PanelSectionHeader { text: "CURRENT OMARCHY PALETTE" }
          Body { text: root.currentPalette ? root.currentPalette.name + (root.currentPalette.dark ? " / dark" : " / light") : "Palette preview is unavailable." }
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
                Text {
                  Layout.fillWidth: true
                  text: root.currentPalette ? root.currentPalette[modelData] : ""
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
            text: "Refresh Preview / Status"
            enabled: !!root.service && !root.busy
            onClicked: if (root.service) root.service.refresh()
          }

          Ui.PanelSeparator { width: parent.width }
          Ui.PanelSectionHeader { text: "PUBLISHING CONSENT" }
          Body {
            text: "Publishing changes Talon-specific settings stored on your ship and shared across your Talon devices. It is not a local-device override. Native Tlon and other ship apps do not use this namespace. Publishing also disables Talon's separate accent override."
          }
          Action {
            objectName: "consent"
            width: parent.width
            text: root.consent ? "[x] Apply across my Talon devices" : "[ ] Apply across my Talon devices"
            selected: root.consent
            enabled: root.ready && root.account.connected && !root.busy
            onClicked: root.consent = !root.consent
          }
          Flow {
            width: parent.width
            spacing: Style.space(8)
            Action {
              objectName: "publish"
              text: "Publish Now"
              enabled: root.ready && root.account.connected && !root.account.authenticationRequired && !root.busy && root.consent && !!root.currentPalette
              onClicked: if (root.service && root.consent) root.service.control("publish")
            }
            Action {
              objectName: "automatic"
              text: root.account.automatic ? "Pause" : "Enable Auto"
              enabled: root.ready && root.account.connected && !root.service.stopping
                && (root.account.automatic || (!root.busy && root.consent && !root.account.authenticationRequired && !!root.currentPalette))
              onClicked: if (root.service) root.service.control(root.account.automatic ? "pause" : "enable")
            }
            Action {
              objectName: "disconnect"
              text: "Disconnect"
              enabled: root.ready && root.account.connected && !root.service.stopping
              onClicked: if (root.service) { root.consent = false; root.service.control("disconnect") }
            }
          }
          Body { text: "Auto publishes this palette now and follows installed theme-change hooks. If Talon missed an update while connecting, Publish Now resends it even when the ship already has this palette. Pause cancels queued publications and retries. A running operation finishes first. Disconnect forgets the local session; it leaves published Talon settings unchanged." }
          Body {
            visible: root.account.lastPublished !== ""
            text: "Last confirmed on ship: " + root.account.lastPublished + "\n" + root.account.lastTheme
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
