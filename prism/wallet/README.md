# wallet/ — intent compiler & local store

Implements the Transaction Intent contract (spec §6.2): NL layer and signer
communicate ONLY through signed Intents; the simulator is ground truth
before any key share signs. Includes view-key scanner and disclosure registry.
