import QtQuick
import QtTest
import "../.." as Plugin
import "../../Model.js" as Model

Item {
  width: 480
  height: 760

  Component { id: panelComponent; Plugin.Panel {} }
  Component { id: serviceComponent; Plugin.Service {} }
  QtObject {
    id: fake
    property var account: Model.emptyState()
    property var currentPalette: null
    property bool loaded: true
    property bool busy: false
    property bool canLogin: true
    property bool stopping: false
    property bool retrying: false
    property string lastError: ""
    property string statusText: "Fixture ship"
    property int logins: 0
    property int refreshes: 0
    property string lastControl: ""
    function refresh() { refreshes++ }
    function login(url, code) { logins++; return true }
    function control(action) { lastControl = action }
  }

  TestCase {
    name: "UrbitTheme"
    when: windowShown
    property var widget: null
    property var service: null

    function init() {
      fake.account = Model.emptyState()
      fake.currentPalette = null
      fake.busy = false
      fake.canLogin = true
      fake.logins = 0
      fake.refreshes = 0
      fake.lastControl = ""
      widget = panelComponent.createObject(parent)
      verify(widget !== null)
    }
    function cleanup() {
      widget.destroy()
      if (service) { service.destroy(); service = null }
      wait(0)
    }
    function control(name) { return findChild(widget, name) }
    function connected(automatic) {
      return { connected: true, ship: "~zod", url: "https://ship.example", automatic: automatic,
        pending: false, authenticationRequired: false, lastPublished: "", lastTheme: "", lastError: "" }
    }
    function response(account, palette) {
      return JSON.stringify({ schemaVersion: 1, ok: true, state: account, palette: palette || null, error: null })
    }
    function test_nullServiceAndLateInjection() {
      widget.open()
      wait(30)
      verify(!control("signIn").enabled)
      verify(!control("refresh").enabled)
      verify(!control("publish").enabled)
      widget.submitLogin()
      compare(fake.logins, 0)
      widget.service = fake
      tryCompare(control("signIn"), "enabled", true)
      widget.close()
      widget.open()
      compare(fake.refreshes, 1)
    }
    function test_secretMaskClearUndoAndKeyboard() {
      widget.service = fake
      widget.open()
      wait(30)
      var url = control("shipUrl"), code = control("shipCode")
      url.text = "https://ship.example"
      url.forceActiveFocus()
      keyClick(Qt.Key_Tab)
      verify(code.activeFocus)
      compare(code.echoMode, TextInput.Password)
      verify((code.inputMethodHints & Qt.ImhSensitiveData) !== 0)
      keyClick(Qt.Key_A)
      verify(code.text.length > 0)
      keyClick(Qt.Key_Return)
      compare(fake.logins, 1)
      compare(code.text, "")
      verify(!code.canUndo)
      code.text = "fixture-not-a-real-code"
      widget.close()
      compare(code.text, "")
      verify(!code.canUndo)
      widget.open()
      wait(30)
      code.forceActiveFocus()
      keyClick(Qt.Key_Escape)
      compare(widget.opened, false)
    }
    function test_invalidLoginAlsoClearsSecret() {
      widget.service = fake
      widget.open()
      control("shipUrl").text = "https://user:password@ship.example"
      control("shipCode").text = "fixture-not-a-real-code"
      widget.submitLogin()
      compare(fake.logins, 0)
      compare(control("shipCode").text, "")
      verify(widget.formError.length > 0)
    }
    function test_consentAndControls() {
      widget.service = fake
      fake.account = connected(false)
      fake.currentPalette = { name: "Fixture", dark: true, primary: "#123456", secondary: "#abcdef",
        tertiary: "#987654", background: "#112233", surface: "#223344" }
      widget.open()
      wait(30)
      verify(!control("publish").enabled)
      verify(!control("automatic").enabled)
      control("consent").forceActiveFocus()
      keyClick(Qt.Key_Space)
      verify(widget.consent)
      verify(control("publish").enabled)
      control("publish").forceActiveFocus()
      keyClick(Qt.Key_Return)
      compare(fake.lastControl, "publish")
      fake.account = connected(true)
      fake.busy = true
      verify(control("automatic").enabled)
      verify(control("disconnect").enabled)
      verify(!control("publish").enabled)
      control("automatic").clicked()
      compare(fake.lastControl, "pause")
      widget.close()
      verify(!widget.consent)
    }
    function test_serviceStdinAndPausedHook() {
      service = serviceComponent.createObject(parent)
      verify(service !== null)
      wait(20)
      var process = findChild(service, "urbitThemeHelper")
      verify(process !== null)
      compare(service.queue.running, "status")
      verify(process.handedOff)
      verify(process.closedAfterWrite)
      compare(service.pendingInput, "")
      compare(process.command[0], "python3")
      compare(process.command[1], "-B")
      verify(!process.command[2].startsWith("file:"))
      process.respond(response(connected(false)), 0)
      wait(20)
      compare(service.queue.running, "preview")
      process.respond(response(connected(false)), 0)
      wait(20)
      verify(!service.busy)
      service.themeChanged()
      service.themeChanged()
      wait(400)
      compare(service.queue.running, "preview")
      process.respond(response(connected(false)), 0)
      wait(20)
      verify(!service.busy)
      compare(process.launches, 3)
    }
    function test_serviceLoginIsImmediateAndNeverQueued() {
      service = serviceComponent.createObject(parent)
      wait(20)
      var process = findChild(service, "urbitThemeHelper")
      verify(!service.login("https://ship.example", "fixture-not-a-real-code"))
      process.respond(response(Model.emptyState()), 0)
      wait(20)
      process.respond(response(Model.emptyState()), 0)
      wait(20)
      verify(service.canLogin)
      verify(service.login("https://ship.example", "fixture-not-a-real-code"))
      verify(!service.login("https://ship.example", "must-not-be-queued"))
      verify(!JSON.stringify(service.queue).includes("fixture-not-a-real-code"))
      wait(20)
      compare(service.pendingInput, "")
      compare(process.command.length, 4)
      compare(process.command[3], "login")
      process.respond(response(connected(false)), 0)
      wait(20)
      verify(!service.busy)
      compare(service.account.automatic, false)
      compare(process.launches, 3)
    }
    function test_serviceBoundsOutput() {
      service = serviceComponent.createObject(parent)
      wait(20)
      var process = findChild(service, "urbitThemeHelper")
      process.stdout.read("x".repeat(Model.maxOutput + 1))
      compare(service.lastError, Model.errorText("output_limit"))
      compare(service.output, "")
    }
    function test_identityTransitionAndVisibilityClearSecretAndConsent() {
      widget.service = fake
      widget.open()
      var code = control("shipCode")
      code.text = "fixture-not-a-real-code"
      fake.account = connected(false)
      compare(code.text, "")
      verify(!code.visible)
      widget.consent = true
      code.text = "hidden-fixture-code"
      var b = connected(false)
      b.ship = "~nec"
      fake.account = b
      compare(code.text, "")
      verify(!code.canUndo)
      verify(!widget.consent)
      widget.consent = true
      code.text = "hidden-fixture-code"
      widget.visible = false
      compare(code.text, "")
      verify(!widget.consent)
    }
    function test_nullStatePreservesTrustedAccountAndNormalExitOrdering() {
      service = serviceComponent.createObject(parent)
      wait(20)
      var process = findChild(service, "urbitThemeHelper")
      process.stoppedBeforeExit = true
      var failure = JSON.stringify({ schemaVersion: 1, ok: false, state: null, palette: null,
        error: { code: "busy", message: "Fixture busy", retryable: true } })
      process.respond(failure, 1)
      verify(!service.loaded)
      compare(service.lastError, Model.errorText("busy"))
      wait(20)
      compare(service.queue.running, "preview")
      process.respond(response(connected(false)), 0)
      wait(20)
      if (service.queue.running === "status") { process.respond(response(connected(false)), 0); wait(20) }
      verify(service.loaded)
      service.currentPalette = { name: "Last trusted preview" }
      service.refresh()
      wait(20)
      process.respond(failure, 1)
      compare(service.account.ship, "~zod")
      verify(service.loaded)
      wait(20)
      compare(service.queue.running, "preview")
      process.respond(failure, 1)
      wait(20)
      verify(!service.busy)
      compare(service.currentPalette.name, "Last trusted preview")
      compare(service.lastError, Model.errorText("busy"))
    }
    function test_allMutationsCarryCapturedIdentity() {
      service = serviceComponent.createObject(parent)
      wait(20)
      var process = findChild(service, "urbitThemeHelper")
      process.respond(response(connected(false)), 0)
      wait(20)
      process.respond(response(connected(false)), 0)
      wait(20)
      for (var i = 0; i < 4; i++) {
        var action = ["publish", "enable", "pause", "disconnect"][i]
        service.control(action)
        wait(20)
        compare(process.expectedAccount.url, "https://ship.example")
        compare(process.expectedAccount.ship, "~zod")
        process.respond(response(connected(action === "enable")), 0)
        wait(20)
        if (service.queue.running === "sync") {
          compare(process.expectedAccount.ship, "~zod")
          process.respond(response(connected(true)), 0)
          wait(20)
        }
      }
    }
    function test_statusSwitchCannotRetargetQueuedPublish() {
      service = serviceComponent.createObject(parent)
      wait(20)
      var process = findChild(service, "urbitThemeHelper")
      process.respond(response(connected(false)), 0)
      wait(20)
      process.respond(response(connected(false)), 0)
      wait(20)
      service.queue = Model.request(service.queue, "publish", service.account)
      service.refresh()
      wait(20)
      var b = connected(false)
      b.url = "https://other.example"
      process.respond(response(b), 0)
      wait(20)
      compare(service.queue.running, "preview")
      process.respond(response(b), 0)
      wait(20)
      verify(!service.busy)
      verify(!service.queue.force)
      compare(process.launches, 4)
    }
    function test_accountChangedWithoutStateRevokesConsentAndObservesAgain() {
      service = serviceComponent.createObject(parent)
      wait(20)
      var process = findChild(service, "urbitThemeHelper")
      process.respond(response(connected(false)), 0)
      wait(20)
      process.respond(response(connected(false)), 0)
      wait(20)
      widget.service = service
      widget.consent = true
      control("shipCode").text = "hidden-fixture-code"
      service.control("publish")
      wait(20)
      process.respond(JSON.stringify({ schemaVersion: 1, ok: false, state: null, palette: null,
        error: { code: "account-changed", message: "Fixture changed", retryable: false } }), 1)
      verify(!widget.consent)
      compare(control("shipCode").text, "")
      compare(service.account.ship, "~zod")
      wait(20)
      compare(service.queue.running, "status")
      var b = connected(true)
      b.ship = "~nec"
      process.respond(response(b), 0)
      wait(20)
      verify(!service.busy)
      compare(service.account.ship, "~nec")
      verify(!service.queue.sync)
    }
  }
}
