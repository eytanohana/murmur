# PyInstaller spec for murmur.app (Apple Silicon). Built by scripts/build_app.sh.
import os
import sys
import tomllib

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, SPECPATH)
from build_info import mlx_min_macos  # noqa: E402

with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
    VERSION = tomllib.load(f)["project"]["version"]

datas, binaries, hiddenimports = [], [], []
# mlx ships a namespace package with libmlx.dylib and mlx.metallib; mlx_whisper ships its
# mel filters and tokenizer files. collect_all picks up the native libraries and data files.
for pkg in ("mlx", "mlx_whisper", "tiktoken", "tiktoken_ext"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
binaries += collect_dynamic_libs("sounddevice")  # PortAudio comes from _sounddevice_data
datas += collect_data_files("_sounddevice_data")
hiddenimports += [
    "pynput.keyboard._darwin",
    "pynput.mouse._darwin",
    "pynput._util.darwin",
    "objc",
    "AppKit",
    "Foundation",
    "Quartz",
    "ApplicationServices",
    "PyObjCTools.AppHelper",
]

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # mlx-whisper depends on torch, but only its conversion script imports it (about 400 MB).
    excludes=[
        "torch",
        "torchaudio",
        "torchvision",
        "mlx_whisper.torch_whisper",
        "tkinter",
        "matplotlib",
        "pytest",
        "IPython",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="murmur",
    console=False,
    target_arch="arm64",
)
coll = COLLECT(exe, a.binaries, a.datas, name="murmur")
app = BUNDLE(
    coll,
    name="murmur.app",
    icon=os.path.join(ROOT, "build", "murmur.icns"),
    bundle_identifier="com.eytanohana.murmur",
    version=VERSION,
    info_plist={
        "CFBundleName": "murmur",
        "CFBundleDisplayName": "murmur",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSUIElement": True,
        "LSMinimumSystemVersion": mlx_min_macos(),  # the installed mlx wheel decides
        "NSMicrophoneUsageDescription": (
            "murmur records your voice while you hold Option, to turn it into text on this Mac."
        ),
        "NSHumanReadableCopyright": "Copyright (c) 2026 Eytan Ohana. MIT License.",
        "NSHighResolutionCapable": True,
    },
)
