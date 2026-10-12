# Prism GUI v2 (devnet) — lean-auth wallet

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
- `index.html` — staged single-purpose screens (welcome → create → recovery
  phrase → unlock → app), then balance hero → NL intent bar → review card →
  CAPTCHA sign step → animated receipt. Chain internals live in a collapsed
  advanced panel. Order is deliberate: a first-time user sees money-in-human-
  units and one input box; nothing else competes for attention.
- `theme.css` — "calm luxury" design system: aurora-gradient backdrop,
  glassmorphism cards, spring-physics reveals, count-up numbers, confetti
  success burst, light/dark themes, WCAG-AA contrast, 17px base, visible
  focus rings, `prefers-reduced-motion` fully honored (every animation has a
  static fallback). Neuro-Inclusive pillars from spec §UX adopted day one.
- `app.js` — thin renderer + screen router. All simulation/warning/plain-
  language strings come from the server (`auth.*` / `intent.*` payloads);
  the client never invents reassurance (honest-UX rule). Pervasive context
  help: every section carries a "?" explainer modal fed by server copy.

## Flow exercised end-to-end (Amendment A2-2026-10: MPC retired)
1. First run → `auth.create` (password) → 12-word recovery phrase shown once
   with the server's verbatim warning → `wallet.balance` hero.
2. "Get test PRSM" → `chain.mine` faucet credits coinbase emission.
3. Type "send 2 PRSM to Alex" → `intent.compile` → review card with
   predicted effects + fraud warnings rendered verbatim → `intent.confirm`.
4. Sign step A: `auth.challenge` → animated 3×3 image-grid CAPTCHA with
   countdown ring (server-supplied `expires_at`) → taps → `auth.solve` →
   single-use token bound to this exact payment digest.
5. Sign step B: password → `auth.sign` → Schnorr signature mined on the
   devnet chain → confetti receipt + `auth.history` ledger entry.
6. Locked wallet → `auth.unlock`; lost password → `auth.recover` (the 12
   words restore the SAME address — pinned by tests).
