import QtQuick
import Quickshell
import Quickshell.Io
import "." as Plugin
import "Model.js" as Model

ShellRoot {
  id: root
  property int themeRequests: 0
  property int previews: 0
  property int statuses: 0
  property int unexpectedActions: 0
  property int previewsDuringDebounce: 0
  property double lastThemeRequest: 0
  property double previewDelay: -1

  Loader {
    id: serviceLoader
    active: true
    sourceComponent: Plugin.Service {
      onQueueChanged: {
        if (queue.generation !== root.themeRequests) {
          root.themeRequests = queue.generation
          root.lastThemeRequest = Date.now()
        }
      }
      onProcessGenerationChanged: {
        if (queue.running === "preview") {
          root.previews++
          if (queue.debouncing) root.previewsDuringDebounce++
          if (root.lastThemeRequest) root.previewDelay = Date.now() - root.lastThemeRequest
        } else if (queue.running === "status") root.statuses++
        else root.unexpectedActions++
      }
    }
  }

  // Observations only: never grant consent, fabricate account state, or publish.
  IpcHandler {
    target: "testControl"
    function snapshot(): string {
      var service = serviceLoader.item
      return JSON.stringify({
        ready: !!service && service.loaded && !!service.currentPalette && Model.idle(service.queue),
        servicePresent: !!service,
        generation: service ? service.queue.generation : -1,
        themeRequests: root.themeRequests,
        previews: root.previews,
        statuses: root.statuses,
        unexpectedActions: root.unexpectedActions,
        previewsDuringDebounce: root.previewsDuringDebounce,
        previewDelay: root.previewDelay,
        disconnected: !!service && !service.account.connected && !service.account.automatic,
        clean: !!service && !service.lastError && service.pendingInput === ""
      })
    }
    function removeService(): string { serviceLoader.active = false; return "removed" }
    function stop(): string { Qt.callLater(Qt.quit); return "stopping" }
  }
}
