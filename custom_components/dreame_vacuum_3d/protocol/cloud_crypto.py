"""Xiaomi cloud request signing and the RC4 encryption of request fields and response bodies.

Portions derived from Tasshack/dreame-vacuum (MIT), see NOTICE.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlsplit

RC4_DROP = 1024


def rc4(key: bytes, data: bytes, *, drop: int = 0) -> bytes:
    """Encrypt or decrypt with RC4, discarding the first drop keystream bytes."""
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray(data)
    i = j = 0
    for n in range(-drop, len(out)):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        if n >= 0:
            out[n] ^= s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def _rc4_b64(signed_nonce: str, data: bytes) -> bytes:
    return rc4(base64.b64decode(signed_nonce), data, drop=RC4_DROP)


def generate_nonce(now_ms: int | None = None, rand: bytes | None = None) -> str:
    """Return 8 random bytes followed by the minutes since the epoch, base64 encoded."""
    if now_ms is None:
        now_ms = round(time.time() * 1000)
    if rand is None:
        rand = secrets.token_bytes(8)
    minutes = now_ms // 60000
    return base64.b64encode(rand + minutes.to_bytes((minutes.bit_length() + 7) // 8, "big")).decode()


def signed_nonce(ssecurity: str, nonce: str) -> str:
    """Return the per-request RC4 key: SHA-256 of the session security key and the nonce."""
    return base64.b64encode(hashlib.sha256(base64.b64decode(ssecurity) + base64.b64decode(nonce)).digest()).decode()


def enc_signature(url: str, method: str, signed_nonce: str, params: dict[str, str]) -> str:
    """Return the SHA-1 signature of method, API path and params in their insertion order."""
    path = urlsplit(url).path
    if path.startswith("/app/"):
        path = path[4:]
    parts = [method.upper(), path, *(f"{k}={v}" for k, v in params.items()), signed_nonce]
    return base64.b64encode(hashlib.sha1("&".join(parts).encode(), usedforsecurity=False).digest()).decode()


def encrypt_params(
    url: str, method: str, signed_nonce: str, nonce: str, params: dict[str, str], ssecurity: str
) -> dict[str, str]:
    """Return the form fields of an encrypted request; params itself is left unchanged."""
    fields = {**params, "rc4_hash__": enc_signature(url, method, signed_nonce, params)}
    fields = {k: base64.b64encode(_rc4_b64(signed_nonce, v.encode())).decode() for k, v in fields.items()}
    fields["signature"] = enc_signature(url, method, signed_nonce, fields)
    fields["ssecurity"] = ssecurity
    fields["_nonce"] = nonce
    return fields


def decrypt_response(signed_nonce: str, body: str) -> bytes:
    """Decrypt a base64 response body with the signed nonce of its request."""
    return _rc4_b64(signed_nonce, base64.b64decode(body))
