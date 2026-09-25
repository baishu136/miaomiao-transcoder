# -*- mode: python ; coding: utf-8 -*-
import os

project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
assets_dir = os.path.join(project_dir, 'assets')
datas = [(assets_dir, 'assets')]
binaries = []
hiddenimports = ['qqmusic_key']


a = Analysis(
    [os.path.join(project_dir, 'qt_main.py')],
    pathex=[project_dir],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter', 'customtkinter', 'tkinterdnd2'],
    noarchive=False,
    optimize=0,
)
# Qt 6.11 on Windows links against the Windows ICU shim (icuuc.dll).  A
# third-party ICU 78 copy discovered on PATH has the same filename but an
# incompatible export table, causing QtCore.pyd to fail with WinError 127.
a.binaries = [entry for entry in a.binaries
              if os.path.basename(entry[0]).lower() not in {'icuuc.dll', 'icudt78.dll'}]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MiaomiaoTranscoder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(assets_dir, 'miaomiao_icon.ico')],
    manifest=os.path.join(assets_dir, 'windows_dpi.manifest'),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MiaomiaoTranscoder',
)
