# Running the GUI wallet standalone

The GUI wallet is **already runnable standalone today** — no Phase-4 gate,
no npm/bundler, no separate daemon setup. One command boots the whole stack:

    pip install -e .        # once, registers the `prism-rpc` entry point
    prism-rpc --open        # starts the loopback daemon AND opens the wallet UI

Equivalent without installing:

    python -m prism.rpc.server --open     # from repo root

Then browse to http://127.0.0.1:8765/ (or whatever port `--port` sets).
The daemon (`prism/rpc/server.py`) serves both the JSON-RPC API and the
zero-build front end (`index.html` + `app.js` + `theme.css`) from the same
process; there is no second component to start.

## What "standalone" does and does not mean here
- **Standalone = one command, one process, zero external services.** True now.
  Loopback-only (127.0.0.1), devnet posture; CORS restricted to localhost origins.
- **Not yet a double-click native binary.** The Tauri/Electron shell that wraps
  these exact files into a platform installer is planned Phase-2 work per
  spec §13 ("native shell will wrap exactly these files"), and PyInstaller is
  not installed in this environment, so the packaged `prism-gui` binary has
  NOT been built or tested here. Do not claim it exists until someone runs
  `packaging/build_release.sh` on a machine with PyInstaller and verifies the
  output.
- Honest caveat for headless boxes: `--open` uses stdlib `webbrowser`; if no
  browser is registered it silently skips and you can open the URL manually.

## Roadmap answer
You do not have to wait for Phase 4 for a working GUI client. Phase 4
(spec §13) covers mainnet, SDKs, merchant toolset, DeFi primitives, and the
compliance toolkit — none of those block using the wallet UI against the
devnet daemon today.
