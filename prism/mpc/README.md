# mpc/ — forgivable self-custody (Decision D4: purely user-owned)

GG20-style t-of-n ECDSA over secp256k1? NO — Ed25519 GG20 variant or LDA19.
Deliverables: keygen ceremony, resharing, social-recovery protocol with
72h timelock (§7.3), duress/decoy wallets (§7.5). Solo mode = user devices
only; Prism holds no share, blind or otherwise (D4 hard constraint).
