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
  property string lastError: ""
  property bool loaded: false
  property var queue: Model.initialQueue()
  property string pendingInput: ""
  property string output: ""
  property string transportError: ""
  property int processGeneration: 0
  signal consentInvalidated()
  readonly property bool busy: queue.running !== "" || queue.control !== ""
  readonly property bool canLogin: loaded && !account.connected && Model.idle(queue)
  readonly property bool stopping: queue.control === "pause" || queue.control === "disconnect"
    || queue.running === "pause" || queue.running === "disconnect"
  readonly property bool retrying: queue.retryDelay > 0
  readonly property string statusText: !loaded ? "Loading ship status" : stopping ? "Waiting to stop publishing"
    : busy ? "Working: " + queue.running : account.authenticationRequired ? "Sign-in required"
    : !account.connected ? "No ship connected" : account.automatic && !queue.suspended ? "Automatic publishing enabled" : "Automatic publishing paused"

  function refresh() {
    queue = Model.request(queue, "refresh", account)
    pump()
  }

  function themeChanged() {
    queue = Model.request(queue, "theme", account)
    if (!queue.retryDelay) retry.stop()
    debounce.restart()
  }

  function control(action) {
    if (["publish", "enable", "pause", "disconnect"].indexOf(action) < 0 || !loaded) return
    queue = Model.request(queue, action, account)
    if (!queue.retryDelay) retry.stop()
    pump()
  }

  function login(url, code) {
    // No credentials in the queue: the only retained copy is the immediate stdin handoff.
    if (!canLogin || !Model.safeUrl(url) || !url || !code || code.length > 512) return false
    var q = Model.copy(queue)
    q.running = "login"
    q.runGeneration = q.generation
    queue = q
    launch("login", { url: url, code: code })
    return true
  }

  function pump() {
    if (queue.running || helper.running) return
    queue = Model.next(queue, account)
    var action = queue.running
    if (!action) return
    var command = action, input = ({})
    if (action === "publish" || action === "sync") { command = "sync"; input = { force: action === "publish" } }
    if (action === "enable" || action === "pause") { command = "set-auto"; input = { enabled: action === "enable" } }
    if (queue.runAccount) input.expectedAccount = queue.runAccount
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
    if (response.error && response.error.code === "account-changed") consentInvalidated()
    if (response.state) {
      account = response.state
      loaded = true
    }
    if (response.palette || (action === "preview" && response.state)) currentPalette = response.palette
    lastError = Model.meaningfulError(lastError, action, response)
    queue = Model.complete(queue, response, account)
    if (!queue.retryDelay) retry.stop()
    if (queue.retryDelay && !retry.running) {
      retry.interval = queue.retryDelay
      retry.start()
    }
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
    onTriggered: {
      var q = Model.copy(root.queue)
      q.retryDelay = 0
      root.queue = q
      root.pump()
    }
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
