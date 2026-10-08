# zk/ — Prism Protocol circuits (Decision D2: PLONK/ultra-honk)

Five v1 circuits (spec §5.3): provenance, solvency, income-attribution,
reserve, clean-exit. Toolchain candidate: halo2 (ultra-honk fork) or gnark.
Includes SRS ceremony tooling (universal SRS, published transcripts).
Depends on crypto/ for the commitment encoding the circuits re-state.
