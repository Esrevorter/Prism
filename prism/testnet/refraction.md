# Refraction — public testnet runbook (Phase 1 exit criteria)

Genesis: `python3 -m chain.cli genesis --network refraction`
PoW: placeholder sha3-diff until RandomX FFI ships (difficulty 1 faucet).
Denylist root: embed GENESIS_ACC until Council v0 publishes weekly roots.

Exit criteria (spec §13 Phase 1):
- [x] block header w/ denylist_root field
- [x] emission + tail schedule exact-integer
- [x] adaptive difficulty retarget
- [ ] RingCT tx format + validation   <- next
- [ ] MPC keygen ceremony prototype
- [ ] NL→Intent compiler (dry-run only)
