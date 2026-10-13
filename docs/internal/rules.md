# Tool Calling and Execution Rules

> **Rules file version: v1.6 (2026-10-13).** This version stamp exists because releases
> v0.1.6–v0.1.10 kept shipping "fixed" claims while the user saw an unchanged app — a rules
> file with no version cannot be proven to have been loaded by any given session. If a fix
> session's plan does not cite this number, its rules were probably not read at all.
>
> **v1.6 changelog (docs alignment pass, Oct 13):** deep-dive audit of the governance
> files found concrete drift and one fabrication-inducing conflict. Fixed: (a) qwen.md
> "Chaining & Automation Protocol" previously said "Run/simulate tests" and "do not
> stop until 100% complete" — wording that licensed narrating imagined results; it now
> defers to §4/ethics.md §3 (qwen.md v1.1). (b) Stale claims corrected across docs:
> HANDOFF §7 still claimed fine-grained PATs cannot write issue milestones; direct
> API read on Oct 13 confirmed #121–#124 ARE attached to Milestone #4 (so the old claim
> was doubly wrong: attaching works, AND it had already been done); (c) Wrong paths in HANDOFF
> §2/§3 (`tests/` → actual `prism/tests/`; `desktop/` → actual `packaging/tauri/`)
> and RELEASE_CHECKLIST step 4 (`scripts/sync_gui.py` → actual `packaging/sync_gui.py`)
> would make any agent following them fail a command and be tempted to "report it as
> if run". (d) Contradictory test counts (511 vs 523+ vs 524) replaced with a rule:
> quote only the count from the run you just observed. (e) Precedence chain made
> explicit everywhere it is stated (see internal/README.md): spec.md > ethics.md §3 ≈
> rules.md §4 (integrity contract, overrides all) > rules.md execution limits >
> qwen.md pacing numbers. The old blanket "qwen.md wins" clause is narrowed to pacing
> NUMBERS only — it must never override integrity rules.
>
> **v1.5 changelog:** v0.1.13 brand pass — the shipped icon files were a uniform near-black
> tile (verified: single colour #0D1117 across all 262k pixels, zero glyph), which is why the
> Windows taskbar showed a black square. Brand is now generated from `scripts/make_icon.py`
> (golden serif P on dark rounded tile, SVG master rasterised at 1024px); every icon file
> (.ico multi-res, .icns, Tauri PNG set, Store tiles) comes from that one source, and
> `release_gate.check_brand_icon` fails any release whose icons regress to a blank tile.
>
> **v1.4 changelog:** §7 added — confirmed root cause of the multi-release "nothing changed"
> saga (stale per-user data dir + cached GUI/lock), the real cross-platform data-directory
> locations for support triage, and GitHub-milestone hygiene rules ahead of Phase 4
> (production). The v0.1.12 cache headers + build stamp are the standing remedy; §7 tells
> support staff where on disk to look when a user still reports an old UI.

> **Release-versioning rule (MANDATORY since v1.3, added after the v0.1.10 report):**
> Every release commit MUST bump the version in *all* of these locations in one change,
> and the release gate MUST verify tag == tree == UI-visible version:
> 1. `pyproject.toml` (`version = "x.y.z"`)
> 2. `prism/chain/__init__.py` (`__version__`)
> 3. `packaging/tauri/prism-gui/package.json`, `src-tauri/Cargo.toml`, `src-tauri/tauri.conf.json`
> 4. `README.md` release badge (historically forgotten — still shows v0.1.5 as of v0.1.10)
> 5. A build stamp visible in the GUI itself (About/footer), so users and bug reports can
>    prove which build is actually running. Without #4/#5, "nothing changed" complaints are
>    undiagnosable: stale installers, cached bundles, and unfixed code look identical.

> **Binding companion files (re-read before every substantive task):** `docs/internal/qwen.md`
> (anti-burst pacing — one tool call per turn, sequential only, ≥20 s inter-request;
> Oct 11 addendum, narrowed Oct 13: if this file and `docs/internal/qwen.md` ever disagree
> on *pacing numbers*, qwen.md wins — but qwen.md can NEVER override §4 or ethics.md §3
> integrity rules; precedence chain lives in `docs/internal/README.md`),
> `docs/internal/ethics.md` (financial-ethics coding & reporting standard — fail-closed money handling,
> proof-or-UNVERIFIED status claims). rules.md §4 and ethics.md §3 together form the
> integrity contract: no status message may be sent without a self-audit against observed
> tool output from the current session.

## 1. Tool Call Limits & Efficiency
*(v1.3 adjustment: the old "2-3 calls per turn" cap was tuned for chat UX, but it made
diagnosis-heavy fix tasks (captcha/interface bugs) give up after one or two probes and fall
back to assumptions — exactly how unverified "fixed" claims survived to release. The budget
below keeps burst protection while leaving room for real verification work.)*

- **Maximum Consecutive Calls:** Never call tools more than 5 times in a single response turn for exploratory reads; for chained verification commands (§5 "Chain Everything") count each chain as ONE call. If a solution genuinely requires more steps, summarize findings so far and ask the user for permission to proceed. *(Exceptions: tidying/cleanup sessions — see §5 — do NOT stop to ask; batching is mandatory there. Verification runs required by §4 evidence rules never count toward the stop-to-ask threshold.)*
- **Batching:** If you need to perform multiple actions (e.g., viewing multiple files or running multiple terminal commands), batch them into a single tool call whenever possible instead of calling them sequentially. Batch aggressively: chained commands (`cmd1 && cmd2 && cmd3`, multi-file `grep`/`sed -n`) count as ONE request toward the qwen.md pacing/breather budget.
- **Parallel-Call Prohibition (overrides any client default):** Even if the agent client supports emitting multiple independent tool calls in one block, never do so against Qwen3-Coder — two simultaneously dispatched requests trip the provider's "Too many concurrent requests" limit. Emit exactly one tool call, await its full result, then proceed per qwen.md pacing.

## 2. Strict Stopping Criteria
- **Loop Prevention:** If a tool returns the exact same error or result twice, DO NOT call it a third time. Stop immediately, explain the issue to the user, and ask for manual intervention.
- **Diminishing Returns:** If the first tool call provides 80% of the required context, do not make subsequent calls for minor details. Rely on your internal knowledge base to fill in non-critical gaps.

## 3. Mandatory Pre-Conditions
- **Think Before You Call:** Before invoking any tool, explicitly state in your internal thoughts (or a short text response) *why* the tool is necessary and *what* specific piece of missing information you expect to gain.
- **No Redundant Calls:** Never call a tool to search for information that has already been provided in the conversation history or the initial prompt.

## 4. Verifiable Status Reporting — Absolute Integrity Rule (MANDATORY)
*Grounded in the project's core principle: users rely on honest, plain-language status because they cannot audit every internal step. A fabricated status report voids that trust and directly violates the neuro-inclusive / honest-UX mandate in spec.md. This section overrides any convenience, brevity, or optimism pressure.*

- **Reality-Only Claims:** Every statement about work being done, passing, committed, pushed, merged, or verified MUST be backed by an actual tool result observed in the current session (e.g., exit code + output of the command run, `git log`/`git status` showing the commit, a fetch confirming the remote tip). No exceptions.
- **Never Predict Results as Facts:** Do not describe a test outcome, benchmark number, commit hash, push result, or PR state before the command that produces it has actually completed and its output has been read. If a job was launched but its result was not observed, report it as "launched, result unknown" — never as passed/failed.
- **No Carrying Over Assumed State:** Previous turns' summaries are NOT evidence of current state. Before asserting anything about files, commits, branches, remotes, or test results, re-inspect the actual repository/environment state with a tool call. Context may have been truncated; sessions may have stalled mid-operation.
- **Distinguish Intent from Achievement:** Phrases like "next steps include…" must be clearly separated from "completed:". A plan is never reported as progress. Partial completion must be labeled PARTIAL with an explicit list of what is done (each item tied to its evidence) and what is not done.
- **Fail Loud, Not Gracefully:** If a verification step fails, times out, or cannot be run, say so plainly with the exact error/output. Do not soften, omit, or substitute an assumed value. Never claim "should pass" or "expected green" as a substitute for observed green.
- **Evidence Citations in Status Reports:** When reporting completion of a substantive task, include the concrete artifact of proof inline (test count + duration line, commit hash from `git log`, remote tip from a fresh fetch, benchmark summary line). If the artifact does not exist yet, the task is not complete.
- **Self-Audit Before Sending:** Before any status/summary response, scan each factual claim and confirm a corresponding observed tool output exists in this session. Any claim that fails this check must be removed or restated as unverified.
- **Zero-Tolerance Retroactive Correction:** If a hallucinated or unverified claim is discovered (by self or user), immediately correct the record with verified facts, re-run the missing verifications, and note the correction prominently rather than quietly editing the narrative.

## 5. Tidying / Cleanup Sessions — Low-Concurrency Protocol
*Added Oct 11, 2026 after repeated provider errors "Oops! There was an issue connecting to Qwen3-Coder. Too many concurrent requests." during tidying tasks. Root cause: tidy-up workflows naturally fan out into many small reads/status checks (git status, ls, per-file views, linters), which either overlap in-flight or sustain a request cadence above the burst-rate threshold.*

- **Inventory First, One Pass:** Start every tidying task with exactly ONE batched inventory command (e.g., `git status --short && git diff --stat && ls -la`) instead of probing files individually. Plan the entire cleanup from that single output; do not re-inspect unless the plan changes.
- **Chain Everything:** Combine related operations into chained shell commands (`git add && git commit`, `sed -i` across multiple listed files in one call). Each chain counts as one request; target ≤ 5 total tool calls for a typical tidy-up.
- **Never Fan Out:** Do not issue parallel/per-block multi-tool calls even when the client allows it (see §1 Parallel-Call Prohibition). Tidy-up steps are strictly sequential with full qwen.md pacing between them (≥20 s steady state, ≥30 s after heavy output, 60 s breather every 8th call).
- **Skip Optional Verification Steps:** During tidying, suppress nice-to-have checks (redundant `git log`, repeat `ls`, full test suites) unless spec.md/ethics.md §4 requires evidence for a status claim. When evidence IS required, prefer one targeted check over several broad ones.
- **On "Too many concurrent requests":** Stop immediately, do NOT auto-retry inline. Wait the full 60 s (then 120 s → 240 s exponential per qwen.md Error Recovery), resume with the smallest possible status check, and continue at doubled inter-request delay (≥40 s) for the rest of the session. Report the stall plainly to the user rather than hammering retries — premature retries extend the throttle window.

## 6. "Fix Shipped But User Sees No Change" — Root Cause & Mandatory Checklist
*Added v1.3 (Oct 12, 2026) after the founder reported: "nothing has been fixed in v0.1.10, it looks exactly the same as all previous versions: broken captcha, ugly interface, despite all made claims." Honest post-mortem of what was and was not verifiable:*

**What inspection proved (verified this session):**
- The repo tree at tag `v0.1.10` DOES contain real captcha/UI fixes: `prism/auth/captcha.py` wire-stamp contract, `app.js` "build out of date" loud error + single-SVG puzzle, `gui/ ↔ static/` bundles byte-identical (`diff` exit 0), version 0.1.10 present in pyproject.toml, chain.__init__, package.json, Cargo.toml, tauri.conf.json.
- What is NOT provable from inside this repo: which binary the user actually launched. Release artifacts are built by CI on GitHub; no session here observed a successful build, uploaded asset, or installed app.

**Root-cause candidates, in order of likelihood:**
1. **Stale installer / no auto-update:** the user runs an old frozen bundle (the very failure mode v0.1.10 tried to patch server-side). Old GUI + new server = unchanged appearance. Nothing in the UI displayed a version, so neither user nor agent could tell (now mandatory: rules header above, item 5).
2. **Release assets never rebuilt/published:** a git tag does not rebuild binaries. If CI failed or assets were not replaced, v0.1.10 downloads ARE v0.1.9 content under a new tag. Claiming "fixed in v0.1.10" without fetching the published asset and checking its embedded version violates §4 (Never Predict Results as Facts).
3. **The claimed fixes addressed the wrong layer:** prior sessions asserted captcha/UI defects were fixed based on green tests in the repo, never on a screenshot/rendered DOM of the shipped app. Tests passing ≠ user-visible change.

**Mandatory pre-release checklist (blocks any "fixed" claim):**
- [ ] Run `scripts/release_gate.py` AND paste its actual output (§4 evidence citation).
- [ ] Fetch the published release asset list (`gh release view` or API) and confirm the asset timestamp is AFTER the fix commit — a tag alone proves nothing.
- [ ] Download the shipped artifact and verify its embedded version string equals the tag (this is the step every prior release skipped).
- [ ] Add/update the visible version stamp in the GUI About/footer so future reports can be triaged by build.
- [ ] In status reports, separate: "fixed in code (evidence: commit)" from "shipped to user (evidence: asset hash + embedded version)" from "user experience changed (evidence: rendered screenshot of the shipped app)". Only the third justifies the word "fixed" to a non-technical user; otherwise say PARTIAL.
- [ ] If any of the above cannot be run in-session, report UNVERIFIED explicitly — do not soften (§4 Fail Loud).


## 7. Support Triage: Where the Wallet Actually Lives on Disk (v1.4)
*Added Oct 12, 2026 after the saga finally closed. The founder's "nothing has ever been fixed"
complaint (v0.1.6 → v0.1.11) had TWO stacked causes, both now confirmed by evidence:*

1. *Release-side (fixed in code): assets cached by WebView2 because old servers sent no
   `Cache-Control` headers — remedied in v0.1.12 (`no-store` on all GUI responses +
   stale-bundle tripwire banner in app.js).*
2. *User-side (confirmed live): a **stale per-user data directory** kept serving/locking the
   old state. Deleting the wallet dir and the stale `gui.lock` is what finally made the new
   UI appear. Until this session we had documented `%LOCALAPPDATA%` guesses that were WRONG
   for this build — the real folder was under the user's home directory.*

### 7.1 Confirmed data-directory locations (verify before quoting to users)
| Platform | Location | Notes |
|---|---|---|
| Windows 11 (this build) | `C:\Users\<name>\.wallet\` | Founder-confirmed Oct 12, 2026. Contains `prism_wallet.json`, chain data, and `gui.lock`. Hidden dot-folder; Explorer needs "Hidden items" ON or Win+R → `%USERPROFILE%\.wallet`. Do NOT send users hunting for `%LOCALAPPDATA%` — it exists on every Windows install but is not where Prism keeps its data. |
| macOS / Linux | `~/.wallet/` (POSIX home) | Same layout; launcher default root is `os.path.expanduser("~")`-based (`prism/rpc/launcher.py`: `DEFAULT_ROOT = ~/.prism` for the daemon; the desktop wallet uses the `.wallet` home dir). |
| GUI mutex | `<data dir>/gui.lock` | Zero-byte mandatory-lock file. A lock left behind by a crashed/killed instance can make the app appear frozen on an old build. Removing it while NO instance runs is safe; never remove it as a first resort without checking for running processes. |

### 7.2 Support script for "the update didn't change anything" (plain language, neuro-inclusive)
1. Look at the footer FIRST: it must read `Prism v<release> · ui <fingerprint> …`.
   - No version prefix at all → old build is running; continue below.
   - Version present but wrong → report the exact string + OS; that is a release-gate bug (§6 checklist was skipped).
2. Fully quit Prism (check tray/process list), then delete ONLY these two things from the
   data dir above: the stale `gui.lock`, and — after exporting/backing up the seed phrase —
   the wallet state files. Reinstall the newest installer from the GitHub Releases page.
3. If the footer still shows no stamp after steps 1–2, escalate: the artifact itself is
   suspect; re-run §6 checklist against the published asset, do not improvise fixes.

### 7.3 GitHub milestone hygiene (MANDATORY from Phase 4 onward)
*Observed Oct 12, 2026: the project is in Phase 4 (production start) yet the repo's only
milestone is "Phase 1". Milestones are how contributors know what is current; a single stale
milestone is worse than none — it advertises activity that ended months ago.*
- Every phase transition MUST create the matching milestone (`Phase N – <name>`) BEFORE the
  first issue of that phase is opened, and close the previous phase's milestone.
- Every release tag gets assigned to exactly one milestone; every closed PR merged toward a
  release is linked to that release's milestone item.
- At each release cut, run `gh api repos/{owner}/{repo}/milestones --jq '.[].title'` and
  confirm the open set matches the Wiki Roadmap phases; a mismatch blocks the release
  (same tier as §6 checklist items — they are gates, not suggestions).
- Historical phases stay visible as CLOSED milestones (evidence trail, like retired MPC
  modules); never delete milestones, only close them.
