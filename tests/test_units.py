import struct

import pytest

from app import ai
from app.drivers.rockwell import build_list_identity, parse_list_identity
from app.security import (LoginThrottle, decrypt_json, encrypt_json, hash_password,
                          validate_password, verify_password)
from app.versioning import is_newer, parse_version, same_version


def test_version_comparison():
    assert is_newer("V4.2.1", "4.2")
    assert not is_newer("4.2", "4.2.0")
    assert not is_newer("4.1.9", "4.2")
    assert is_newer("10.0", "9.9")          # numeric, not lexical
    assert not is_newer("abc", "1.0")       # unparseable never counts as newer
    assert same_version("V4.20", "4.20.0")
    assert parse_version("FW 3.5.12-b2") == (3, 5, 12, 2)


def test_password_hashing_and_policy():
    h = hash_password("correct horse battery")
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    assert validate_password("short")
    assert validate_password("x" * 100)
    assert validate_password("long-enough-password") is None


def test_credentials_encrypted_roundtrip():
    token = encrypt_json({"username": "u", "password": "secret-pw"})
    assert "secret-pw" not in token
    assert decrypt_json(token) == {"username": "u", "password": "secret-pw"}
    assert decrypt_json(None) == {}


def test_login_throttle():
    t = LoginThrottle(max_failures=3, window_seconds=60)
    for _ in range(3):
        assert not t.blocked("k")
        t.fail("k")
    assert t.blocked("k")
    t.reset("k")
    assert not t.blocked("k")


def test_heuristic_analysis_flags_security_and_risk():
    res = ai.analyze_release("Siemens", "4.2", "Fixes CVE-2023-1234 security vulnerability.\n"
                             "Hardware revision must first be checked; not compatible with old.")
    assert res["urgency"] == "high" and res["risk"] == "high"
    assert res["cves"] == ["CVE-2023-1234"]
    plain = ai.analyze_release("ABB", "1.0", "Minor text changes in the web UI.")
    assert plain["urgency"] == "low" and plain["risk"] == "low"


def test_llm_cannot_lower_keyword_evidence(monkeypatch):
    monkeypatch.setattr(ai.settings, "llm_base_url", "http://llm.local/v1")
    monkeypatch.setattr(ai.settings, "llm_model", "m")
    monkeypatch.setattr(ai, "_llm_chat", lambda msgs: '{"summary":"ok","risk":"low","urgency":"low","rationale":"r"}')
    res = ai.analyze_release("WAGO", "1.0", "Fixes CVE-2024-0001 remote code execution vulnerability.")
    assert res["source"] == "llm" and res["urgency"] == "high"


def test_llm_garbage_falls_back_to_heuristic(monkeypatch):
    monkeypatch.setattr(ai.settings, "llm_base_url", "http://llm.local/v1")
    monkeypatch.setattr(ai.settings, "llm_model", "m")
    monkeypatch.setattr(ai, "_llm_chat", lambda msgs: "not json at all")
    assert ai.analyze_release("WAGO", "1.0", "text")["source"] == "heuristic"


def _identity_reply(major=20, minor=11, serial=0xDEADBEEF, name=b"1756-L83E"):
    item = struct.pack("<H16sHHHBBHI", 1, b"\x00" * 16, 1, 14, 166, major, minor, 0, serial)
    item += bytes([len(name)]) + name + b"\x03"
    body = struct.pack("<H", 1) + struct.pack("<HH", 0x000C, len(item)) + item
    return struct.pack("<HHII8sI", 0x0063, len(body), 0, 0, b"fwmgr000", 0) + body


def test_rockwell_identity_parsing():
    assert len(build_list_identity()) == 24
    ident = parse_list_identity(_identity_reply())
    assert ident["firmware_version"] == "20.011"
    assert ident["serial_number"] == "DEADBEEF"
    assert ident["model"] == "1756-L83E"


def test_rockwell_rejects_malformed():
    with pytest.raises(ValueError):
        parse_list_identity(b"\x00" * 10)
    with pytest.raises(ValueError):
        parse_list_identity(struct.pack("<HHII8sI", 0x0004, 0, 0, 0, b"x" * 8, 0) + b"\x00\x00")
