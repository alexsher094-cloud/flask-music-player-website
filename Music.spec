# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-сборка Windows-версии (onedir).

Запуск:
    python -m PyInstaller Music.spec --noconfirm
Результат:
    dist/Music/Music.exe
"""

import sys

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

sys.path.insert(0, SPECPATH)
from version import __version__  # noqa: E402

_ver = tuple(int(part) for part in __version__.split("."))
_ver = (_ver + (0, 0, 0, 0))[:4]

version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=_ver,
        prodvers=_ver,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo([
            StringTable("040904B0", [
                StringStruct("CompanyName", "Music"),
                StringStruct("FileDescription", "Music Player"),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", "Music"),
                StringStruct("OriginalFilename", "Music.exe"),
                StringStruct("ProductName", "Music"),
                StringStruct("ProductVersion", __version__),
            ]),
        ]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

a = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("templates", "templates"),
        ("static", "static"),
    ],
    hiddenimports=[
        "pystray._win32",
        "webview.platforms.winforms",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "pydoc",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Music",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/app.ico",
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Music",
)
