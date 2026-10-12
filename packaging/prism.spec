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
    datas=[
        # GUI static bundle served at / by rpc.server; frozen builds resolve
        # it via sys._MEIPASS/prism/rpc/static (see server.py). Required for
        # `prism gui` / prism-gui.exe to actually render the wallet UI.
        (os.path.join(root, 'prism', 'rpc', 'static'),
         os.path.join('prism', 'rpc', 'static')),
    ],
    hiddenimports=[
        'chain.cli', 'chain.node', 'chain.block', 'chain.params',
        'chain.emission', 'chain.pow', 'chain.difficulty', 'chain.denylist',
        'crypto.hashing', 'crypto.edwards', 'crypto.field', 'crypto.pedersen',
        'crypto.clsag', 'crypto.bulletproof', 'crypto.stealth',
        # GUI stack (lazy-imported by `prism gui`; PyInstaller cannot see
        # lazy imports, so list them explicitly):
        'rpc.launcher', 'rpc.server', 'rpc.service',
        'ai.fraud', 'ai.simulator',
        'wallet.intent', 'wallet.agent_auth', 'wallet.disclosure',
        'auth.captcha', 'auth.keystore',
        'l2.fees', 'l2.channels',
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

# --- prism-gui: same bundle, zero-argument GUI launcher entry (entry.gui).
# Ships inside the prism archive so double-clicking prism-gui(.exe) opens
# the wallet UI in a browser (spec §13 "Double-click installer mandate").
# NOTE: this EXE must freeze the *GUI script* (prism/entry_gui.py ->
# rpc.launcher.main), NOT entry.gui via a bare reference to `a.scripts`:
# reusing the CLI's analysis scripts made prism-gui.exe run chain.cli.main
# with argv[0]="prism-gui", so argparse demanded a subcommand and the
# binary died instantly ("the following arguments are required: cmd") —
# perceived as "prism-gui.exe crashes". A dedicated Analysis is cheap here
# because everything lands in one onefile cache anyway.
# The GUI binary needs exactly one extra data item: the static web bundle.
# Do NOT copy `a.datas` wholesale into this second Analysis:
#  - PyInstaller >= 6.0 stores Analysis.datas as 3-element tuples
#    (dest, src, typecode); feeding those into a fresh Analysis raised
#    "ValueError: too many values to unpack (expected 2)".
#  - On macOS, Analysis.datas also carries framework entries whose source
#    paths are relative to the *first* spec dir; re-resolving them against
#    SPECPATH broke with "Unable to find .../packaging/Python.framework/...".
_static_dir = os.path.join(root, 'prism', 'rpc', 'static')

agui = Analysis(
    [os.path.join(root, 'prism', 'entry_gui.py')],
    pathex=[root],                       # resolves the `prism.*` package tree
    binaries=[],
    datas=[(_static_dir, os.path.join('prism', 'rpc', 'static'))],
    hiddenimports=[
        'prism.rpc.launcher', 'prism.rpc.server', 'prism.rpc.service',
        'prism.auth.captcha', 'prism.auth.keystore',
        'prism.chain.node', 'prism.chain.block', 'prism.chain.params',
        'prism.chain.emission', 'prism.chain.pow', 'prism.chain.difficulty',
        'prism.chain.denylist',
        'prism.crypto.hashing', 'prism.crypto.edwards', 'prism.crypto.field',
        'prism.wallet.agent_auth', 'prism.wallet.intent',
        'prism.wallet.disclosure',
        'prism.ai.fraud', 'prism.ai.simulator',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'setuptools'],
    win_no_prefer_redirects=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz2 = PYZ(agui.pure, agui.zipped_data, cipher=block_cipher)

exe_gui = EXE(
    pyz2,
    agui.scripts,
    agui.binaries,
    agui.zipfiles,
    agui.datas,
    [],
    name='prism-gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,           # keeps a terminal for status/errors (v1 posture)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
