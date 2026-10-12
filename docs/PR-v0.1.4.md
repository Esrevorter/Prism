# Prism v0.1.4 — Amendment A2: Lean Auth replaces MPC

## What changed
- **MPC ceremony retired** (FROST 2-of-3, device pairing, GG20) → **lean authentication**: password-sealed local spend key (`prism/auth/keystore.py`, scrypt envelope, lockout after 5 failures, address-invariant 12-word recovery phrase) + image-grid CAPTCHA gating every transaction (`prism/auth/captcha.py`, HMAC token bound to the exact intent digest, 120s TTL, single-use).
- **RPC wire contract**: `mpc.*`/`recovery.*` endpoints removed; full `auth.*` surface added (status/create/unlock/lock/recover/change_password/challenge/solve/sign/history). All money-moving paths require valid CAPTCHA token AND correct password; signatures self-verified before broadcast.
- **GUI v2 "calm luxury"**: staged payment flow (Review → animated CAPTCHA grid with countdown ring → unlock/sign → confetti receipt), glassmorphism + aurora theming, count-up balance, skeleton loaders, reduced-motion fallbacks, pervasive context help (9 explainers incl. honest presence-vs-identity framing), dark/light themes.
- **Testnet parity**: real ChainState validation on ALL networks, testnet faucet wallet credited on mined blocks, network-aware RPC/launcher CLI, atomic keystore persistence across restarts, new `test_testnet_parity.py` (10 pins incl. 10-payment soak cross-validated by a fresh production validator).
- **spec.md v1.2**: Amendment A2-2026-10 fully written — §7 rewritten for lean auth UX, §7.9 founder UI/UX mandate made binding ("beautiful, graphically stunning… animations… lots of context help"), §9 replaced, §11 E8/E9 updated, §13 Phase-1 marked superseded (evidence preserved), §14 decision-log row incl. market rationale (BTC ≈$114k, privacy coins risk-off → friction-free onboarding as adoption lever).
- **CI**: new `ci.yml` (pytest matrix Ubuntu/macOS/Windows + installer dry-run); **macOS release fix** — PyInstaller now uses the runner's framework CPython (`cpython-3.12`) which resolves the v0.1.3 failure "Unable to find Python.framework/Versions/3.12/Python". Legacy MPC modules kept in-tree but unwired (historical evidence reproducible).

## Verification
- Full suite at tag tree: **511 passed, 7 skipped** (incl. fuzz + property tests).
- Frozen Linux binary smoke-tested through create→intent→CAPTCHA→sign over real HTTP socket.
- All three workflow YAMLs validated.

## Release artifacts (triggered by this tag)
`release.yml`: prism CLI binaries (win x64 / mac universal2 / linux x64) + wheel + sdist.
`desktop.yml`: Tauri installers (.msi/.exe, .dmg, .deb, .AppImage) + SHA256SUMS attached to Release v0.1.4.
