# Release Process

*How `prism` binaries and Python packages get built, verified, and published — including everything a Windows-first user should know about the files they download. Automation: [`.github/workflows/release.yml`](../.github/workflows/release.yml); scripts: [`packaging/`](../packaging/).*

---

## The pipeline, start to finish

```text
git tag -a vX.Y.Z  ──▶ CI triggers on tag push
                        │
        1. test gate    : pytest prism/tests (Linux/macOS/Windows × Py 3.10–3.12)
                          ❌ any failure → release blocked, artifacts never built
        2. sdist + wheel: python -m build → prism-x.y.z.tar.gz · prism-x.y.z-py3-none-any.whl
                          (wheel is pure-Python — no C extensions, no platform wheels)
        3. frozen CLIs  : PyInstaller --onedir per OS runner
                          ├─ windows-latest  → prism.exe            → prism-…-windows-x86_64.zip
                          ├─ ubuntu-22.04    → prism               → prism-…-linux_x86_64.tar.gz
                          └─ macos-14 (arm)  → prism               → prism-…-macos_arm64.tar.gz
        4. smoke test   : each binary runs --selftest (crypto + chain validation inside
                          the frozen interpreter) AND the CLI tour commands
        5. checksums    : SHA256SUMS generated fresh in-packaging-dir, embedded IN each
                          archive plus uploaded standalone; also written into release body
        6. publish      : GitHub prerelease with all assets + attached release notes
```

### Design choices worth knowing

- **Onedir > onefile:** one-file exes unpack to temp dirs at every launch — slow starts and antivirus false positives waiting to happen. Onedir zips are the tradeoff we chose deliberately.
- **Checksum hygiene:** `SHA256SUMS` is generated *fresh inside the staging dir* so paths match extracted layouts exactly (`sha256sum -c SHA256SUMS` just works), and copied into each archive before zipping — the archive you hold contains its own verification list.
- **Version single-sourcing:** `pyproject.toml`, `prism/__init__.py`, and the tag must agree or the test gate fails (`test_release_engineering.py`). No half-bumped releases.
- **Prerelease status until audits complete** — Phase-1 builds carry the pre-release flag ([Roadmap](Roadmap.md)).

## What users should do with a download

1. Check the file's SHA256 against the release page / embedded `SHA256SUMS`:
   ```powershell
   # Windows PowerShell
   Get-FileHash .\prism-0.1.0-windows-x86_64.zip -Algorithm SHA256
   ```
   ```bash
   # macOS / Linux (after extracting)
   sha256sum -c SHA256SUMS
   ```
2. Extract the archive (Windows: right-click → *Extract All…* — see [Getting Started as a User](Getting-Started-as-a-User.md)).
3. Run `prism --selftest` first; it exercises the cryptographic core end-to-end inside the packaged runtime (~seconds). If that passes, everything else is UI.

### Platform notes (honest ones)

| Platform | Status today | What's coming |
|---|---|---|
| **Windows x64** | ✅ zip w/ embedded checksums | Authenticode signing + documented SmartScreen guidance once code-signing identity completes |
| **Linux x86_64** | ✅ tar.gz | GPG-signed manifests |
| **macOS arm64** | ✅ tar.gz; unsigned dev builds trigger Gatekeeper — use *right-click → Open* once | Notarization with the same signing milestone |
| **Intel Mac** | not yet built | add matrix entry when demand warrants |

Signing/notarization gaps are stated here rather than hidden; the checksums exist precisely so an unsigned transport can still be integrity-verified by anyone.

## Maintainer checklist

```bash
# 1. Confirm version sync across pyproject.toml / prism/__init__.py, tag from main:
git tag -a v0.1.1 -m "0.1.1: <summary>" && git push origin v0.1.1

# 2. Watch Actions → "Release" run: tests → build → freeze → smoke → publish.

# 3. Post-publish sanity (on a clean machine, ideally Windows):
#    download zip → hash-check → extract → ./prism --selftest → prism params

# Local dry-run without pushing a tag:
./packaging/build_release.sh          # honors PRSM_PYTHON / PYINSTALLER env overrides
```

Rollback policy: broken artifact ⇒ immediately unlist/draft the release, note it in the next changelog, ship vNext — never overwrite published bytes.

## Changelog conventions

Release notes attach automatically from tagged annotated messages; keep a human summary in the release body: **What ships · Fixed · Known limitations (always include audit-status line) · Verify-this-download snippet.**

---

**Next:** [Testing & Quality](Testing-&-Quality.md) · [Getting Started as a Developer](Getting-Started-as-a-Developer.md) · [Wiki Home](Home.md)
