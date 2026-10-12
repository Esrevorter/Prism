# Financial-Ethics Coding & Reporting Standard (MANDATORY)

> Prism is a financially motivated solution: it moves real value (PRSM shards, MPC-held
> keys, autonomous agent spending). This file is the binding ethical guide for all code
> and all status reporting. It complements docs/internal/rules.md §4 (Verifiable Status Reporting) and
> spec.md's honest-UX / neuro-inclusive mandate. Where this file and convenience conflict,
> this file wins. Violations are treated as defects of equal severity to data-loss bugs.

## 1. Money-Handling Code Standards
- **Fail closed:** Any ambiguity in an amount, address, capability, policy match, or
  signature check MUST result in refusal, not a best guess. A false-refusal is a UX bug;
  a false-approval is a financial-ethics violation.
- **No silent mutations:** Every state-changing path (broadcast, spend, share issuance,
  agent execution) must be reachable only through an explicit, reviewed user action
  (confirm/veto/acknowledge) recorded with a canonical digest. Server-side gating is
  authoritative; client-side checks are advisory only.
- **Amounts are integers (shards).** Float arithmetic on money is forbidden anywhere in
  the pipeline. Fiat quotes are display-only and never feed back into shard math.
- **Capability scoping is least-privilege by default.** New agent capabilities start
  deny; ACTION_TO_CAPABILITY mappings must be auditable and tested with negative cases
  (wrong action under a capability, expired capability, revoked grant).
- **Veto/revoke windows are hard guarantees.** Once vetoed or revoked, no execution path
  may proceed — including via replayed digests, restarted services, or direct RPC calls.
  Tests must prove this, not assume it.

## 2. Honest-UX Obligations (Neuro-Inclusive)
- Plain language first: every UI string a user can act on must be understandable without
  crypto knowledge; jargon requires inline explanation templates from the shipped pack.
- Never show a number the user cannot interpret (raw shards without a fiat/unit label,
  hashes without "what this identifies").
- Risk bands drive copy: interrupt-band actions must visibly say what does NOT happen
  ("Nothing has been sent") before offering any proceed path.
- No dark patterns: no pre-checked acknowledgements, no hidden fees, no countdown
  pressure, no burying the veto/revoke control below the fold.

## 3. Reporting Ethics (extends docs/internal/rules.md §4)
- **Financial claims carry proof or don't exist.** Test counts, benchmark FPR/TPR, run
  times, commit hashes, remote tips: quote the observed output verbatim or mark the item
  UNVERIFIED. "Should pass" is banned language.
- **Distinguish shipped vs scaffolded.** A UI screen that exists in code but was never
  rendered/observed is reported as "code present, not visually verified" — never as done.
- **Disclose known failures loudly.** If the last observed smoke-test line was a refusal,
  the status says so, even if fixes were written afterward.
- **Self-audit gate:** Before sending any status message, re-check each claim against a
  tool output from THIS session. Claims about prior sessions require fresh inspection.

## 4. Process Requirements
- Every financial-behavior change ships with: (a) at least one positive test, (b) at
  least one negative/refusal test, (c) evidence line pasted into spec.md §Phase notes.
- Security-sensitive code (keys, shares, signatures, settlement) gets differential or
  property-based testing where feasible; otherwise the gap is stated explicitly in the
  PR description.
- Credentials/tokens used in-session are scrubbed from logs/output and flagged to the
  user for rotation at session end.
