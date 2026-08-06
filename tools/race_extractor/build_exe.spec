# PyInstaller spec: build a single-file uma-race-extract.exe
#
# Run:   pyinstaller build_exe.spec        (Windows Python)
# Requires:  pip install pyinstaller
# Result:    dist/uma-race-extract.exe   (self-contained)
#
# Forked from uma-it-optimizer's proven build_exe.spec. The exe reads
# the game's memory via frida, so scenario_decode.py and the vendored
# il2cpp bridge ride along inside the bundle; config + captures live
# BESIDE the exe (see HOME_DIR handling in uma_race_extract.py).

# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['uma_race_extract.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('vendor/il2cpp_bridge.js', 'vendor'),  # bundle bridge JS
    ],
    # scenario_decode is imported inside a try-block whose except would
    # silently swallow a bundling miss — named here so a missing module
    # is a build error, not a quietly poorer capture.
    hiddenimports=['scenario_decode'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='uma-race-extract',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX packing amplifies small source-level changes into huge byte-level
    # differences (compression is nonlinear), which makes Windows Defender's
    # ML classifier score every release inconsistently. Left off deliberately.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # keep console window (users see progress + errors)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
