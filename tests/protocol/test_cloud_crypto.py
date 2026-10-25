"""Tests of Xiaomi cloud request signing and RC4, checked against Tasshack's implementation."""

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

from hypothesis import given, strategies as st
import pytest

from custom_components.dreame_vacuum_3d.protocol import cloud_crypto as cc

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "cloud_vectors.json").read_text(encoding="utf-8"))
REQUESTS = [pytest.param(v, id=v["url"].split("/app/")[1]) for v in _FIXTURE["requests"]]


@pytest.mark.parametrize(
    ("key", "plaintext", "expected"),
    [
        (b"Key", b"Plaintext", "bbf316e8d940af0ad3"),
        (b"Wiki", b"pedia", "1021bf0420"),
        (b"Secret", b"Attack at dawn", "45a01f645fc35b383552544b9bf5"),
    ],
)
def test_rc4_known_vectors(key, plaintext, expected):
    assert cc.rc4(key, plaintext).hex() == expected


@given(st.binary(min_size=1, max_size=256), st.binary(max_size=512))
def test_rc4_drop_skips_keystream(key, plaintext):
    assert cc.rc4(key, plaintext, drop=1024) == cc.rc4(key, bytes(1024) + plaintext)[1024:]


@given(st.binary(min_size=1, max_size=256), st.binary(max_size=512))
def test_rc4_is_an_involution(key, plaintext):
    assert cc.rc4(key, cc.rc4(key, plaintext)) == plaintext


@pytest.mark.parametrize("vector", _FIXTURE["nonces"], ids=lambda v: str(v["now_ms"]))
def test_generate_nonce_matches_reference(vector):
    assert cc.generate_nonce(vector["now_ms"], bytes.fromhex(vector["rand_hex"])) == vector["nonce"]


def test_generate_nonce_defaults_to_random_bytes_and_current_minute(monkeypatch):
    monkeypatch.setattr(cc.time, "time", lambda: 1761300000.123)
    raw = base64.b64decode(cc.generate_nonce())
    assert len(raw) == 12
    assert int.from_bytes(raw[8:], "big") == 1761300000123 // 60000
    assert cc.generate_nonce() != cc.generate_nonce()


def test_signed_nonce_matches_reference():
    assert cc.signed_nonce(_FIXTURE["ssecurity"], _FIXTURE["nonce"]) == _FIXTURE["signed_nonce"]


@pytest.mark.parametrize("vector", REQUESTS)
def test_enc_signature_matches_reference(vector):
    signature = cc.enc_signature(vector["url"], vector["method"], _FIXTURE["signed_nonce"], vector["params"])
    assert signature == vector["enc_signature"]


def test_enc_signature_uppercases_the_method():
    vector = _FIXTURE["requests"][0]
    signature = cc.enc_signature(vector["url"], "post", _FIXTURE["signed_nonce"], vector["params"])
    assert signature == vector["enc_signature"]


def test_enc_signature_signs_the_path_without_the_app_prefix():
    vector = _FIXTURE["requests"][0]
    url = vector["url"].replace("/app/", "/")
    assert cc.enc_signature(url, "POST", _FIXTURE["signed_nonce"], vector["params"]) == vector["enc_signature"]


@pytest.mark.parametrize("vector", REQUESTS)
def test_encrypt_params_matches_reference(vector):
    fields = cc.encrypt_params(
        vector["url"],
        vector["method"],
        _FIXTURE["signed_nonce"],
        _FIXTURE["nonce"],
        vector["params"],
        _FIXTURE["ssecurity"],
    )
    assert fields == vector["enc_params"]
    assert list(fields) == list(vector["enc_params"])


def test_encrypt_params_leaves_its_input_alone():
    vector = _FIXTURE["requests"][2]
    params = copy.deepcopy(vector["params"])
    cc.encrypt_params(vector["url"], "POST", _FIXTURE["signed_nonce"], _FIXTURE["nonce"], params, _FIXTURE["ssecurity"])
    assert params == vector["params"]


def test_decrypt_response_matches_reference():
    response = _FIXTURE["response"]
    assert cc.decrypt_response(_FIXTURE["signed_nonce"], response["body"]) == response["plaintext"].encode()


def test_encrypted_fields_decrypt_to_the_original_params():
    vector = _FIXTURE["requests"][0]
    fields = cc.encrypt_params(
        vector["url"], "POST", _FIXTURE["signed_nonce"], _FIXTURE["nonce"], vector["params"], _FIXTURE["ssecurity"]
    )
    assert cc.decrypt_response(_FIXTURE["signed_nonce"], fields["data"]).decode() == vector["params"]["data"]


@given(st.binary(min_size=16, max_size=16), st.binary(min_size=8, max_size=8), st.binary(max_size=512))
def test_decrypt_response_round_trip(ssecurity, rand, plaintext):
    sn = cc.signed_nonce(base64.b64encode(ssecurity).decode(), cc.generate_nonce(1761300000123, rand))
    body = base64.b64encode(cc.rc4(base64.b64decode(sn), plaintext, drop=cc.RC4_DROP)).decode()
    assert cc.decrypt_response(sn, body) == plaintext
