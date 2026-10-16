"""Generate the app icon (murmur.icns) and the DMG background with AppKit.

Run as: uv run python scripts/make_assets.py build/
Nothing here is checked in as a binary; build_app.sh calls this on every build.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import AppKit
from Foundation import NSMakeRect, NSPoint

ICON_SIZES = (16, 32, 128, 256, 512)  # each is written at 1x and 2x for iconutil
DMG_SIZE = (660, 400)


def color(r: int, g: int, b: int, a: float = 1.0):
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, a)


def render(width: int, height: int, draw) -> bytes:
    """Draw into an RGBA bitmap of exactly width x height pixels and return PNG bytes."""
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, width, height, 8, 4, True, False, AppKit.NSCalibratedRGBColorSpace, 0, 0
    )
    ctx = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(ctx)
    ctx.setShouldAntialias_(True)
    draw(width, height)
    AppKit.NSGraphicsContext.restoreGraphicsState()
    return bytes(rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {}))


def draw_icon(width: int, height: int) -> None:
    """Rounded square with a vertical gradient and a white microphone."""
    s = width / 1024  # the artwork is laid out on a 1024 grid
    inset = 100 * s
    body = NSMakeRect(inset, inset, width - 2 * inset, height - 2 * inset)
    square = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(body, 185 * s, 185 * s)
    gradient = AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
        color(93, 120, 255), color(52, 58, 170)
    )
    gradient.drawInBezierPath_angle_(square, -90)
    white = AppKit.NSColor.whiteColor()
    white.setFill()
    white.setStroke()
    capsule = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(422 * s, 450 * s, 180 * s, 330 * s), 90 * s, 90 * s
    )
    capsule.fill()
    holder = AppKit.NSBezierPath.bezierPath()
    holder.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
        NSPoint(512 * s, 530 * s), 170 * s, 180, 360
    )
    holder.setLineWidth_(38 * s)
    holder.setLineCapStyle_(AppKit.NSLineCapStyleRound)
    holder.stroke()
    stand = AppKit.NSBezierPath.bezierPath()
    stand.moveToPoint_(NSPoint(512 * s, 360 * s))
    stand.lineToPoint_(NSPoint(512 * s, 290 * s))
    stand.moveToPoint_(NSPoint(420 * s, 285 * s))
    stand.lineToPoint_(NSPoint(604 * s, 285 * s))
    stand.setLineWidth_(38 * s)
    stand.setLineCapStyle_(AppKit.NSLineCapStyleRound)
    stand.stroke()


def draw_dmg_background(width: int, height: int) -> None:
    """Soft background with an arrow from the app to Applications and one line of text."""
    top, bottom = color(246, 247, 252), color(226, 229, 244)
    AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(top, bottom).drawInRect_angle_(
        NSMakeRect(0, 0, width, height), -90
    )
    y = height * 0.52
    arrow = AppKit.NSBezierPath.bezierPath()
    arrow.moveToPoint_(NSPoint(width * 0.38, y))
    arrow.lineToPoint_(NSPoint(width * 0.60, y))
    arrow.setLineWidth_(8)
    arrow.setLineCapStyle_(AppKit.NSLineCapStyleRound)
    color(120, 130, 190).setStroke()
    arrow.stroke()
    head = AppKit.NSBezierPath.bezierPath()
    head.moveToPoint_(NSPoint(width * 0.575, y + 22))
    head.lineToPoint_(NSPoint(width * 0.615, y))
    head.lineToPoint_(NSPoint(width * 0.575, y - 22))
    head.setLineWidth_(8)
    head.setLineCapStyle_(AppKit.NSLineCapStyleRound)
    head.setLineJoinStyle_(AppKit.NSLineJoinStyleRound)
    head.stroke()
    text = AppKit.NSAttributedString.alloc().initWithString_attributes_(
        "Drag murmur to Applications",
        {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(
                20, AppKit.NSFontWeightMedium
            ),
            AppKit.NSForegroundColorAttributeName: color(70, 76, 120),
        },
    )
    size = text.size()
    text.drawAtPoint_(NSPoint((width - size.width) / 2, height * 0.16))


def make_icns(out: Path) -> Path:
    iconset = out / "murmur.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for size in ICON_SIZES:
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            (iconset / name).write_bytes(render(px, px, draw_icon))
    icns = out / "murmur.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
    return icns


def make_dmg_background(out: Path) -> Path:
    path = out / "dmg-background.png"
    path.write_bytes(render(DMG_SIZE[0], DMG_SIZE[1], draw_dmg_background))
    return path


def main(argv: list[str]) -> None:
    out = Path(argv[1] if len(argv) > 1 else "build")
    out.mkdir(parents=True, exist_ok=True)
    print(make_icns(out))
    print(make_dmg_background(out))


if __name__ == "__main__":
    AppKit.NSApplication.sharedApplication()  # AppKit drawing needs an application object
    main(sys.argv)
