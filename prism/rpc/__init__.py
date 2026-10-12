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
    auth.status            -> {has_wallet, unlocked, address, ...} (spec §9 A2)
    auth.create            -> password-seal a new spend key; returns 12-word phrase once
    auth.unlock / lock     -> open/close the sealed key in memory
    auth.recover           -> restore from the 12-word phrase (address-invariant)
    auth.change_password   -> re-seal the same key under a new password
    auth.challenge         -> image CAPTCHA bound to a confirmed intent digest
    auth.solve             -> submit grid picks -> single-use signing token
    auth.sign              -> token + password -> signed, mined payment (only path that moves money)
    auth.history           -> recently signed transactions for the GUI ledger view

Security posture for local dev: binds 127.0.0.1 only, no auth (the GUI is
a same-machine loopback client); CORS restricted to localhost origins.
Production hardening (TLS, pairing token, unix socket) is TODO(§9).
"""
from __future__ import annotations

from .service import PrismService
from .server import serve, RPCRequestHandler

__all__ = ["PrismService", "serve", "RPCRequestHandler"]
