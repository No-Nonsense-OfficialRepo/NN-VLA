"""Ed25519 keys, signing and bundle helpers (Workstream B)."""
from __future__ import annotations
import base64, hashlib, json, time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

_RAW = dict(encoding=serialization.Encoding.Raw)


def generate_keypair() -> tuple[str, str]:
    """Return (private_b64, public_b64), raw 32-byte keys."""
    priv = Ed25519PrivateKey.generate()
    pb = priv.private_bytes(**_RAW, format=serialization.PrivateFormat.Raw,
                            encryption_algorithm=serialization.NoEncryption())
    pub = priv.public_key().public_bytes(**_RAW, format=serialization.PublicFormat.Raw)
    return base64.b64encode(pb).decode(), base64.b64encode(pub).decode()


def sign_bytes(priv_b64: str, data: bytes) -> str:
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(priv_b64))
    return base64.b64encode(key.sign(data)).decode()


def verify_bytes(pub_b64: str, data: bytes, sig_b64: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64)).verify(
            base64.b64decode(sig_b64), data)
        return True
    except (InvalidSignature, ValueError):
        return False


def canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def sign_obj(priv_b64: str, obj: dict) -> dict:
    body = {k: v for k, v in obj.items() if k != "sig"}
    return {**body, "sig": sign_bytes(priv_b64, canonical(body))}


def verify_obj(pub_b64: str, obj: dict) -> bool:
    sig = obj.get("sig")
    if not isinstance(sig, str):
        return False
    return verify_bytes(pub_b64, canonical({k: v for k, v in obj.items() if k != "sig"}), sig)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_bundle(pub_b64: str, planet_bytes: bytes, library_version: str) -> dict:
    return {"host_pub": pub_b64, "planet_sha256": sha256_hex(planet_bytes),
            "library_version": library_version, "created": int(time.time())}


def sign_planet(priv_b64: str, planet_bytes: bytes) -> dict:
    """Detached signature package served to clients on re-serve (F4)."""
    return {"sha256": sha256_hex(planet_bytes), "sig": sign_bytes(priv_b64, planet_bytes)}


def verify_planet(pub_b64: str, planet_bytes: bytes, pkg: dict) -> bool:
    return pkg.get("sha256") == sha256_hex(planet_bytes) and \
        verify_bytes(pub_b64, planet_bytes, pkg.get("sig", ""))
