# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the `prism` CLI binary.
# Run from the repo root:  pyinstaller packaging/prism.spec --distpath dist --workpath build -y
# (or use packaging/build_release.sh, which does this and zips the result.)

import os

block_cipher = None
root = os.path.abspath(SPECPATH) + '/..'

a = Analysis(
    [os.path.join(root, 'prism', 'entry.py')],
    pathex=[os.path.join(root, 'prism')],   # chain/, crypto/, ... are top-level here
    binaries=[],
    datas=[],
    hiddenimports=[
        'chain.cli', 'chain.node', 'chain.block', 'chain.params',
        'chain.emission', 'chain.pow', 'chain.difficulty', 'chain.denylist',
        'crypto.hashing', 'crypto.edwards', 'crypto.field', 'crypto.pedersen',
        'crypto.clsag', 'crypto.bulletproof', 'crypto.stealth',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'setuptools'],
    win_no_prefer_redirects=False,
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
    name='prism',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,           # CLI tool: keep the terminal attached
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
