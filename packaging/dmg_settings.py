# dmgbuild settings for the murmur disk image. Used by scripts/build_app.sh:
#   dmgbuild -s packaging/dmg_settings.py -D app=dist/murmur.app \
#            -D background=build/dmg-background.png -D icon=build/murmur.icns \
#            murmur dist/murmur-<version>-arm64.dmg
# dmgbuild writes the Finder layout itself, so no Finder session or AppleScript is needed.
import os.path

application = defines["app"]  # noqa: F821  (defines is provided by dmgbuild)
app_name = os.path.basename(application)

format = "UDZO"
files = [application]
symlinks = {"Applications": "/Applications"}
background = defines["background"]  # noqa: F821
icon = defines.get("icon")  # noqa: F821  volume icon

window_rect = ((200, 140), (660, 400))  # the same size as the background image
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
icon_size = 128
text_size = 13
arrange_by = None
icon_locations = {
    app_name: (165, 190),
    "Applications": (495, 190),
}
