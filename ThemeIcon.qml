import QtQuick

// A font-independent palette outline with an optically centered Urbit tilde.
// Use the same uncluttered outline and tilde at both bar and hero sizes.
Canvas {
  id: root
  property real size: 24
  property color ink: "white"
  implicitWidth: size
  implicitHeight: size
  antialiasing: true

  onInkChanged: requestPaint()
  onWidthChanged: requestPaint()
  onHeightChanged: requestPaint()

  onPaint: {
    var ctx = getContext("2d")
    ctx.reset()
    ctx.clearRect(0, 0, width, height)
    ctx.scale(width / 64, height / 64)
    ctx.lineWidth = Math.max(2.8, 64 / Math.max(1, width))
    ctx.lineJoin = "round"
    ctx.lineCap = "round"
    ctx.strokeStyle = ink

    ctx.beginPath()
    ctx.moveTo(32, 5)
    ctx.bezierCurveTo(17, 5, 5, 17, 5, 32)
    ctx.bezierCurveTo(5, 47, 17, 59, 32, 59)
    ctx.bezierCurveTo(38, 59, 41, 56, 41, 52)
    ctx.bezierCurveTo(41, 49, 38, 47, 38, 43)
    ctx.bezierCurveTo(38, 39, 41, 37, 46, 37)
    ctx.lineTo(49, 37)
    ctx.bezierCurveTo(56, 37, 59, 34, 59, 28)
    ctx.bezierCurveTo(59, 15, 46, 5, 32, 5)
    ctx.closePath()
    ctx.stroke()

    // Center in the palette's usable body, slightly left/up of the bounding
    // box center to balance the thumb notch. Equal-height ends read as `~`.
    ctx.lineWidth = Math.max(4, 64 / Math.max(1, width))
    ctx.beginPath()
    ctx.moveTo(15, 30)
    ctx.bezierCurveTo(19, 23, 24, 23, 28.5, 30)
    ctx.bezierCurveTo(33, 37, 38, 37, 42, 30)
    ctx.stroke()
  }
}
