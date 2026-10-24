"""miIO packet format: a 32-byte header and an AES-CBC encrypted JSON body keyed by the device token."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import struct
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = 0x2131
HEADER_LEN = 32
HELLO = bytes.fromhex("21310020" + "ff" * 28)
_HEAD = struct.Struct(">HHIII")


class MiioError(Exception):
    """A packet that isn't valid miIO."""


class MiioChecksumError(MiioError):
    """The checksum doesn't match: wrong token or a corrupted packet."""


@dataclass(frozen=True)
class Header:
    """The fields of a packet header that matter to the caller."""

    device_id: int
    stamp: int


def _md5(data: bytes) -> bytes:
    return hashlib.md5(data, usedforsecurity=False).digest()


def key_iv(token: bytes) -> tuple[bytes, bytes]:
    """Return the AES key and IV derived from the device token."""
    key = _md5(token)
    return key, _md5(key + token)


def encrypt(token: bytes, plaintext: bytes) -> bytes:
    """Encrypt with AES-128-CBC and PKCS#7 padding."""
    key, iv = key_iv(token)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def decrypt(token: bytes, ciphertext: bytes) -> bytes:
    """Decrypt and unpad; raise MiioError on bad length or padding."""
    key, iv = key_iv(token)
    try:
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        data = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(data) + unpadder.finalize()
    except ValueError as err:
        raise MiioError("can't decrypt the payload") from err


def build(token: bytes, device_id: int, stamp: int, payload: dict[str, Any]) -> bytes:
    """Build a signed, encrypted packet carrying payload as JSON."""
    data = encrypt(token, json.dumps(payload, separators=(",", ":")).encode() + b"\x00")
    head = _HEAD.pack(MAGIC, HEADER_LEN + len(data), 0, device_id, stamp)
    return head + _md5(head + token + data) + data


def parse_header(packet: bytes) -> Header:
    """Check the magic and length and return the header."""
    if len(packet) < HEADER_LEN:
        raise MiioError("packet shorter than the header")
    magic, length, _unknown, device_id, stamp = _HEAD.unpack_from(packet)
    if magic != MAGIC or length != len(packet):
        raise MiioError("not a miIO packet")
    return Header(device_id, stamp)


def parse(token: bytes, packet: bytes) -> tuple[Header, Any]:
    """Verify and decrypt a packet; the payload is None for a header-only reply."""
    header = parse_header(packet)
    data = packet[HEADER_LEN:]
    if not data:
        return header, None
    if not hmac.compare_digest(_md5(packet[:16] + token + data), packet[16:32]):
        raise MiioChecksumError("checksum mismatch")
    text = decrypt(token, data).rstrip(b"\x00 ").decode("utf-8", errors="replace")
    try:
        return header, json.loads(text)
    except ValueError as err:
        raise MiioError("payload isn't JSON") from err
