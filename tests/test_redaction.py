"""Redaction must be shown to work, not just default to empty (Chapter 15)."""
from arh.harness.redaction import redact_arguments, arguments_hash


def test_seeded_secret_is_redacted_including_nested():
    args = {"account_id": "A-1007", "api_key": "sk-live-SECRET",
            "meta": {"password": "hunter2", "note": "ok"},
            "items": [{"authorization": "Bearer XYZ"}]}
    out = redact_arguments("issue_credit", args)
    assert out["api_key"] == "[REDACTED]"
    assert out["meta"]["password"] == "[REDACTED]"     # nested object
    assert out["items"][0]["authorization"] == "[REDACTED]"  # nested in a list
    assert out["account_id"] == "A-1007"               # non-secret kept
    assert out["meta"]["note"] == "ok"


def test_tokenize_is_stable_and_non_reversible():
    a = redact_arguments("t", {"token": "abc"})["token"]
    b = redact_arguments("t", {"token": "abc"})["token"]
    assert a == b and a.startswith("tok_") and "abc" not in a


def test_correlation_digest_excludes_secrets():
    # Two requests differing only in a secret produce the SAME correlation
    # digest, because the digest is computed over redacted values.
    d1 = arguments_hash({"account_id": "A-1007", "api_key": "one"})
    d2 = arguments_hash({"account_id": "A-1007", "api_key": "two"})
    assert d1 == d2
