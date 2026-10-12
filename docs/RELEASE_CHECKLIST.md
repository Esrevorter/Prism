# Prism Release Checklist (v0.1.10+)

A tag is only cut AFTER the tree passes every gate below. v0.1.7/v0.1.8 shipped
broken because tags were cut before version bumps landed and artifacts were
force-added past .gitignore. Never repeat that.

1. `python -m pytest prism/tests -q` -> must be fully green (523+ passed).
2. `python scripts/release_gate.py` -> must exit 0. It checks:
   - no tracked build artifacts (`build/`, `dist/`, `__pycache__`, `prism-*-*/`)
   - ALL version sources agree (pyproject.toml, prism/chain/__init__.py,
     tauri.conf.json, src-tauri/Cargo.toml, package.json + package-lock)
   - captcha wire contract: challenge ships visible `tiles`; answer keys
     (`target`, `answer`) NEVER cross the wire.
3. Bump version in ALL five places (release_gate lists them if they differ):
   pyproject.toml, prism/chain/__init__.py, packaging/tauri/prism-gui/
   src-tauri/tauri.conf.json, .../src-tauri/Cargo.toml, .../package.json
   (and package-lock.json top-level "version" entries).
4. `python scripts/sync_gui.py` after touching prism/gui/* so prism/rpc/static
   matches (GUI class contract test compares both).
5. Commit the bump; push to main; open/merge PR; CI must pass on the merge commit.
6. ONLY THEN: `git tag vX.Y.Z <merge-commit>` and push the tag. The Release
   workflow builds assets named from pyproject.toml — step 3 guarantees they
   match the tag.
7. Verify the GitHub Release page: title vX.Y.Z, assets all contain vX.Y.Z in
   their filenames. If any asset name mismatches, delete the release AND tag
   immediately (releases below the newest good one may be deleted outright).
8. Any GUI change: check the css-class contract test covers new classes
   (test_release_hygiene.test_gui_class_contract) — missing CSS caused the
   "nineties look" regression.
