# Security Policy

Prism PRI is a privacy-focused cryptocurrency project. We take the security of the
protocol, wallet software, and release pipeline seriously — and we follow
**coordinated disclosure**: give us time to fix before anything becomes public.

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security problems.**

Use one of these private channels:

1. **GitHub Private Security Advisory (preferred)**
   Go to https://github.com/Esrevorter/Prism/security/advisories/new
   This creates a *private* draft advisory visible only to you and the maintainers.
   You can even propose a fix via a private fork directly from that flow.

2. **Encrypted email**
   security@prism-coin.org *(PGP key below)* — if the address is not yet live in
   your region or the PGP step fails, use the advisory flow above instead; it is
   always available.

We acknowledge receipt within **3 business days**, triage within **7 days**, and
aim to ship a fix or mitigation within **30 days** for confirmed issues, depending
on severity.

### What to include

- Affected component (`prism/auth`, `prism/chain`, RPC layer, GUI, packaging…)
- Version / commit (`prism --version`, or the git SHA you built from)
- Step-by-step reproduction (a failing test or script is ideal)
- Potential impact and any suggested mitigation

## Scope

In scope:

- Wallet & keystore (`prism/auth/keystore.py`) — e.g. KDF weaknesses, key
  material leakage, recovery-phrase flaws, lockout bypass
- CAPTCHA signing gate (`prism/auth/captcha.py`) — e.g. token forgery, replay
  across intents, challenge-answer leakage to logs or telemetry
- Consensus & chain rules (`prism/chain`) — double-spend, inflation, reorg
  safety, fee-handling bugs
- RPC / service layer (`prism/rpc`) — auth-bypass, path traversal, injection
- Release pipeline & attestation — artifact tampering, signature bypass,
  provenance spoofing
- GUI (`prism/gui`) — XSS or other vectors that could manipulate a signing
  review screen (the "what you approve is what you see" promise)

Out of scope (please don't report as vulnerabilities):

- Attacks requiring physical access to an unlocked machine with the sealed key
  already exposed
- Social engineering of users (password/phrase phishing) — real risk, but not a
  software flaw; covered by our honest-UX warnings
- Third-party OS/platform issues outside our shipped code
- Theoretical attacks against primitives documented as Phase-2 work in spec.md

## Our Own Honest Limitations

Per the project's honesty rules: the CAPTCHA gate proves **human presence**, not
identity; the password protects the key **at rest**. A weak password plus a
stolen sealed file is a funded theft — this is documented behavior, not a bug.
What *would* be a bug: anything that lets a transaction sign without both gates,
leaks the spend key or recovery phrase, or bypasses consensus validation.

## Bounties & Recognition

This is a pre-mainnet reference implementation; there is no paid bounty program
yet. We will:

- Credit reporters in the release notes and advisory (unless you prefer anonymity)
- Add a named entry to the wiki Hall of Contributors
- Revisit formal bounties at mainnet candidate

## PGP

Key ID and fingerprint are published on the wiki Security Model page once the
release key ceremony is completed (see docs/HANDOFF.md, open threads). Until
then, GitHub private advisories are the recommended channel.

---
*This policy itself may have flaws — improvements are welcome via ordinary PR.*
