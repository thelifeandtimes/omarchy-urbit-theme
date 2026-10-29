import QtQuick
import QtTest
import "../stable" as Stable
import "../dev" as Dev
import "../edge" as Edge

Item {
  width: 1024; height: 768
  Component { id: stableService; Stable.Service { desktopEnabled: false } }
  Component { id: devService; Dev.Service { desktopEnabled: false } }
  Component { id: edgeService; Edge.Service { desktopEnabled: false } }
  Component { id: panelComponent; Dev.Panel {} }
  QtObject {
    id: fakeBar
    property string fontFamily: "sans-serif"
    property color barForeground: "#ddeeff"
    property color urgent: "#ff8877"
    property bool foregroundAnimationEnabled: false
    property bool vertical: false
    property int barSize: 32
    function hideTooltip(item) {}
    property QtObject shell: QtObject {
      property var services: ({})
      function serviceFor(id) { return services[id] || null }
    }
  }
  TestCase {
    name: "ThemeProfiles"
    when: windowShown
    property var objects: []
    function make(component, properties) {
      var object = component.createObject(parent, properties || {})
      verify(object !== null); objects.push(object); return object
    }
    function cleanup() {
      fakeBar.shell.services = ({})
      for (var i = objects.length - 1; i >= 0; i--) objects[i].destroy()
      objects = []; wait(0)
    }
    function test_serviceTargetsAndStateAreSeparate() {
      var components = [stableService, devService, edgeService]
      var suffixes = ["", ".dev", ".edge"]
      var services = []
      for (var i = 0; i < components.length; i++) {
        var id = "thelifeandtimes.urbit-theme" + suffixes[i]
        var service = make(components[i], {manifest: {id: id}})
        var ipc = findChild(service, "themeInstanceIpc"); verify(ipc !== null)
        compare(service.moduleName, id); compare(ipc.target, "urbit-theme" + suffixes[i]); verify(ipc.enabled)
        service.account = {ships: [{id: id}]}; services.push(service)
      }
      for (var j = 0; j < services.length; j++)
        compare(services[j].account.ships[0].id, "thelifeandtimes.urbit-theme" + suffixes[j])
    }
    function test_noCanonicalIpcBeforeIdentityInjection() {
      var service = make(devService)
      var ipc = findChild(service, "themeInstanceIpc"); verify(ipc !== null)
      verify(!ipc.enabled); compare(ipc.target, "")
      service.manifest = {id: "thelifeandtimes.urbit-theme.dev"}
      verify(ipc.enabled); compare(ipc.target, "urbit-theme.dev")
      service.manifest = {id: "unrelated.plugin"}
      verify(!ipc.enabled); compare(ipc.target, "")
      service.manifest = null
      verify(!ipc.enabled); compare(ipc.target, "")
    }
    function test_panelIdentityAndServiceFollowHostInjection() {
      var stable = make(stableService, {manifest: {id: "thelifeandtimes.urbit-theme"}})
      var dev = make(devService, {manifest: {id: "thelifeandtimes.urbit-theme.dev"}})
      var edge = make(edgeService, {manifest: {id: "thelifeandtimes.urbit-theme.edge"}})
      fakeBar.shell.services = {"thelifeandtimes.urbit-theme": stable,
        "thelifeandtimes.urbit-theme.dev": dev, "thelifeandtimes.urbit-theme.edge": edge}
      var panel = make(panelComponent, {bar: fakeBar})
      compare(panel.ipcTarget, ""); compare(panel.service, null)
      var suffixes = ["", ".dev", ".edge"]
      var labels = ["Network Theme", "Network Theme (dev)", "Network Theme (edge)"]
      var services = [stable, dev, edge]
      for (var i = 0; i < suffixes.length; i++) {
        panel.moduleName = "thelifeandtimes.urbit-theme" + suffixes[i]
        compare(panel.ipcTarget, panel.moduleName)
        compare(panel.service, services[i]); compare(panel.instanceLabel, labels[i])
      }
    }
  }
}
