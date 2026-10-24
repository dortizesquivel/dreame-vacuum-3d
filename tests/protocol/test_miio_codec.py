"""Tests of the miIO packet codec."""

from __future__ import annotations

import contextlib
import hashlib
import json
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from hypothesis import given, strategies as st
import pytest

from custom_components.dreame_vacuum_3d.protocol import miio_codec as codec

TOKEN = bytes.fromhex("00112233445566778899aabbccddeeff")

_json = st.recursive(
    st.none() | st.booleans() | st.integers(-(2**53), 2**53) | st.text(),
    lambda children: st.lists(children, max_size=4) | st.dictionaries(st.text(), children, max_size=4),
    max_leaves=10,
)


def _md5(data: bytes) -> bytes:
    return hashlib.md5(data, usedforsecurity=False).digest()


def test_hello_packet():
    assert bytes.fromhex("21310020" + "ff" * 28) == codec.HELLO


def test_key_and_iv_derive_from_token():
    key = _md5(TOKEN)
    assert codec.key_iv(TOKEN) == (key, _md5(key + TOKEN))


def test_round_trip():
    packet = codec.build(TOKEN, device_id=0x1234, stamp=1000, payload={"id": 1, "method": "miIO.info", "params": []})
    header, payload = codec.parse(TOKEN, packet)
    assert header == codec.Header(device_id=0x1234, stamp=1000)
    assert payload == {"id": 1, "method": "miIO.info", "params": []}
    magic, length = struct.unpack_from(">HH", packet)
    assert (magic, length) == (0x2131, len(packet))


@given(
    payload=st.dictionaries(st.text(), _json, max_size=5),
    device_id=st.integers(0, 2**32 - 1),
    stamp=st.integers(0, 2**32 - 1),
)
def test_round_trip_property(payload, device_id, stamp):
    packet = codec.build(TOKEN, device_id, stamp, payload)
    assert codec.parse(TOKEN, packet) == (codec.Header(device_id, stamp), payload)


def test_bad_checksum_is_rejected():
    packet = bytearray(codec.build(TOKEN, 1, 1, {"id": 1}))
    packet[20] ^= 0xFF
    with pytest.raises(codec.MiioChecksumError):
        codec.parse(TOKEN, bytes(packet))


def test_wrong_token_is_rejected():
    packet = codec.build(TOKEN, 1, 1, {"id": 1})
    with pytest.raises(codec.MiioChecksumError):
        codec.parse(bytes(16), packet)


def test_header_only_reply_has_no_payload():
    reply = struct.pack(">HHIII", 0x2131, 32, 0, 7, 99) + bytes(16)
    assert codec.parse(TOKEN, reply) == (codec.Header(7, 99), None)


def _signed(data: bytes) -> bytes:
    """Wrap data, as is, in a header with a valid length and checksum."""
    head = struct.pack(">HHIII", 0x2131, 32 + len(data), 0, 1, 1)
    return head + _md5(head + TOKEN + data) + data


def test_trailing_nulls_and_spaces_are_tolerated():
    packet = _signed(codec.encrypt(TOKEN, json.dumps({"id": 2, "result": [0]}).encode() + b" \x00\x00"))
    assert codec.parse(TOKEN, packet)[1] == {"id": 2, "result": [0]}


def test_non_json_payload_is_rejected():
    with pytest.raises(codec.MiioError, match="JSON"):
        codec.parse(TOKEN, _signed(codec.encrypt(TOKEN, b"not json")))


@given(st.binary(max_size=64).filter(lambda b: len(b) % 16 != 0))
def test_ciphertext_of_bad_length_is_rejected(data):
    with pytest.raises(codec.MiioError):
        codec.parse(TOKEN, _signed(data))


def test_bad_padding_is_rejected():
    key, iv = codec.key_iv(TOKEN)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    unpadded = encryptor.update(bytes(16)) + encryptor.finalize()
    with pytest.raises(codec.MiioError):
        codec.decrypt(TOKEN, unpadded)


@given(st.binary(max_size=300))
def test_garbage_only_raises_miio_error(data):
    with contextlib.suppress(codec.MiioError):
        codec.parse(TOKEN, data)


@given(st.binary(max_size=300))
def test_garbage_with_valid_header_only_raises_miio_error(data):
    with contextlib.suppress(codec.MiioError):
        codec.parse(TOKEN, _signed(data))
