# Prism Wallet — Attestation, Signing & Notarization Plan

Status: **design + CI wiring landed; live certificate flows pending founder accounts.**
This document states honestly what each mechanism does and does not prove —
in keeping with the spec's honest-UX rules (Amendment A2).

## Why this matters

Modern OSes gate untrusted binaries hard:

| OS | Gate if unsigned/un-notarized | Our v0.1.4 experience |
|---|---|---|
| Windows 10/11 | SmartScreen "unknown publisher" warning; some AVs flag PyInstaller bundles | Users must click "More info → Run anyway" |
| macOS 12+ (Gatekeeper) | `.dmg` from the internet is quarantined; **un-notarized apps are blocked outright** ("can't be opened") | Works only via right-click Open on some configs; will break harder on future macOS |
| Linux (.deb/.AppImage) | No OS-level gate | Fine as-is |

## 1. macOS — Developer ID + notarization (feasible, needs one Apple account)

**Requirement:** Apple Developer Program membership ($99/yr) — any plan works for
Developer ID + notarization (the $500 enterprise tier is NOT needed; that rumor
confuses it with private-API licensing).

**Flow (already wired into `desktop.yml`, activates when secrets exist):**
1. Build `.app`/`.dmg` as today (`tauri-action`).
2. `codesign --timestamp --options runtime --sign "Developer ID Application: <name>" ...`
   (hardened runtime required by notarization).
3. Upload artifacts to Apple's notary service (`xcrun notarytool submit --keychain-profile`).
4. `stapler staple` the ticket onto the `.dmg` so offline Gatekeeper checks pass.
5. Verify in CI: `codesign --verify --deep --strict` and `spctl -a -vv`.

**CI secrets:** `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_SPECIFIC_PASSWORD`
(or an API-key pair: `APPLE_KEY_ID`, `APPLE_ISSUER_ID`, base64 `.p8`).
Alternative: Tauri's built-in signing (`TAURI_SIGNING_PRIVATE_KEY`) covers the
*updater payload*, not Gatekeeper — we need both eventually.

**Honest note:** notarization proves *who published the binary* (Apple verified
the Developer ID), not that the code is bug-free or that keys can't leak.

## 2. Windows — code signing certificate (feasible; pick your trade-off)

| Option | Cost | SmartScreen reputation | Notes |
|---|---|---|---|
| **Azure Trusted Signing** (recommended) | ~$9.99/mo org verification | Reputation accrues naturally; no EV hardware | Microsoft-operated; works headless in GitHub Actions via OIDC/federated identity — no HSM to babysit |
| EV code-signing cert (DigiCert/GlobalSign/SSL.com) | ~$200–700/yr + USB token/HSM | Immediate SmartScreen bypass | Physical token complicates CI (needs cloud-HSM variant, e.g. KeyVault/eSigner, to sign autonomously) |
| OV (standard) cert | ~$100–300/yr | Slow reputation build (thousands of downloads) | Cheapest real option; warnings persist early on |
| Self-signed / sigstore-only | free | None (warning remains) | Still valuable for *integrity provenance* (see §4), not for Gatekeeper UX |

**Flow (wired into `release.yml`/`desktop.yml` when secrets exist):**
`signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /n "Prism" file.exe`
— applied to the sidecar `.exe` **before** bundling and to the final `.msi`/setup `.exe` after.

## 3. Linux — no OS gate, but offer provenance anyway

- `.deb`: sign the Release file with `debsign`/`dpkg-scanpackages` + maintainer GPG key (repo users verify with `apt-secure`).
- Flatpak (optional later): Flathub requires its own GPG key signing — nice middle ground for Linux distribution with attestation.
- AppImage: already supports embedded GPG signatures (`appimage-updateinformation`); Tauri emits updater signatures when configured.

## 4. Cross-platform transparency — cosign/sigstore (free, no certs needed)

Independently of OS gates, every release asset gets a **cosign keyless signature**
bound to the GitHub OIDC identity (`Esrevorter/Prism` + tag), plus an entry in
Rekor's public transparency log. Users verify with one command:

```bash
cosign verify-blob --signature prism_0.1.5_linux-x86_64.zip.sig \
  --certificate ... --certificate-identity-regexp 'https://github.com/Esrevorter/Prism' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com prism_0.1.5_linux-x86_64.zip
```

This gives auditable build provenance (SLSA-style): *this exact bytes were built
by this repo's Actions at this commit* — without paying anyone. Cheap win;
add a `verify.sh` helper to the Release notes.

## 5. What attestation does NOT cover (honesty per spec §7.9.4)

No OS-level signing answers the question *"is this wallet code safe with my
money?"*. It proves publisher identity + integrity in transit only. The real
assurance path remains: reproducible builds (hash the frozen binary in CI logs),
open source review, and the testnet soak regime (P1–P6 parity tests).

## Recommended order (effort vs. user-visible payoff)

1. **cosign/sigstore on all assets** — free, no accounts, immediate provenance. *(next PR)*
2. **macOS notarization** — single $99 Apple account; unblocks clean Gatekeeper UX. Biggest pain-killer for Mac users.
3. **Azure Trusted Signing for Windows** — cheap, CI-friendly, removes SmartScreen scare over time.
4. Debian repo signing / Flatpak — once there's a stable apt/Flathub channel.
5. Auto-updater signing (Tauri plugin + minisign keypair) — before post-mainnet patch cadence.

## Current CI state (as of this doc)

- `desktop.yml`: hardened-runtime codesign + notarytool submit/staple steps run **only if** `secrets.APPLE_ID` etc. are present; otherwise job logs "skipped (no Apple credentials)" and ships unsigned artifacts exactly as today.
- `release.yml` + `desktop.yml`: Windows `signtool` step runs **only if** `secrets.WINDOWS_PFX_BASE64` (+ password or Azure Trusted Signing federation vars) are present.
- All artifacts always carry `SHA256SUMS`; cosign step added per §4 when enabled.

No secret values are ever committed; everything is gated on repository Secrets settings.
