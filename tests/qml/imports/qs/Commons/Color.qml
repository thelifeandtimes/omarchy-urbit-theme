pragma Singleton
import QtQuick
QtObject {
  property color foreground: "#ddeeff"
  property color background: "#112233"
  property color accent: "#88aaff"
  property color urgent: "#ff8877"
  property var shellValues: ({})
  property var tooltip: ({ background: "#112233", text: "#ddeeff", border: "#88aaff" })
}
