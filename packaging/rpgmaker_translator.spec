# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows release bundle (onedir).

Build from the repo root:

    pyinstaller packaging/rpgmaker_translator.spec --noconfirm --clean

or use packaging/build_windows.ps1, which also zips the result.

Output: dist/RPGMakerTranslator/RPGMakerTranslator.exe (+ _internal/).
"""

import os
import re

from PyInstaller.utils.hooks import collect_data_files

# SPECPATH / workpath are injected by PyInstaller.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

APP_NAME = "RPGMakerTranslator"


def _read_version():
    with open(os.path.join(ROOT, "translator", "version.py"), encoding="utf-8") as f:
        m = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", f.read(), re.M)
    if not m:
        raise SystemExit("Could not read __version__ from translator/version.py")
    return m.group(1)


def _write_version_info(version):
    """Windows file properties (Details tab) - also makes the exe look less anonymous to AV."""
    nums = [int(x) for x in re.findall(r"\d+", version)[:3]]
    nums += [0] * (4 - len(nums))
    tup = tuple(nums)
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tup}, prodvers={tup}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'MoriTranslates'),
      StringStruct('FileDescription', 'RPG Maker Translator'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{APP_NAME}'),
      StringStruct('LegalCopyright', 'Copyright (c) MoriTranslates - Business Source License 1.1'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe'),
      StringStruct('ProductName', 'RPG Maker Translator'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    os.makedirs(workpath, exist_ok=True)
    path = os.path.join(workpath, "file_version_info.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


VERSION = _read_version()
VERSION_FILE = _write_version_info(VERSION)


def _wordninja_data():
    """wordninja is a single-module dist that reads <site-packages>/wordninja/*.gz."""
    import wordninja

    gz = os.path.join(os.path.dirname(os.path.abspath(wordninja.__file__)),
                      "wordninja", "wordninja_words.txt.gz")
    return [(gz, "wordninja")]


datas = [
    # Read-only app resources, resolved at runtime via translator/resource_paths.py
    (os.path.join(ROOT, "assets"), "assets"),
    (os.path.join(ROOT, "translator", "resources"), os.path.join("translator", "resources")),
    (os.path.join(ROOT, "LICENSE"), "."),
]
datas += _wordninja_data()
# Spell checker: English dictionary only (the editor checks English output).
datas += collect_data_files("spellchecker", includes=["resources/en.json.gz"])

hiddenimports = [
    # Imported lazily inside functions; listed explicitly so a refactor to
    # importlib-style loading can't silently drop them from the bundle.
    "openai",
    "rubymarshal.reader",
    "rubymarshal.writer",
    "translator.version",  # for the window title / About box
]

excludes = [
    # Not used by the app; keeps the bundle smaller.
    "tkinter",
    "unittest",
    "pydoc_data",
    "pytest",
    "setuptools",
    "pkg_resources",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtQml",
    "PyQt6.QtQuick",
    "PyQt6.QtMultimedia",
    "PyQt6.QtBluetooth",
    "PyQt6.QtPositioning",
    "PyQt6.Qt3DCore",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX-packed exes trigger far more AV false positives
    console=False,      # GUI app; errors go to _error.log / message box
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "logo.png"),  # converted to .ico by PyInstaller (needs Pillow)
    version=VERSION_FILE,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
