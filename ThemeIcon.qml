import QtQuick

// A font-independent palette outline with an optically centered Urbit tilde.
// Keep the bar glyph open and uncluttered; only the large hero adds paint.
Canvas {
  id: root
  property real size: 24
  property color ink: "white"
  property var palette: null
  property bool colored: false
  implicitWidth: size
  implicitHeight: size
  antialiasing: true

  onInkChanged: requestPaint()
  onPaletteChanged: requestPaint()
  onColoredChanged: requestPaint()
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
    ctx.fillStyle = ink

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
    ctx.globalAlpha = 0.04
    if (colored) ctx.fill()
    ctx.globalAlpha = 1
    ctx.stroke()

    var pots = [[17, 21, "error"], [29, 14, "primary"], [43, 18, "tertiary"]]
    for (var i = 0; colored && i < pots.length; i++) {
      var pot = pots[i]
      ctx.fillStyle = colored && palette && palette[pot[2]] ? palette[pot[2]] : ink
      ctx.beginPath()
      ctx.arc(pot[0], pot[1], 2.8, 0, Math.PI * 2)
      ctx.fill()
    }

    ctx.strokeStyle = ink
    ctx.lineWidth = Math.max(4, 64 / Math.max(1, width))
    ctx.beginPath()
    ctx.moveTo(17, 34)
    ctx.bezierCurveTo(21, 27, 25, 27, 30, 31)
    ctx.bezierCurveTo(35, 35, 39, 35, 43, 28)
    ctx.stroke()
  }
}
