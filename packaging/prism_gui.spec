# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the `prism-gui` DESKTOP binary (double-click wallet).
# Run from the repo root:
#   pyinstaller packaging/prism_gui.spec --distpath dist --workpath build -y
# Produces dist/prism-gui(.exe): a onefile executable that embeds CPython and
# the zero-argument launcher (rpc.launcher.main) plus the GUI static bundle,
# satisfying the spec §13 "Double-click installer mandate" runtime side.
# The Tauri/Electron installer shell bundles this binary as a resource.

import os

block_cipher = None
root = os.path.abspath(SPECPATH) + '/..'
gui_dir = os.path.join(root, 'prism', 'rpc', 'static')
if not os.path.isfile(os.path.join(gui_dir, 'index.html')):
    raise SystemExit(
        "ERROR: prism/rpc/static/ is missing. Run `python packaging/sync_gui.py` "
        "from the repo root before building (single source of truth stays in "
        "prism/gui/, synced into the package for pip installs and frozen builds).")

a = Analysis(
    [os.path.join(root, 'prism', 'entry_gui.py')],
    pathex=[root],   # collect the `prism` package as one namespace-consistent tree
    binaries=[],
    datas=[
        # GUI static bundle served at / and /static/* by rpc.server; frozen
        # builds resolve it via sys._MEIPASS/prism/rpc/static (see server.py).
        (os.path.join(root, 'prism', 'rpc', 'static'),
         os.path.join('prism', 'rpc', 'static')),
    ],
    hiddenimports=[
        'prism.rpc.launcher', 'prism.rpc.server', 'prism.rpc.service',
        'prism.auth.captcha', 'prism.auth.keystore',
        'prism.chain.node', 'prism.chain.block', 'prism.chain.params',
        'prism.chain.emission', 'prism.chain.pow', 'prism.chain.difficulty',
        'prism.chain.denylist',
        'prism.crypto.hashing', 'prism.crypto.edwards', 'prism.crypto.field',
        'prism.crypto.pedersen', 'prism.crypto.clsag', 'prism.crypto.bulletproof',
        'prism.crypto.stealth',
        'prism.ai.fraud', 'prism.ai.simulator',
        'prism.wallet.agent_auth', 'prism.wallet.intent', 'prism.wallet.disclosure',
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
    name='prism-gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # doofus-proof: no terminal window ever appears
    disable_windowed_traceback=False,
    onefile=True,
)
