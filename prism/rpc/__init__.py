"""Prism JSON-RPC server (GUI/RPC scaffolding, spec §13 Phase-2 precursor).

Stdlib-only (http.server) so the core stays dependency-free. The daemon
wraps a single in-process PrismService (chain node + intent compiler +
MPC ceremony state) and exposes it to the GUI over JSON-RPC 2.0:

    POST /jsonrpc   {"jsonrpc":"2.0","method":"...","params":{...},"id":1}
    GET  /health    liveness probe for the GUI bootstrap

Methods (v0):
    health.ping            -> {status, version, network, tip_height}
    chain.info             -> {height, difficulty, total_shards, tip_hash,
                               cadence_s, virtual_mode}
    chain.mine             -> mine one devnet block (Refraction faucet)
    wallet.balance         -> {balance_shard, balance_prsm}
    contacts.list          -> [{contact_id, display_name, address}]
    contacts.add           -> add a Contact from the GUI form
    intent.compile         -> NL utterance -> Intent draft/confirm payload
    intent.answer          -> fold a clarification-chip answer into a draft
    intent.simulate        -> plain-language simulation preview of a draft
    intent.confirm         -> mark confirmed; returns signed-pending envelope
    mpc.ceremony_start     -> run 2-of-3 Solo keygen ceremony for a wallet
    mpc.ceremony_status    -> poll ceremony progress (staged, async-safe)
    mpc.sign_preview       -> threshold-sign a confirmation hash

Security posture for local dev: binds 127.0.0.1 only, no auth (the GUI is
a same-machine loopback client); CORS restricted to localhost origins.
Production hardening (TLS, pairing token, unix socket) is TODO(§9).
"""
from __future__ import annotations

from .service import PrismService
from .server import serve, RPCRequestHandler

__all__ = ["PrismService", "serve", "RPCRequestHandler"]
