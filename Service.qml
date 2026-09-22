import QtQuick
import Quickshell.Io
import "Model.js" as Model

Item {
  id: root
  property var shell: null
  property var manifest: null
  property var settings: ({})
  property var account: Model.emptyState()
  property var currentPalette: null
  property var operationErrors: ({})
  property string cleanupWarning: ""
  readonly property string lastError: Object.keys(operationErrors).map(function(key) {
    return root.operationErrors[key]
  }).concat(cleanupWarning ? ["Cleanup warning: " + cleanupWarning] : []).join(" ")
  property bool loaded: false
  property var queue: Model.initialQueue()
  property string pendingInput: ""
  property string output: ""
  property string transportError: ""
  property int processGeneration: 0
  property var rowErrors: ({})
  readonly property bool busy: queue.running !== "" || queue.controls.length > 0
  readonly property bool canLogin: loaded && account.ships.length < 64
    && queue.running === "" && queue.controls.length === 0 && !helper.running
  readonly property string statusText: !loaded ? "Loading ships" : busy ? "Working: " + queue.running
    : account.ships.length + " ships"

  function rowBusy(row) {
    return Model.controlled(queue, row) || (!!queue.run && queue.running !== "sync"
      && Model.sameAccount(queue.run.expectedAccount, row))
  }

  function rowStatus(row) {
    if (row.authenticationRequired) return "Session expired. Remove this ship, then add it again."
    if (rowBusy(row)) return "Waiting to " + (Model.controlled(queue, row) ? "apply change" : "finish change")
    if (queue.running === "sync" && Model.sameAccount(queue.run.expectedAccount, row)) return "Syncing..."
    var jobs = queue.jobs.filter(function(j) { return Model.sameAccount(j.expectedAccount, row) })
    if (jobs.length) return jobs[0].attempts ? "Sync pending. Automatic retry scheduled." : "Sync pending."
    return rowErrors[row.id] || row.lastError || (row.pending ? "Sync pending. Pause then resume to retry." : "")
  }

  function refresh() {
    queue = Model.request(queue, "refresh", account)
    pump()
  }

  function themeChanged() {
    queue = Model.request(queue, "theme", account)
    retry.stop()
    debounce.restart()
  }

  function control(action, row) {
    if (["enable", "pause", "disconnect"].indexOf(action) < 0 || !loaded || !row) return
    if (action === "enable" && row.authenticationRequired) return
    queue = Model.request(queue, action, account, row)
    pump()
  }

  function login(url, code) {
    // No credentials in the queue: the only retained copy is the immediate stdin handoff.
    if (!canLogin || !Model.safeUrl(url) || !url || !code || code.length > 512) return false
    var q = Model.copy(queue)
    q.running = "login"
    q.run = { action: "login" }
    q.loginIds = account.ships.map(function(row) { return row.id })
    queue = q
    launch("login", { url: url, code: code })
    return true
  }

  function pump() {
    if (queue.running || helper.running) return
    retry.stop()
    queue = Model.next(queue, account, Date.now())
    var action = queue.running
    if (!action) {
      var delay = Model.retryDelay(queue, Date.now())
      if (delay) { retry.interval = delay; retry.start() }
      return
    }
    var command = action, input = ({})
    if (action === "sync") input = { force: false, palette: queue.run.palette }
    if (action === "enable" || action === "pause") { command = "set-auto"; input = { enabled: action === "enable" } }
    if (queue.run.expectedAccount) input.expectedAccount = queue.run.expectedAccount
    launch(command, input)
  }

  function launch(action, input) {
    processGeneration++
    var path = Model.localPath(Qt.resolvedUrl("client/main.py"))
    if (!path) { finish(Model.failure("helper_unavailable")); return }
    output = ""
    transportError = ""
    pendingInput = JSON.stringify(input) + "\n"
    helper.command = ["python3", "-B", path, action]
    helper.stdinEnabled = true
    watchdog.restart()
    helper.running = true
    deferStopped()
  }

  function deferStopped() {
    // runningChanged can precede exited. Only infer FailedToStart after the
    // event turn, and never let an old callback complete a consecutive process.
    var generation = processGeneration
    Qt.callLater(function() {
      if (root && root.processGeneration === generation && root.queue.running && !helper.running) {
        root.pendingInput = ""
        root.finish(Model.failure(root.transportError || "helper_unavailable"))
      }
    })
  }

  function finish(response) {
    if (!queue.running) return
    watchdog.stop()
    pendingInput = ""
    helper.stdinEnabled = false
    var action = queue.running
    if (response.state) {
      account = response.state
      loaded = true
    }
    if (action === "preview" && queue.run.generation === queue.generation && response.ok)
      currentPalette = response.palette
    var expected = queue.run ? queue.run.expectedAccount : null
    var errors = ({})
    account.ships.forEach(function(row) {
      if (root.rowErrors[row.id] && (!response.state || row.lastError)) errors[row.id] = root.rowErrors[row.id]
    })
    if (expected && Model.account(account, expected)) {
      if (response.ok) delete errors[expected.id]
      else errors[expected.id] = response.error.message
    }
    rowErrors = errors
    var operational = Object.assign({}, operationErrors)
    if (response.state) delete operational.status
    if (action !== "preview" || queue.run.generation === queue.generation) {
      if (response.ok) delete operational[action]
      else if (!expected || !Model.account(account, expected)) operational[action] = response.error.message
    }
    operationErrors = operational
    if (response.warning) cleanupWarning = response.warning.message
    queue = Model.complete(queue, response, account, Date.now())
    output = ""
    Qt.callLater(pump)
  }

  Component.onCompleted: Qt.callLater(pump)
  Component.onDestruction: pendingInput = ""

  IpcHandler {
    target: "urbit-theme"
    function themeChanged(): string { root.themeChanged(); return "queued" }
  }

  Timer {
    id: debounce
    interval: 350
    onTriggered: {
      var q = Model.copy(root.queue)
      q.debouncing = false
      root.queue = q
      root.pump()
    }
  }
  Timer {
    id: retry
    onTriggered: root.pump()
  }
  Timer {
    id: watchdog
    objectName: "helperWatchdog"
    interval: 120000
    onTriggered: {
      root.pendingInput = ""
      root.transportError = "helper_timeout"
      if (helper.running) helper.signal(9)
      else root.finish(Model.failure(root.transportError))
    }
  }

  Process {
    id: helper
    objectName: "urbitThemeHelper"
    stdinEnabled: true
    onStarted: {
      write(root.pendingInput)
      root.pendingInput = ""
      stdinEnabled = false
    }
    onRunningChanged: if (!running && root.queue.running) {
      root.pendingInput = ""
      root.deferStopped()
    }
    stdout: SplitParser {
      splitMarker: ""
      onRead: function(data) {
        if (root.transportError) return
        if (root.output.length + data.length > Model.maxOutput) {
          root.output = ""
          root.transportError = "output_limit"
          helper.signal(9)
        } else root.output += data
      }
    }
    // Drain chunks without collecting or forwarding them to the shell log.
    stderr: SplitParser { splitMarker: ""; onRead: function(data) {} }
    onExited: function(exitCode, exitStatus) {
      root.finish(root.transportError ? Model.failure(root.transportError)
        : Model.parseResponse(root.output, exitStatus === 0 ? exitCode : -1))
    }
  }
}
