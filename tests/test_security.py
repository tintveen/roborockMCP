from __future__ import annotations

from roborock_mcp.security import opaque_key, redact


def test_opaque_key_is_stable_and_hides_input() -> None:
    key = opaque_key("device", "secret-duid")
    assert key == opaque_key("device", "secret-duid")
    assert "secret" not in key


def test_recursive_redaction() -> None:
    redacted = redact({"token": "secret", "nested": {"localKey": "key", "ok": 2}, "blob": b"abc"})
    assert redacted == {
        "token": "[REDACTED]",
        "nested": {"localKey": "[REDACTED]", "ok": 2},
        "blob": "[REDACTED BYTES: 3]",
    }
