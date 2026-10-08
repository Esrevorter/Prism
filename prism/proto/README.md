# proto/ — canonical wire formats

Single source of truth for serialization shared by node/wallet/zk verifier.
Phase 1: Python dataclasses in chain/block.py are provisional; freeze into
`.proto`/schema files when the first cross-language consumer (Rust FFI) lands.
