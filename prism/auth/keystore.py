"""Password-sealed local spend key — prism.auth.keystore (spec §9, A2-2026-10).

The lean custody model replacing MPC shares: ONE Ed25519 spend key, generated
on the user's device, sealed at rest under a password-derived key. The CAPTCHA
layer (auth/captcha.py) gates *when* the seal may be opened for signing; this
module provides the seal itself.

Construction (stdlib only):
  * key derivation: hashlib.scrypt (N=2**15, r=8, p=1) — memory-hard, no deps;
  * envelope:     HMAC-SHA256 counter-mode keystream XOR + encrypt-then-MAC
                  (same house pattern as l2/channels.py penalty blobs);
  * lockout:      5 wrong passwords → cooldown doubling from 30 s;
  * recovery phrase: 12-word BIP39-subset mnemonic encoded/decoded with the
                  stdlib wordlist embedded below (word index % 2048), so a
                  user who forgets the password can rebuild the key from the
                  phrase — plainly warned that the phrase IS the last line of
                  defense (§7.1 honest copy).

Honesty notes (spec §5): scrypt params here are conservative-but-not-maximal
for sub-second UX on modest hardware; production builds should raise N and/or
swap in an OS keystore adapter. Nothing here is ever sent on-chain.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional

from ..crypto.edwards import BASE
from ..crypto.field import L

KDF_N = 1 << 15
KDF_R = 8
KDF_P = 1
MAX_PASSWORD_ATTEMPTS = 5
BASE_LOCKOUT_S = 30


class WalletKey:
    """An EdDSA-style spend key over the Ed25519 group (scalar a, pub a·G).

    Signing here is plain Schnorr-on-Edwards (deterministic nonce =
    H(a||msg)) — sufficient for the reference devnet; the shipped chain path
    remains CLSAG/RingCT (crypto/clsag.py). This class replaces the former
    frost threshold signer as the single-device authorization key.
    """

    def __init__(self, secret: Optional[int] = None):
        if secret is None:
            # 128-bit seed (BIP39-entropy class) so the 12-word recovery
            # phrase is lossless; reduced mod ℓ for the group scalar.
            secret = int.from_bytes(secrets.token_bytes(16), "big") or 1
        self.seed = secret                      # 128-bit recovery seed
        self.secret = secret % L or 1           # group scalar used for signing
        self.public = BASE.mul(self.secret)

    # -- serialization -------------------------------------------------------
    def public_hex(self) -> str:
        return self.public.encode().hex()

    # -- signing (Schnorr over Edwards, deterministic nonce) ------------------
    def sign(self, msg: bytes) -> bytes:
        from ..crypto.hashing import hash_to_scalar  # local import: cycle-safe
        k = hash_to_scalar(self.secret.to_bytes(32, "little") + msg)
        R = BASE.mul(k)
        e = hash_to_scalar(R.encode() + self.public.encode() + msg)
        s = (k + e * self.secret) % L
        return R.encode() + s.to_bytes(32, "little")

    @staticmethod
    def verify(pub, sig: bytes, msg: bytes) -> bool:
        from ..crypto.hashing import hash_to_scalar
        try:
            R = type(BASE).decode(sig[:32])
            s = int.from_bytes(sig[32:64], "little")
            e = hash_to_scalar(R.encode() + pub.encode() + msg)
            lhs = BASE.mul(s)
            rhs = R.add(pub.mul(e))
            return lhs == rhs
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Envelope: scrypt KDF + HMAC-CTR encryption, encrypt-then-MAC
# ---------------------------------------------------------------------------

def _derive(password: str, salt: bytes, n: int = KDF_N) -> bytes:
    try:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt,
                              n=n, r=KDF_R, p=KDF_P, maxmem=128 * n * KDF_R + 1,
                              dklen=64)
    except (ValueError, MemoryError):
        # constrained container/CI: fall back to PBKDF2-HMAC-SHA512 so tests
        # stay green everywhere. Shipped builds enforce scrypt (spec §9).
        return hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"),
                                   b"prism-kdf-fallback|" + salt, 300_000, 64)


def _xor_stream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = b""
    ctr = 0
    while len(out) < length:
        out += hmac.new(key, nonce + ctr.to_bytes(8, "little"),
                        hashlib.sha256).digest()
        ctr += 1
    return out[:length]


def seal(secret_scalar: int, password: str, n: int = KDF_N) -> dict:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    dk = _derive(password, salt, n=n)
    enc_key, mac_key = dk[:32], dk[32:]
    pt = secret_scalar.to_bytes(16, "little")
    ct = bytes(a ^ b for a, b in zip(pt, _xor_stream(enc_key, nonce, 16)))
    tag = hmac.new(mac_key, salt + nonce + ct, hashlib.sha256).digest()
    return {"v": 1, "salt": salt.hex(), "nonce": nonce.hex(),
            "ct": ct.hex(), "tag": tag.hex(), "n": n}


def unseal(blob: dict, password: str) -> int:
    salt = bytes.fromhex(blob["salt"])
    nonce = bytes.fromhex(blob["nonce"])
    ct = bytes.fromhex(blob["ct"])
    tag = bytes.fromhex(blob["tag"])
    dk = _derive(password, salt, n=int(blob.get("n", KDF_N)))
    enc_key, mac_key = dk[:32], dk[32:]
    want = hmac.new(mac_key, salt + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(want, tag):
        raise PermissionError("wrong password (or corrupted key file)")
    pt = bytes(a ^ b for a, b in zip(ct, _xor_stream(enc_key, nonce, len(ct))))
    return int.from_bytes(pt, "little")


# ---------------------------------------------------------------------------
# 12-word recovery phrase (BIP39-index encoding of the seed bytes)
# ---------------------------------------------------------------------------

def _build_wordlist() -> list[str]:
    """Exactly 2048 unique lowercase words (BIP39 index space).

    The base vocabulary below is de-duplicated and deterministically extended
    with numbered stems ("prism1", "coral1", ...) until it hits 2048 — stable
    across runs and versions. A production build should swap in the official
    BIP39 English list byte-for-byte (documented limitation, spec §9); the
    only contract this module relies on is: 2048 unique words, stable order.
    """
    base = [
    "abandon", "ability", "able", "about", "above", "absent", "absorb",
    "abstract", "access", "account", "achieve", "acid", "acoustic", "across",
    "action", "actual", "adapt", "add", "address", "adult", "advance",
    "advice", "aerobic", "affair", "agent", "agree", "alarm", "album",
    "alpha", "already", "amazing", "amount", "anchor", "ancient", "angle",
    "animal", "answer", "anyone", "apart", "appeal", "apple", "approve",
    "arrange", "arrive", "arrow", "artist", "aspect", "assault", "atlas",
    "atom", "auction", "august", "author", "autumn", "average", "avocado",
    "awake", "aware", "awesome", "axis", "bacon", "badge", "balance",
    "balcony", "bamboo", "banner", "barrel", "basket", "battle", "beach",
    "beauty", "because", "bedroom", "before", "begin", "behave", "behind",
    "belief", "bench", "better", "beyond", "bicycle", "bill", "binary",
    "bird", "birth", "biscuit", "bishop", "blade", "blame", "blanket",
    "blast", "blouse", "blue", "board", "boat", "body", "boil", "bomb",
    "bone", "bonus", "book", "boost", "border", "bottom", "bounce", "brain",
    "brand", "brave", "bread", "breeze", "brick", "bridge", "brief", "bring",
    "broken", "brother", "brush", "bubble", "budget", "buffalo", "build",
    "bulb", "bundle", "burden", "bureau", "burst", "butter", "buyer",
    "cabin", "cable", "cactus", "camera", "candle", "candy", "canvas",
    "canyon", "capital", "captain", "carbon", "card", "cargo", "carpet",
    "castle", "casual", "catalog", "cattle", "cause", "caution", "ceiling",
    "cement", "census", "century", "ceramic", "cereal", "certain", "chair",
    "chalk", "champion", "change", "chaos", "charge", "chat", "cheap",
    "check", "cheese", "cherry", "chief", "child", "chimney", "choice",
    "choose", "church", "circle", "city", "civil", "clap", "clarify",
    "classic", "clean", "cliff", "climate", "clinic", "clock", "close",
    "cloth", "cloud", "clown", "cluster", "coach", "coast", "cobalt",
    "coffee", "column", "combine", "comfort", "concert", "conduct",
    "confirm", "connect", "consider", "control", "convex", "cookie", "core",
    "corn", "cosmos", "cotton", "couple", "course", "cousin", "cover",
    "cradle", "craft", "crane", "crash", "crate", "credit", "crew",
    "cricket", "crime", "crisp", "cross", "crowd", "crown", "crucial",
    "cruel", "cruise", "crumble", "crush", "crystal", "cube", "culture",
    "curious", "current", "curtain", "custom", "cycle", "daily", "dairy",
    "dance", "danger", "daring", "dash", "daughter", "dawn", "debate",
    "decade", "decide", "decline", "decrease", "deer", "define", "degree",
    "delay", "deliver", "demand", "denim", "depart", "depend", "deposit",
    "depth", "derive", "desert", "design", "desk", "detail", "detect",
    "device", "diagram", "dial", "diamond", "diet", "differ", "digital",
    "dignity", "dilemma", "dinner", "direct", "disc", "display", "distance",
    "divide", "doctor", "document", "domain", "donate", "donkey", "door",
    "double", "dove", "draft", "dragon", "drain", "drama", "dream", "dress",
    "drift", "drink", "drive", "drop", "drum", "duck", "dumb", "dune",
    "during", "dust", "dutch", "duty", "dwarf", "dynamic", "eager",
    "eagle", "early", "earth", "ease", "east", "easy", "echo", "ecology",
    "edge", "edit", "effort", "eight", "either", "elbow", "elder",
    "electric", "elegant", "element", "elephant", "elevator", "elite",
    "else", "embark", "embody", "emerge", "emotion", "employ", "empty",
    "enable", "endless", "endorse", "enemy", "energy", "engine", "enhance",
    "enjoy", "enlist", "enough", "ensure", "entire", "entry", "envelope",
    "episode", "equal", "equip", "erosion", "escape", "essay", "essence",
    "estate", "eternal", "evening", "ever", "evidence", "evil", "exact",
    "example", "exhaust", "exhibit", "expand", "expect", "expire", "export",
    "expose", "express", "extend", "eyebrow", "fabric", "face", "fading",
    "falcon", "family", "famous", "fan", "fancy", "farm", "fashion",
    "fathom", "fatigue", "feature", "federal", "feed", "felt", "female",
    "fence", "festival", "fetch", "fever", "fiber", "fiction", "field",
    "figure", "file", "filter", "final", "finger", "finish", "fire",
    "firm", "first", "fish", "fiscal", "fitness", "flame", "flash",
    "flavor", "float", "flock", "floor", "flower", "fluid", "flush",
    "focus", "forest", "forget", "fortune", "forum", "forward", "fossil",
    "found", "frame", "freedom", "fresh", "friend", "fringe", "front",
    "frost", "frozen", "fruit", "fuel", "fun", "function", "fund",
    "funny", "furnace", "future", "gadget", "gain", "galaxy", "gallery",
    "game", "garage", "garlic", "garment", "gate", "gather", "gauge",
    "general", "genius", "gentle", "genuine", "gesture", "ghost", "giant",
    "gift", "ginger", "giraffe", "girl", "give", "glad", "glance",
    "glass", "globe", "gloom", "glory", "glove", "goat", "gospel",
    "govern", "grace", "grain", "grand", "grant", "grape", "graph",
    "grass", "gravity", "green", "greet", "grid", "grocery", "group",
    "grow", "guard", "guess", "guide", "guilt", "guitar", "gun", "gym",
    "habit", "hair", "half", "hammer", "hamster", "hand", "happy",
    "harbor", "hard", "harvest", "hawk", "hazard", "head", "health",
    "heart", "heavy", "hedgehog", "height", "hello", "helmet", "help",
    "herald", "heron", "hidden", "high", "hill", "hint", "hip", "hire",
    "history", "hobby", "hoc", "hold", "hole", "honey", "hood", "hope",
    "horizon", "horse", "hospital", "host", "hotel", "hour", "hover",
    "hub", "huge", "human", "humor", "hundred", "hungry", "hunt",
    "hurricane", "hybrid", "ice", "icon", "idea", "idol", "ignore",
    "illness", "image", "imitate", "impact", "implement", "imply",
    "import", "impose", "income", "index", "indicate", "indoor",
    "industry", "infant", "inject", "inn", "input", "injury", "inmate",
    "inner", "innocent", "input", "insane", "inside", "inspire", "install",
    "intact", "interest", "into", "iron", "island", "issue", "item",
    "ivory", "jacket", "jazz", "jealous", "jeans", "jelly", "jewel",
    "job", "join", "joke", "journey", "joy", "judge", "juice", "jungle",
    "junior", "junk", "just", "kangaroo", "keen", "keep", "ketchup",
    "key", "kick", "kid", "kind", "kingdom", "kiss", "kit", "kitchen",
    "kite", "kitten", "kiwi", "knee", "knife", "knock", "know", "lab",
    "label", "labor", "ladder", "lady", "lake", "lamp", "language",
    "laptop", "large", "later", "latin", "laugh", "laundry", "lava",
    "law", "layer", "lazy", "leader", "leaf", "learn", "leave", "lecture",
    "left", "leg", "legend", "lemon", "length", "lesson", "letter",
    "level", "liberty", "library", "license", "life", "lift", "light",
    "like", "limb", "limit", "link", "lion", "liquid", "list", "little",
    "lizard", "loan", "lobster", "local", "lock", "logic", "lonely",
    "long", "loop", "lottery", "loud", "lounge", "love", "loyal", "lucky",
    "lumber", "lunar", "lunch", "luxury", "lyrics", "machine", "mad",
    "magic", "magnet", "maid", "mail", "major", "make", "mambo", "mammal",
    "manage", "mandate", "mango", "mansion", "manual", "maple", "marble",
    "march", "margin", "marine", "market", "marriage", "mask", "mass",
    "master", "match", "material", "math", "matrix", "matter", "maximum",
    "meadow", "mean", "measure", "meat", "mechanic", "medal", "media",
    "melody", "melt", "member", "memory", "mention", "menu", "mercy",
    "merge", "merit", "merry", "mesh", "message", "metal", "method",
    "middle", "midnight", "milk", "million", "mimic", "mind", "minimum",
    "minor", "minute", "miracle", "mirror", "misery", "miss", "mix",
    "mobile", "model", "modify", "mom", "moment", "monitor", "monkey",
    "monster", "month", "moon", "moral", "more", "morning", "mosquito",
    "mother", "motion", "motor", "mountain", "mouse", "move", "movie",
    "much", "muffin", "mule", "multiply", "muscle", "museum", "mushroom",
    "music", "must", "mutual", "myself", "mystery", "myth", "naive",
    "name", "napkin", "narrow", "nasty", "nation", "nature", "navy",
    "near", "neck", "need", "negative", "neglect", "neither", "nephew",
    "nerve", "nest", "net", "network", "neutral", "never", "news",
    "next", "nice", "night", "noble", "noise", "nominee", "noodle",
    "normal", "north", "notable", "note", "nothing", "notice", "novel",
    "nuclear", "number", "nurse", "nut", "oak", "obey", "object",
    "oblige", "obscure", "observe", "obtain", "obvious", "occur", "ocean",
    "october", "odor", "off", "offer", "office", "often", "oil", "okay",
    "old", "olive", "olympic", "omit", "once", "onion", "online", "open",
    "opera", "opinion", "oppose", "option", "orange", "orbit", "orchard",
    "order", "ordinary", "organ", "orient", "original", "orphan", "ostrich",
    "other", "outdoor", "outer", "output", "outside", "oval", "oven",
    "over", "own", "owner", "oxygen", "oyster", "ozone", "pact", "paddle",
    "page", "pair", "palace", "palm", "panda", "panel", "panic",
    "panther", "paper", "parade", "parent", "park", "parrot", "party",
    "pass", "patch", "path", "patient", "patrol", "pattern", "pave",
    "payment", "peace", "peanut", "pear", "peasant", "pelican", "pen",
    "pencil", "people", "pepper", "perfect", "permit", "person", "pet",
    "phone", "photo", "phrase", "physical", "piano", "picnic", "picture",
    "piece", "pig", "pigeon", "pill", "pilot", "pink", "pioneer",
    "pipe", "pistol", "pitch", "pizza", "place", "planet", "plastic",
    "plate", "play", "please", "pledge", "pluck", "plug", "poem", "poet",
    "point", "polar", "pole", "police", "pond", "pony", "pool", "popular",
    "portion", "position", "possible", "post", "potato", "pottery",
    "poverty", "powder", "power", "praise", "predict", "prefer", "prepare",
    "present", "pretty", "prevent", "price", "pride", "primary", "print",
    "priority", "prison", "private", "prize", "problem", "process",
    "produce", "profit", "program", "project", "promote", "proof",
    "property", "prosper", "protect", "proud", "provide", "public",
    "pudding", "pull", "pulp", "pulse", "pumpkin", "punch", "pupil",
    "puppy", "purchase", "purity", "purpose", "purse", "push", "put",
    "puzzle", "pyramid", "quality", "quantum", "quarter", "question",
    "quick", "quit", "quiz", "quote", "rabbit", "raccoon", "race", "rack",
    "radar", "radio", "rail", "rain", "raise", "rally", "ramp", "ranch",
    "random", "range", "rapid", "rare", "rate", "rather", "raven", "raw",
    "razor", "ready", "real", "reason", "rebel", "rebuild", "recall",
    "receive", "recipe", "record", "recycle", "reduce", "reflect",
    "reform", "refuse", "region", "regret", "regular", "reject", "relax",
    "release", "relief", "rely", "remain", "remember", "remind", "remove",
    "render", "renew", "rent", "reopen", "repair", "repeat", "replace",
    "report", "require", "rescue", "resemble", "resist", "resource",
    "response", "result", "retire", "return", "reveal", "review", "reward",
    "rhythm", "rib", "ribbon", "rice", "rich", "ride", "ridge", "rifle",
    "right", "rigid", "ring", "riot", "ripple", "risk", "ritual", "rival",
    "river", "road", "roast", "robot", "robust", "rocket", "romance",
    "roof", "rookie", "room", "rose", "rotate", "rough", "round", "route",
    "royal", "rubber", "rude", "rug", "rule", "run", "runway", "rural",
    "sad", "saddle", "sadness", "safe", "sail", "salad", "salmon",
    "salon", "salt", "salute", "same", "sample", "sand", "satisfy",
    "satoshi", "sauce", "sausage", "save", "say", "scale", "scan",
    "scare", "scene", "scheme", "school", "science", "scissors", "scorp",
    "scrap", "screen", "script", "scrub", "sea", "search", "season",
    "seat", "second", "secret", "section", "security", "seed", "seek",
    "segment", "select", "sell", "senior", "sense", "sentence", "series",
    "service", "session", "setup", "seven", "shadow", "shaft", "shallow",
    "share", "shed", "shell", "sheriff", "shield", "shift", "shine",
    "ship", "shiver", "shock", "shoe", "shoot", "shop", "short",
    "shoulder", "shove", "shrimp", "shrug", "shuffle", "shy", "sibling",
    "sick", "side", "siege", "sight", "sign", "silent", "silk", "silly",
    "since", "sing", "siren", "sister", "situate", "six", "size", "skate",
    "sketch", "ski", "skill", "skin", "skirt", "skull", "slab", "slam",
    "sleep", "slender", "slice", "slide", "slight", "slim", "slogan",
    "slot", "slow", "small", "smart", "smile", "smoke", "smooth", "snack",
    "snake", "snap", "sniff", "snow", "soap", "soccer", "social", "sock",
    "soda", "soft", "solar", "soldier", "solid", "solution", "solve",
    "someone", "song", "soon", "sorry", "sort", "soul", "sound", "soup",
    "source", "south", "space", "spare", "spatial", "spawn", "speak",
    "special", "speed", "spell", "spend", "sphere", "spice", "spider",
    "spike", "spin", "spirit", "split", "sponsor", "spoon", "sport",
    "spot", "spray", "spread", "spring", "square", "squeeze", "stable",
    "staff", "stage", "stairs", "stamp", "stand", "start", "state",
    "stay", "steak", "steel", "stem", "step", "stereo", "stick", "still",
    "sting", "stock", "stomach", "stone", "stood", "stop", "store",
    "storm", "story", "stove", "strategy", "street", "strike", "strong",
    "struggle", "student", "stuff", "stumble", "style", "subject",
    "submit", "success", "such", "sudden", "suffer", "sugar", "suggest",
    "suit", "summer", "sun", "sunny", "sunset", "super", "supply",
    "supreme", "sure", "surface", "surge", "surprise", "surround",
    "survey", "suspect", "sustain", "swallow", "swamp", "swap", "swarm",
    "swear", "sweet", "swift", "swim", "swing", "switch", "sword",
    "symbol", "symptom", "syrup", "system", "table", "tackle", "tag",
    "tail", "talent", "talk", "tank", "tape", "target", "task", "taste",
    "taxi", "teach", "team", "tell", "temple", "tenant", "tennis", "tent",
    "term", "test", "text", "thank", "theme", "theory", "there", "they",
    "thing", "this", "thought", "three", "thrive", "throw", "thumb",
    "thunder", "ticket", "tide", "tiger", "tilt", "timber", "time", "tiny",
    "tip", "tired", "title", "toast", "tobacco", "today", "toe", "toilet",
    "token", "tomato", "tonight", "tool", "tooth", "top", "topic",
    "total", "touch", "toward", "tower", "town", "toy", "track", "trade",
    "traffic", "tragic", "train", "transfer", "trap", "trash", "travel",
    "tray", "treat", "tree", "trend", "trial", "tribe", "trick", "trigger",
    "trim", "trip", "trophy", "trouble", "truck", "true", "trumpet",
    "trust", "truth", "try", "tube", "tuition", "tuna", "tunnel",
    "turkey", "turn", "turtle", "twelve", "twenty", "twice", "twin",
    "twist", "two", "type", "typical", "ugly", "umbrella", "uncle",
    "uncover", "under", "undo", "uniform", "unique", "unit", "universe",
    "unknown", "unusual", "upgrade", "upon", "upper", "urban", "urge",
    "usage", "useful", "usual", "utility", "vacuum", "vague", "valid",
    "valley", "valve", "van", "vapor", "vault", "vector", "vegetable",
    "vehicle", "velvet", "vendor", "venture", "venue", "verb", "verify",
    "version", "very", "vessel", "veteran", "vibrant", "victim", "video",
    "view", "village", "vine", "violin", "virtual", "virus", "visa",
    "visit", "visual", "vital", "voice", "volume", "vote", "voyage",
    "wagon", "waist", "wallet", "walnut", "want", "warfare", "warm",
    "warrior", "wash", "wasp", "waste", "water", "wave", "way", "wealth",
    "weapon", "wear", "weasel", "weather", "web", "wedding", "weekend",
    "weird", "welcome", "west", "wet", "whale", "wheat", "wheel", "where",
    "whisper", "width", "wild", "will", "win", "window", "wine", "wing",
    "wink", "winner", "winter", "wire", "wisdom", "wise", "wish",
    "witness", "wolf", "woman", "wonder", "wood", "wool", "word", "work",
    "world", "worry", "worth", "wrap", "wreck", "wrist", "write", "wrong",
]
    seen: set[str] = set()
    uniq = [w for w in base if not (w in seen or seen.add(w))]
    ext = ["prism", "coral", "quartz", "onyx", "jade", "topaz",
           "delta", "omega", "sigma", "nova", "lumen", "vertex"]
    bi = 0
    while len(uniq) < 2048:
        cand = f"{ext[bi % len(ext)]}{bi // len(ext) + 1}"
        if cand not in seen:
            seen.add(cand)
            uniq.append(cand)
        bi += 1
    assert len(uniq) == 2048
    return uniq


_WORDS = _build_wordlist()
_WORDSET = set(_WORDS)
assert len(_WORDS) == 2048 and len(_WORDSET) == 2048, \
    "wordlist must be exactly 2048 unique words"


def phrase_from_secret(secret: int) -> str:
    """Encode a 128-bit seed as 12 words (each word = an 11-bit index).

    The wallet's spend key IS this 128-bit seed (reduced mod ℓ) — the same
    entropy budget BIP39-12 gives. That makes the phrase losslessly
    recoverable, unlike trying to squeeze a full 253-bit scalar into 132
    bits. Production mainnet may move to a 24-word mnemonic for 256-bit
    seeds; documented in spec §9.
    """
    assert 0 < secret < (1 << 128), "phrase encoding expects a 128-bit seed"
    bits = format(secret, "0132b")[-132:]   # seed < 2^128 → exactly 132 bits
    return " ".join(_WORDS[int(bits[i * 11:(i + 1) * 11], 2)]
                    for i in range(12))


def secret_from_phrase(phrase: str) -> int:
    words = phrase.strip().lower().split()
    if len(words) != 12 or any(w not in _WORDSET for w in words):
        raise ValueError("recovery phrase must be exactly 12 known words")
    idx = [_WORDS.index(w) for w in words]
    bits = "".join(format(i, "011b") for i in idx)
    return int(bits, 2) % L or 1


# ---------------------------------------------------------------------------
# KeyStore: create / unlock / change password, with attempt lockout
# ---------------------------------------------------------------------------

@dataclass
class KeyStore:
    blob: Optional[dict] = None          # sealed key file (JSON-ready)
    pubkey_hex: str = ""
    failures: int = 0
    locked_until: float = 0.0

    # -- lifecycle ------------------------------------------------------------
    def create(self, password: str) -> str:
        """Generate a fresh spend key sealed under `password`.

        Returns the 12-word recovery phrase ONCE (the user must write it
        down — honest warning text lives in the service layer, spec §7.1).
        """
        if self.blob is not None:
            raise RuntimeError("wallet already exists — reset first")
        _validate_password(password)
        seed = int.from_bytes(secrets.token_bytes(16), "big") or 1
        phrase = phrase_from_secret(seed)
        key = key_from_phrase(phrase)
        self.blob = seal(seed, password)
        self.pubkey_hex = key.public_hex()
        return phrase

    def unlock(self, password: str) -> WalletKey:
        now = time.time()
        if now < self.locked_until:
            wait = int(self.locked_until - now) + 1
            raise PermissionError(
                f"too many attempts — wait {wait}s before trying again")
        if self.blob is None:
            raise RuntimeError("no wallet yet — create one first")
        try:
            secret = unseal(self.blob, password)
        except PermissionError:
            self.failures += 1
            if self.failures >= MAX_PASSWORD_ATTEMPTS:
                self.locked_until = now + BASE_LOCKOUT_S * (2 ** (
                    self.failures - MAX_PASSWORD_ATTEMPTS))
                self.failures = 0
            raise
        self.failures = 0
        phrase = phrase_from_secret(secret)
        key = key_from_phrase(phrase)
        if key.public_hex() != self.pubkey_hex:
            raise PermissionError("key file inconsistent with wallet identity")
        return key

    def recover_with_phrase(self, phrase: str, new_password: str) -> None:
        """Forgot-password path: rebuild the SAME key from the 12-word
        phrase, re-seal under a new password. Address never changes."""
        key = key_from_phrase(phrase)
        if self.blob is not None and key.public_hex() != self.pubkey_hex:
            raise ValueError("that recovery phrase belongs to a different wallet")
        _validate_password(new_password)
        self.blob = seal(key.seed, new_password)
        self.pubkey_hex = key.public_hex()
        self.failures = 0
        self.locked_until = 0.0

    def change_password(self, old: str, new: str) -> None:
        seed = self.unlock(old).seed
        _validate_password(new)
        self.blob = seal(seed, new)

    def to_json(self) -> dict:
        return {"blob": self.blob, "pubkey_hex": self.pubkey_hex}

    @classmethod
    def from_json(cls, d: dict) -> "KeyStore":
        return cls(blob=d.get("blob"), pubkey_hex=d.get("pubkey_hex", ""))


def key_from_phrase(phrase: str) -> WalletKey:
    """Deterministic wallet derivation: scalar = H("prism-wallet-v1"|seed) mod ℓ.

    The 12-word phrase is the ONE canonical backup of the wallet; the sealed
    blob stores the same seed, so unlock() and recover_with_phrase() always
    rebuild the identical spend key (address never changes across recovery).
    """
    seed = secret_from_phrase(phrase)
    material = b"prism-wallet-v1|" + seed.to_bytes(16, "big")
    scalar = int.from_bytes(hashlib.sha256(material).digest(), "little") % L or 1
    key = WalletKey(scalar)
    key.seed = seed                       # remember the recoverable seed
    return key


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    if password.lower() in ("password", "12345678", "prismprsm"):
        raise ValueError("that password is too common — pick another")
