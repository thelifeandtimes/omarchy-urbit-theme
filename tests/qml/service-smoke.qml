import QtQuick
import Quickshell
import "." as Plugin
import "Model.js" as Model

ShellRoot {
  Plugin.Service { id: service }
  property int phase: 0
  function resource(name) {
    for (var i = 0; i < service.resources.length; i++)
      if (service.resources[i].objectName === name) return service.resources[i]
    return null
  }
  function begin(action) {
    var q = Model.copy(service.queue)
    q.running = action
    service.queue = q
    service.operationErrors = ({})
    service.launch(action, {})
  }
  function fail() { console.error("SERVICE_SMOKE_FAILED_PHASE_" + phase); Qt.quit() }
  Timer {
    interval: 25
    repeat: true
    running: true
    onTriggered: {
      if (!service.loaded || service.busy || !service.currentPalette || service.queue.debouncing) return
      if (service.pendingInput !== "") { console.error("SERVICE_SMOKE_BUFFER_NOT_CLEARED"); Qt.quit(); return }
      if (phase === 0) {
        phase = 1
        service.currentPalette = null
        service.themeChanged()
        service.themeChanged()
      } else if (phase === 1) {
        if (service.lastError) { fail(); return }
        phase = 2
        begin("fixture-failure")
      } else if (phase === 2) {
        if (service.lastError !== Model.errorText("busy")) { fail(); return }
        var helper = resource("urbitThemeHelper")
        if (!helper) { fail(); return }
        phase = 3
        var q = Model.copy(service.queue)
        q.running = "fixture-failstart"
        service.queue = q
        service.processGeneration++
        service.operationErrors = ({})
        helper.command = ["/nonexistent-urbit-theme-fixture-command"]
        helper.running = true
      } else if (phase === 3) {
        if (service.lastError !== Model.errorText("helper_unavailable")) { fail(); return }
        var watchdog = resource("helperWatchdog")
        if (!watchdog) { fail(); return }
        watchdog.interval = 75
        phase = 4
        begin("fixture-timeout")
      } else if (phase === 4) {
        if (service.lastError !== Model.errorText("helper_timeout")) { fail(); return }
        resource("helperWatchdog").interval = 120000
        phase = 5
        service.operationErrors = ({})
        service.refresh()
      } else {
        if (service.lastError) { fail(); return }
        console.log("SERVICE_SMOKE_PASS")
        Qt.quit()
      }
    }
  }
}
