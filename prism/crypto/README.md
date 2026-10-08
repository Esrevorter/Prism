# crypto/ — primitives (Phase 1)

Scope per spec §5.1: Ed25519-compatible curve ops, Pedersen commitments
`C = v·H + r·G`, Bulletproofs+ range proofs, CLSAG ring signatures, stealth
addresses, key images. First deliverable: hash-to-curve + commitment ABI;
second: pure-Python reference CLSAG (slow but test vectors), then a Rust/FFI
fast path. Blocks on nothing — start immediately.
