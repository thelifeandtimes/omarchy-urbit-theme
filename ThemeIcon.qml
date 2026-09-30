import QtQuick

// A font-independent painter's palette. Paint colors track the active theme
// at both bar and hero sizes.
Canvas {
  id: root
  property real size: 24
  property color ink: "white"
  property var palette: null
  implicitWidth: size
  implicitHeight: size
  antialiasing: true

  onInkChanged: requestPaint()
  onPaletteChanged: requestPaint()
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

    var paints = [[16, 31, "error"], [25, 17, "primary"],
                  [41, 17, "secondary"], [50, 29, "tertiary"]]
    for (var i = 0; i < paints.length; i++) {
      var paint = paints[i]
      ctx.fillStyle = palette && palette[paint[2]] ? palette[paint[2]] : ink
      ctx.beginPath()
      ctx.arc(paint[0], paint[1], 4.2, 0, Math.PI * 2)
      ctx.fill()
    }
  }
}
