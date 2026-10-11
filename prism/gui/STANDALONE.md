# Running the GUI wallet standalone

The GUI wallet is **already runnable standalone today** — no Phase-4 gate,
no npm/bundler, no separate daemon setup. End users should never have to
think about ports, hosts, daemons, or flags; that is exactly what the
`prism-gui` launcher automates (Electrum / Bitcoin Core style: the app
manages its own backend).

## The end-user path (one action, zero configuration)

    pip install -e .        # once, registers `prism-gui` + `prism-rpc`
    prism-gui               # THAT is the whole user-facing command

What `prism-gui` does with no arguments at all:
1. picks a free loopback port automatically (no "address already in use");
2. creates a private per-user data dir `~/.prism/wallet/` (mode 0700) and
   marks it with `.prism_wallet`, so opening a terminal inside that folder
   and running `prism-gui` reopens THIS wallet instead of a fresh one;
3. single-instance guard: if your wallet is already running, it just opens
   the existing UI in your browser instead of starting a second daemon
   (stale locks from crashed sessions are detected via health probe and
   reclaimed);
4. starts the JSON-RPC + GUI daemon in-process (same trusted code path as
   `prism-rpc`; nothing to orphan on quit);
5. opens the wallet UI in your default browser **only after** the daemon's
   `/health` endpoint confirms it is listening — no blank-page race.

Equivalent without installing:

    python -m prism.rpc.launcher          # from repo root, same behavior

## Power-user path (unchanged, still available)

    prism-rpc --open          # fixed port 8765, opens browser immediately
    python -m prism.rpc.server --open
    prism-rpc --port 9000     # explicit host/port for scripted/dev setups

Then browse to http://127.0.0.1:8765/ (or whatever port `--port` sets).
The daemon (`prism/rpc/server.py`) serves both the JSON-RPC API and the
zero-build front end (`index.html` + `app.js` + `theme.css`) from the same
process; there is no second component to start.

Evidence (2026-10-11): bare zero-arg launch verified live over a real
socket — auto-selected port, `GET /health -> {"status": "ok"}`,
`GET / -> 200, 5165 bytes` (real GUI bundle), `health.ping` JSON-RPC round
trip against the launcher-owned service instance. Automated coverage:
`prism/tests/test_gui_launcher.py` — 14 tests (data-dir privacy/marker,
free-port selection, lock acquire/release, second-instance deferral,
stale/garbage lock reclaim, health-probe refusal cases, per-user service
isolation, full bare-run end-to-end incl. auto-open).

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
