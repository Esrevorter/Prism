# Prism GUI v0 (devnet)

Zero-build front end served by the RPC daemon itself:

    python -m prism.rpc.server            # http://127.0.0.1:8765

Open that URL — index.html + theme.css + app.js are served from this
directory (`/`, `/static/*`). No npm, no bundler; the native shell
(Tauri/Electron) will wrap exactly these files and talk to the same JSON-RPC
API, so nothing here is throwaway. Per spec §13 "Double-click installer
mandate" (2026-10-11) that native installer is a **mandatory** acceptance
requirement (download → install → run, zero terminal), not optional Phase-2
scope. End users today should use the zero-argument `prism-gui` launcher
(see STANDALONE.md); the command above is power-user/dev detail only.

## Layout
- `index.html` — balance hero → NL intent bar → plain-language review card →
  contacts → collapsible node panel (advanced). Order is deliberate: a first
  time user sees money-in-human-units and one input box; chain internals are
  opt-in.
- `theme.css` — design tokens (light/dark, WCAG-AA contrast, 17px base,
  visible focus rings, prefers-reduced-motion honored). Neuro-Inclusive
  pillars from spec §UX adopted from day one.
- `app.js` — thin renderer. All simulation/warning/plain-language strings
  come from the server (`intent.compile` payloads); the client never invents
  reassurance (honest-UX rule).

## Flow exercised end-to-end
1. "Get test PRSM" → `chain.mine` faucet credits coinbase emission.
2. Type "send 2 PRSM to Alex" → `intent.compile` → review card with
   predicted effects + fraud warnings rendered verbatim.
3. Vague utterance ("pay mom") → clarification chips → `intent.answer`.
4. "Looks right — confirm" → `intent.confirm` → `mpc.sign_preview` shows the
   2-of-3 threshold signature proof + the "never Prism holds your key" line.
5. "Set up device keys" → `mpc.ceremony_start` staged progress.
