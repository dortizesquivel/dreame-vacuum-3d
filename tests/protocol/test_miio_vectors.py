"""Check the miIO codec against packets built by an independent implementation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from custom_components.dreame_vacuum_3d.protocol import miio_codec as codec

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "miio_vectors.json").read_text(encoding="utf-8"))
TOKEN = bytes.fromhex(_FIXTURE["token"])
VECTORS = [pytest.param(v, id=v["payload"].get("method", "result")) for v in _FIXTURE["vectors"]]


@pytest.mark.parametrize("vector", VECTORS)
def test_parse_decodes_reference_packet(vector):
    packet = bytes.fromhex(vector["packet_hex"])
    assert codec.parse(TOKEN, packet) == (codec.Header(vector["device_id"], vector["stamp"]), vector["payload"])


@pytest.mark.parametrize("vector", VECTORS)
def test_encryption_and_checksum_match_reference_bytes(vector):
    # The reference serializes with json.dumps defaults, build() doesn't, so re-encrypt its plaintext instead.
    packet = bytes.fromhex(vector["packet_hex"])
    data = codec.encrypt(TOKEN, json.dumps(vector["payload"]).encode() + b"\x00")
    assert data == packet[codec.HEADER_LEN :]
    assert hashlib.md5(packet[:16] + TOKEN + data, usedforsecurity=False).digest() == packet[16:32]


@pytest.mark.parametrize("vector", VECTORS)
def test_build_header_matches_reference(vector):
    packet = bytes.fromhex(vector["packet_hex"])
    built = codec.build(TOKEN, vector["device_id"], vector["stamp"], vector["payload"])
    assert built[4:16] == packet[4:16]
