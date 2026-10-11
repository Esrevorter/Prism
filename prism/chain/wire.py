"""P2P wire framing + block encode/decode — the §4.2 transport layer for
the Refraction networking daemon (spec §13 Phase-1 exit-criteria note:
"pending a wall-clock soak against a real Refraction testnet deployment").

Design (stdlib-only, like the rest of L1):

  * Frame = 4-byte little-endian payload length + JSON payload.
    Strict exact-length framing on read: partial frames buffer until
    complete; oversize frames are refused (DoS posture).
  * Message kinds: "version", "verack", "getblocks", "inv", "block",
    "headers", "ping", "pong". Unknown kinds are ignored (forward compat)
    but never crash the stream.
  * Block payloads serialize through the canonical header path
    (chain.block.serialize/parse_header), so every inbound block goes
    through the same untrusted-decode boundary the fuzz gate pins
    (parse(serialize(h)).hash() == h.hash() fixpoint).

This module contains no socket I/O itself — `encode_frame`/`FrameReader`
are pure functions over bytes so the daemon (chain/p2p.py) and tests can
exercise them without a network stack.
"""
from __future__ import annotations

import json
import struct

from .block import BlockHeader, parse_header
from .node import Block

PROTOCOL_VERSION = 1
MAGIC = b"PRSM"                     # 4-byte prefix inside version handshake
MAX_FRAME_BYTES = 4 * 1024 * 1024   # hard cap: refuse oversized frames

# message kinds on the wire
MSG_VERSION = "version"
MSG_VERACK = "verack"
MSG_GETBLOCKS = "getblocks"
MSG_INV = "inv"
MSG_BLOCK = "block"
MSG_HEADERS = "headers"
MSG_PING = "ping"
MSG_PONG = "pong"

KNOWN_KINDS = {MSG_VERSION, MSG_VERACK, MSG_GETBLOCKS, MSG_INV,
               MSG_BLOCK, MSG_HEADERS, MSG_PING, MSG_PONG}


class WireError(ValueError):
    """Malformed/untrusted wire input. The daemon treats this as a
    protocol violation: drop the peer connection, never apply state."""


# --------------------------------------------------------------------------
# framing
# --------------------------------------------------------------------------

def encode_frame(kind: str, payload: dict | None = None) -> bytes:
    """Serialize one message: <u32 LE len> || JSON{kind, payload}."""
    if not isinstance(kind, str) or not kind:
        raise WireError("message kind must be a non-empty string")
    body = json.dumps({"kind": kind, "payload": payload or {}},
                      separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise WireError(f"frame too large: {len(body)} > {MAX_FRAME_BYTES}")
    return struct.pack("<I", len(body)) + body


class FrameReader:
    """Incremental, strict frame decoder. Feed bytes from the socket;
    yields fully-framed decoded messages. Any malformed frame raises
    WireError (the caller disconnects the peer — fail closed)."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[str, dict]]:
        """Append received bytes; return every complete message available."""
        self._buf.extend(data)
        out: list[tuple[str, dict]] = []
        while True:
            if len(self._buf) < 4:
                return out
            (ln,) = struct.unpack_from("<I", self._buf, 0)
            if ln > MAX_FRAME_BYTES:
                raise WireError(f"declared frame size {ln} exceeds cap")
            if len(self._buf) < 4 + ln:
                return out                    # partial: wait for more bytes
            body = bytes(self._buf[4:4 + ln])
            del self._buf[:4 + ln]
            try:
                msg = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise WireError(f"non-JSON frame body: {e}") from e
            if not isinstance(msg, dict):
                raise WireError("frame body must be a JSON object")
            kind = msg.get("kind")
            if not isinstance(kind, str) or not kind:
                raise WireError("frame missing message kind")
            payload = msg.get("payload")
            if payload is None:
                payload = {}
            if not isinstance(payload, dict):
                raise WireError("frame payload must be an object")
            out.append((kind, payload))


# --------------------------------------------------------------------------
# block encoding (header via canonical path; body as hex txid list)
# --------------------------------------------------------------------------

def encode_block(blk: Block) -> dict:
    """Canonical block payload: header hex (chain.block.serialize) plus
    txid/coinbase-commitment hex list. Hashes travel as lowercase hex."""
    return {
        "header_hex": blk.header.serialize().hex(),
        "txids_hex": [t.hex() for t in blk.txids],
        "coinbase_commitment_hex": blk.coinbase_commitment.hex(),
    }


def decode_block(payload: dict) -> Block:
    """Untrusted decode path: every failure is a WireError, never a
    partially-constructed block."""
    try:
        hdr_blob = bytes.fromhex(str(payload["header_hex"]))
        txids_hex = payload.get("txids_hex", [])
        cb_hex = payload["coinbase_commitment_hex"]
    except (KeyError, TypeError, ValueError) as e:
        raise WireError(f"malformed block payload: {e}") from e
    try:
        header = parse_header(hdr_blob)      # strict exact-length parser
    except ValueError as e:
        raise WireError(f"bad header: {e}") from e
    try:
        txids = [bytes.fromhex(t) for t in txids_hex]
        cb = bytes.fromhex(cb_hex)
    except ValueError as e:
        raise WireError(f"bad hex in block body: {e}") from e
    if any(len(t) != 32 for t in txids) or len(cb) != 32:
        raise WireError("txid/coinbase commitment must be 32 bytes")
    return Block(header=header, txids=txids, coinbase_commitment=cb)


def block_hash_of(payload: dict) -> bytes:
    """Hash of the header inside a block payload WITHOUT full validation —
    used to build inv inventories cheaply. Still goes through the strict
    parser so junk inventories get rejected at decode time."""
    try:
        header = parse_header(bytes.fromhex(str(payload["header_hex"])))
    except (KeyError, TypeError, ValueError) as e:
        raise WireError(f"malformed block payload: {e}") from e
    return header.hash()
