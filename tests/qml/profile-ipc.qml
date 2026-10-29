import QtQuick
import Quickshell
import Quickshell.Io
import "stable" as Stable
import "dev" as Dev
import "edge" as Edge

ShellRoot {
  id: root
  Stable.Service { id: stable; desktopEnabled: false; manifest: ({id: "thelifeandtimes.urbit-theme"}) }
  Dev.Service { id: dev; desktopEnabled: false }
  Edge.Service { id: edge; desktopEnabled: false }
  function state(service) {
    return {target: service.ipcTarget, ready: service.loaded && !!service.currentPalette && !service.busy && !service.queue.debouncing,
      generation: service.queue.generation, ships: service.account.ships.length, error: service.lastError}
  }
  IpcHandler {
    target: "profileTest"
    function snapshot(): string {
      return JSON.stringify({stable: root.state(stable), dev: root.state(dev), edge: root.state(edge)})
    }
    function inject(): string {
      dev.manifest = {id: "thelifeandtimes.urbit-theme.dev"}
      edge.manifest = {id: "thelifeandtimes.urbit-theme.edge"}
      return "injected"
    }
    function clearDev(): string { dev.manifest = null; return "cleared" }
    function stop(): string { Qt.callLater(Qt.quit); return "stopping" }
  }
}
