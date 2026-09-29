import QtQuick
import QtTest
import "../.." as Plugin
import "../../Model.js" as Model

Item {
  width: 480
  height: 760
  Component { id: panelComponent; Plugin.Panel {} }
  Component { id: serviceComponent; Plugin.Service { desktopEnabled: false } }
  QtObject {
    id: fake
    property var account: Model.emptyState()
    property var currentPalette: null
    property bool loaded: true
    property bool busy: false
    property bool canLogin: true
    property string lastError: ""
    property string statusText: "Fixture ships"
    property bool desktopEnabled: false
    property var desktopState: ({status: "Following shared appearance", error: "", hub: null, paused: false, theme: "", bridgeErrors: {}})
    property var lastDesktopCommand: null
    function desktopCommand(value) { lastDesktopCommand = value }
    property int logins: 0
    property int refreshes: 0
    property string lastControl: ""
    property var lastAccount: null
    function refresh() { refreshes++ }
    function login(url, code) { logins++; return true }
    function control(action, row) { lastControl = action; lastAccount = Model.identity(row) }
    function rowBusy(row) { return false }
    function rowStatus(row) { return row.authenticationRequired ? "Session expired. Remove this ship, then add it again." : "" }
  }
  TestCase {
    name: "UrbitTheme"
    when: windowShown
    property var widget: null
    property var service: null
    property var palette: ({ id: "omarchy-urbit-theme", name: "Fixture", dark: true, primary: "#123456",
      secondary: "#abcdef", tertiary: "#987654", background: "#112233", surface: "#223344" })
    function row(id, automatic) {
      return { id: id.repeat(64), ship: id === "a" ? "~zod" : "~nec", url: "https://" + id + ".example",
        automatic: automatic, pending: automatic, authenticationRequired: false,
        lastPublished: "", lastTheme: "", lastError: "" }
    }
    function state(rows) { return { ships: rows || [] } }
    function response(s, p, error, warning) {
      return JSON.stringify({ schemaVersion: 2, ok: !error, state: s, palette: p || null,
        error: error || null, warning: warning || null })
    }
    function fail(code, retryable) { return { code: code, message: "Fixture failure", retryable: !!retryable } }
    function init() {
      fake.account = Model.emptyState()
      fake.currentPalette = palette
      fake.busy = false
      fake.canLogin = true
      fake.logins = 0
      fake.refreshes = 0
      fake.lastControl = ""
      fake.lastAccount = null
      fake.lastError = ""
      fake.desktopEnabled = false
      fake.desktopState = { status: "Following shared appearance", error: "", hub: null, paused: false, theme: "", bridgeErrors: {} }
      fake.lastDesktopCommand = null
      widget = panelComponent.createObject(parent)
      verify(widget !== null)
    }
    function cleanup() {
      widget.destroy()
      if (service) { service.destroy(); service = null }
      wait(0)
    }
    function control(name) { return findChild(widget, name) }
    function openForm() {
      widget.service = fake
      widget.open()
      wait(30)
      control("addUrbit").clicked()
      wait(10)
    }
    function startService(rows) {
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
      process.respond(response(state(rows)), 0)
      wait(20)
      compare(service.queue.running, "preview")
      process.respond(response(state(rows), palette), 0)
      wait(20)
      return process
    }
    function test_nullServiceAndTwoOrderedSections() {
      widget.open()
      wait(30)
      verify(!control("addUrbit").enabled)
      verify(!control("refresh").enabled)
      verify(control("publish") === null)
      verify(control("consent") === null)
      verify(control("paletteSection").y < control("shipsSection").y)
      widget.submitLogin()
      compare(fake.logins, 0)
      widget.service = fake
      tryCompare(control("addUrbit"), "enabled", true)
      widget.close()
      widget.open()
      compare(fake.refreshes, 1)
    }
    function test_desktopModeNeverPublishesTheJoiningLocalPalette() {
      service = serviceComponent.createObject(parent, { desktopEnabled: true })
      wait(20)
      var helper = findChild(service, "urbitThemeHelper")
      var desktop = findChild(service, "desktopSyncHelper")
      verify(!!desktop)
      compare(desktop.command[3], "serve")
      helper.respond(response(state([row("a", true)])), 0)
      wait(20)
      compare(service.queue.running, "preview")
      helper.respond(response(state([row("a", true)]), palette), 0)
      wait(20)
      compare(service.queue.running, "")
      compare(service.queue.jobs.length, 0)
      var frame = JSON.stringify({status: "Following shared appearance", error: "", hub: Model.identity(row("a", true)),
        paused: false, theme: "tokyo-night", bridgeErrors: {}}) + "\n"
      desktop.stdout.read(frame.substring(0, 20))
      desktop.stdout.read(frame.substring(20))
      compare(service.desktopState.theme, "tokyo-night")
      service.desktopCommand({action: "pause"})
      verify(desktop.handedOff)
      verify(!desktop.closedAfterWrite)
    }
    function test_addCanStartWhileAnotherShipWaitsForRetry() {
      var a = row("a", true)
      var process = startService([a])
      compare(service.queue.running, "sync")
      process.respond(response(state([a]), null, fail("network", true)), 1)
      wait(20)
      compare(service.queue.running, "")
      compare(service.queue.jobs.length, 1)
      verify(service.canLogin)
      verify(service.login("https://b.example", "fixture-code"))
      compare(service.queue.running, "login")
      compare(service.queue.jobs.length, 1)
      wait(20)
      compare(service.pendingInput, "")
      verify(!JSON.stringify(service.queue).includes("fixture-code"))
    }
    function test_secretMaskClearUndoAndKeyboard() {
      openForm()
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
      verify(!widget.adding)
      control("addUrbit").clicked()
      code.text = "fixture-not-a-real-code"
      widget.close()
      compare(code.text, "")
      verify(!code.canUndo)
      widget.open()
      control("addUrbit").clicked()
      wait(30)
      code.forceActiveFocus()
      keyClick(Qt.Key_Escape)
      compare(widget.opened, false)
    }
    function test_invalidCancelInvisibleAndBusyClearCredentials() {
      openForm()
      var code = control("shipCode")
      control("shipUrl").text = "https://user:password@ship.example"
      code.text = "fixture-not-a-real-code"
      widget.submitLogin()
      compare(fake.logins, 0)
      compare(code.text, "")
      verify(widget.formError.length > 0)
      code.text = "fixture"
      fake.canLogin = false
      compare(code.text, "")
      verify(!code.enabled)
      fake.canLogin = true
      code.text = "fixture"
      control("cancelLogin").clicked()
      compare(code.text, "")
      compare(control("shipUrl").text, "")
      control("addUrbit").clicked()
      code.text = "fixture"
      widget.visible = false
      compare(code.text, "")
      verify(!code.canUndo)
      verify(!widget.adding)
    }
    function test_actualInverseIconsTooltipsAndAddWhileConnected() {
      var a = row("a", true), b = row("b", false)
      fake.account = state([a, b])
      openForm()
      var pause = control("automatic-" + a.id), sync = control("automatic-" + b.id)
      compare(pause.text, "")
      verify(control("pauseBars-" + a.id).visible)
      verify(pause.tooltipText.startsWith("Pause automatic syncing for ~zod"))
      compare(pause.Accessible.name, pause.tooltipText)
      compare(sync.text, "\u25b6")
      verify(!control("pauseBars-" + b.id).visible)
      verify(sync.tooltipText.startsWith("Resume automatic syncing for ~nec"))
      compare(sync.Accessible.name, sync.tooltipText)
      var name = control("shipName-" + a.id)
      compare(name.text, "~zod")
      compare(name.tooltipText, a.url)
      var tooltip = null
      for (var i = 0; i < name.resources.length; i++)
        if (name.resources[i].objectName === "shipUrlTooltip-" + a.id) tooltip = name.resources[i]
      verify(tooltip !== null)
      verify(!tooltip.visible)
      mouseMove(name, name.width / 2, name.height / 2)
      tryCompare(tooltip, "visible", true)
      compare(tooltip.text, a.url)
      mouseMove(control("refresh"), 1, 1)
      tryCompare(tooltip, "visible", false)
      verify(!control("shipStatus-" + a.id).text.includes(a.url))
      compare(control("disconnect-" + a.id).text, "×")
      compare(pause.width, control("disconnect-" + a.id).width)
      compare(pause.height, control("disconnect-" + a.id).height)
      compare(sync.width, pause.width)
      compare(sync.height, pause.height)
      verify(control("disconnect-" + a.id).tooltipText.includes("log out and forget"))
      fake.busy = true
      verify(pause.enabled)
      verify(control("disconnect-" + b.id).enabled)
      pause.clicked()
      compare(fake.lastControl, "pause")
      compare(fake.lastAccount.id, a.id)
      sync.clicked()
      compare(fake.lastControl, "enable")
      compare(fake.lastAccount.id, b.id)
      control("shipUrl").text = "https://third.example"
      control("shipCode").text = "fixture"
      widget.submitLogin()
      compare(fake.logins, 1)
      verify(!widget.adding)
    }
    function test_expiredRowAllowsRemoveNotResume() {
      var a = row("a", false)
      a.authenticationRequired = true
      fake.account = state([a])
      widget.service = fake
      widget.open()
      wait(20)
      verify(!control("automatic-" + a.id).enabled)
      verify(control("disconnect-" + a.id).enabled)
      verify(control("shipStatus-" + a.id).text.includes("Session expired"))
      control("disconnect-" + a.id).clicked()
      compare(fake.lastControl, "disconnect")
    }
    function test_desktopSwitchSelectsOneShipAndPausesOrResumesItsHub() {
      var a = row("a", true), b = row("b", false)
      fake.account = state([a, b])
      fake.desktopEnabled = true
      fake.desktopState = { status: "Following shared appearance", error: "", hub: Model.identity(a), paused: false, theme: "Fixture" }
      widget.service = fake
      widget.open()
      wait(30)
      var first = control("desktop-" + a.id), second = control("desktop-" + b.id)
      verify(first.checked)
      verify(!second.checked)
      compare(first.width, control("disconnect-" + a.id).width)
      compare(first.height, control("disconnect-" + a.id).height)
      first.forceActiveFocus()
      keyClick(Qt.Key_Space)
      compare(fake.lastDesktopCommand.action, "pause")
      fake.desktopState = { status: "Paused", error: "", hub: Model.identity(a), paused: true, theme: "Fixture" }
      verify(!first.checked)
      first.toggled()
      compare(fake.lastDesktopCommand.action, "resume")
      second.toggled()
      compare(fake.lastControl, "enable")
      compare(fake.lastAccount.id, b.id)
      compare(fake.lastDesktopCommand.action, "hub")
      compare(fake.lastDesktopCommand.id, b.id)
      b.automatic = true
      fake.account = state([a, b])
      fake.desktopState = { status: "Following shared appearance", error: "", hub: Model.identity(b), paused: false, theme: "Fixture" }
      verify(!control("desktop-" + a.id).checked)
      verify(control("desktop-" + b.id).checked)
    }
    function test_compactPaletteAndHeaderRetainAccessibleLabels() {
      fake.account = state([row("a", true)])
      widget.service = fake
      widget.open()
      wait(30)
      compare(control("paletteSection").title, "Network Theme")
      verify(control("swatch-muted") === null)
      var first = control("swatch-primary"), last = control("swatch-link")
      compare(first.y, last.y)
      verify(last.x > first.x)
      verify(first.Accessible.name.includes("primary"))
      compare(control("addUrbit").parent, control("shipsHeader"))
    }
    function test_serviceFanoutSamePaletteAndRemoveDuringOtherFlight() {
      var a = row("a", true), b = row("b", true), c = row("c", true)
      var process = startService([a, b, c])
      compare(service.queue.running, "sync")
      compare(process.expectedAccount.id, a.id)
      compare(JSON.stringify(process.palette), JSON.stringify(palette))
      verify(!process.force)
      service.control("disconnect", b)
      compare(process.command[3], "sync")
      process.respond(response(state([a, b, c]), null, fail("network", true)), 1)
      wait(20)
      compare(service.queue.running, "disconnect")
      compare(process.expectedAccount.id, b.id)
      compare(process.expectedAccount.url, b.url)
      compare(process.expectedAccount.ship, b.ship)
      process.respond(response(state([a, c]), null, null, fail("logout-unconfirmed", false)), 0)
      wait(20)
      verify(service.lastError.includes("Cleanup warning"))
      compare(service.queue.running, "sync")
      compare(process.expectedAccount.id, c.id)
      compare(JSON.stringify(process.palette), JSON.stringify(palette))
      process.respond(response(state([a, c])), 0)
      wait(20)
      compare(service.queue.jobs.length, 1)
      compare(service.queue.jobs[0].expectedAccount.id, a.id)
      service.control("pause", a)
      wait(20)
      compare(service.queue.jobs.length, 0)
      a.automatic = false
      a.pending = false
      process.respond(response(state([a, c])), 0)
      wait(20)
      verify(!service.busy)
      verify(Model.idle(service.queue))
    }
    function test_serviceImmediateAddOnlyNewShipAndPauseResumeResends() {
      var a = row("a", false), b = row("b", true)
      var process = startService([a])
      verify(service.canLogin)
      verify(service.login(b.url, "fixture-not-a-real-code"))
      verify(!service.login("https://c.example", "must-not-be-queued"))
      verify(!JSON.stringify(service.queue).includes("fixture-not-a-real-code"))
      wait(20)
      compare(service.pendingInput, "")
      compare(process.command.length, 4)
      compare(process.command[3], "login")
      process.respond(response(state([a, b])), 0)
      wait(20)
      compare(service.queue.running, "sync")
      compare(process.expectedAccount.id, b.id)
      process.respond(response(state([a, b])), 0)
      wait(20)
      service.control("pause", b)
      wait(20)
      compare(process.command[3], "set-auto")
      b.automatic = false
      b.pending = false
      process.respond(response(state([a, b])), 0)
      wait(20)
      service.control("enable", b)
      wait(20)
      b.automatic = true
      b.pending = true
      process.respond(response(state([a, b])), 0)
      wait(20)
      compare(service.queue.running, "sync")
      compare(process.expectedAccount.id, b.id)
      compare(JSON.stringify(process.palette), JSON.stringify(palette))
      process.respond(response(state([a, b])), 0)
      wait(20)
      verify(service.canLogin)
    }
    function test_passiveRefreshPausedHookAndNullTrustedState() {
      var a = row("a", false), process = startService([a])
      process.stoppedBeforeExit = true
      service.refresh()
      process.respond(response(null, null, fail("busy", false)), 1)
      wait(20)
      compare(service.account.ships[0].id, a.id)
      verify(service.loaded)
      process.respond(response(null, null, fail("busy", false)), 1)
      wait(20)
      compare(service.currentPalette.name, palette.name)
      verify(Model.idle(service.queue))
      service.themeChanged()
      service.themeChanged()
      wait(400)
      compare(service.queue.running, "preview")
      process.respond(response(state([a]), palette), 0)
      wait(20)
      verify(Model.idle(service.queue))
      compare(process.launches, 5)
    }
    function test_serviceBoundsOutput() {
      service = serviceComponent.createObject(parent)
      wait(20)
      var process = findChild(service, "urbitThemeHelper")
      verify(!service.login("https://ship.example", "must-not-be-queued"))
      process.stdout.read("x".repeat(Model.maxOutput + 1))
      compare(service.lastError, Model.errorText("output_limit"))
      compare(service.output, "")
    }
    function test_failedRemoveWithCleanupWarningKeepsTrustedRowAndReportsFailure() {
      var a = row("a", false), process = startService([a])
      service.control("disconnect", a)
      wait(20)
      process.respond(response(null, null, fail("state", false), fail("keyring-cleanup", false)), 1)
      wait(20)
      compare(service.account.ships.length, 1)
      compare(service.account.ships[0].id, a.id)
      verify(service.rowStatus(service.account.ships[0]).includes(Model.errorText("state")))
      verify(service.lastError.includes("saved session may remain"))
      verify(!service.lastError.includes("removed locally"))
      verify(Model.idle(service.queue))
    }
    function test_previewRecoveryClearsOnlyItsOperationalError() {
      var a = row("a", false), process = startService([a])
      service.themeChanged()
      wait(400)
      process.respond(response(null, null, fail("busy", true)), 1)
      wait(20)
      compare(service.operationErrors.preview, Model.errorText("busy"))
      verify(service.queue.preview)
      verify(Model.retryDelay(service.queue, Date.now()) > 0)
      service.refresh()
      wait(20)
      compare(service.queue.running, "status")
      process.respond(response(state([a])), 0)
      wait(20)
      compare(service.lastError, Model.errorText("busy"), "status success does not prove palette recovery")
      var q = Model.copy(service.queue)
      q.previewDue = 0
      service.queue = q
      service.pump()
      wait(20)
      compare(service.queue.running, "preview")
      process.respond(response(state([a]), palette), 0)
      wait(20)
      compare(service.lastError, "")
      verify(Model.idle(service.queue))
    }
    function test_startupBusyObservationsRecoverAndFanoutWithoutStatusBlocking() {
      service = serviceComponent.createObject(parent)
      wait(20)
      var process = findChild(service, "urbitThemeHelper")
      process.respond(response(null, null, fail("busy", true)), 1)
      wait(20)
      compare(service.queue.running, "preview")
      process.respond(response(null, null, fail("busy", true)), 1)
      wait(20)
      verify(!service.loaded)
      verify(service.queue.status && service.queue.preview)
      verify(Model.retryDelay(service.queue, Date.now()) > 0)
      var q = Model.copy(service.queue)
      q.previewDue = 0
      service.queue = q
      service.pump()
      wait(20)
      compare(service.queue.running, "preview")
      var a = row("a", true), b = row("b", true)
      process.respond(response(state([a, b]), palette), 0)
      wait(20)
      verify(service.loaded)
      compare(service.lastError, "")
      verify(!service.queue.status)
      for (var i = 0; i < 2; i++) {
        compare(service.queue.running, "sync")
        compare(process.expectedAccount.id, i === 0 ? a.id : b.id)
        compare(JSON.stringify(process.palette), JSON.stringify(palette))
        process.respond(response(state([a, b])), 0)
        wait(20)
      }
      verify(Model.idle(service.queue))
    }
    function test_authoritativeHealthyRowsClearRecoveredErrorsButNullStateDoesNot() {
      var a = row("a", true), b = row("b", true), process = startService([a, b])
      process.respond(response(null, null, fail("network", false)), 1)
      wait(20)
      process.respond(response(null, null, fail("http", false)), 1)
      wait(20)
      compare(service.rowErrors[a.id], Model.errorText("network"))
      compare(service.rowErrors[b.id], Model.errorText("http"))
      service.refresh()
      process.respond(response(null, null, fail("busy", false)), 1)
      wait(20)
      compare(service.rowErrors[a.id], Model.errorText("network"))
      a.pending = false
      b.lastError = "Persisted failure"
      process.respond(response(state([a, b]), palette), 0)
      wait(20)
      verify(!service.rowErrors[a.id])
      compare(service.rowStatus(service.account.ships[0]), "")
      compare(service.rowErrors[b.id], Model.errorText("http"))
      compare(service.lastError, "", "authoritative preview also recovers status observation")
      service.refresh()
      b.pending = false
      b.lastError = ""
      process.respond(response(state([a, b])), 0)
      wait(20)
      verify(!service.rowErrors[b.id])
      compare(service.rowStatus(service.account.ships[1]), "")
      process.respond(response(state([a, b]), palette), 0)
      wait(20)
      verify(Model.idle(service.queue))
    }
    function test_cleanupWarningSurvivesRefreshThemeRecoveryAndSuccessfulLogin() {
      var a = row("a", false), process = startService([a])
      service.control("disconnect", a)
      wait(20)
      process.respond(response(state([]), null, null, fail("logout-unconfirmed", false)), 0)
      wait(20)
      var warning = service.lastError
      verify(warning.includes("Cleanup warning"))
      service.refresh()
      compare(service.lastError, warning)
      process.respond(response(state([])), 0)
      wait(20)
      process.respond(response(state([]), null, fail("palette", false)), 1)
      wait(20)
      verify(service.lastError.includes(Model.errorText("palette")))
      verify(service.lastError.includes(warning))
      service.themeChanged()
      wait(400)
      process.respond(response(state([]), palette), 0)
      wait(20)
      compare(service.lastError, warning)
      verify(service.login(a.url, "fixture-not-a-real-code"))
      wait(20)
      process.respond(response(state([a])), 0)
      wait(20)
      compare(service.lastError, warning)
      verify(Model.idle(service.queue))
    }
  }
}
