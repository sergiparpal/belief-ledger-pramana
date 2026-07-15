from __future__ import annotations

import pytest

from belief_ledger_pramana.events import content_hash
from belief_ledger_pramana.ingestion.adapters import ToolAdapterRegistry
from belief_ledger_pramana.ingestion.tool import prepare_evidence, redact_secrets


@pytest.mark.parametrize(
    ("value", "secret"),
    [
        ('{"api_key":"sk-example-secret-value"}', "sk-example-secret-value"),
        ("OPENAI_API_KEY=sk-example-secret-value", "sk-example-secret-value"),
        ("Cookie: sessionid=secret-session-value", "secret-session-value"),
        ("Set-Cookie: session=secret-session-value; HttpOnly", "secret-session-value"),
        ("-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----", "private-material"),
        ("token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature", "eyJhbGciOiJIUzI1NiJ9"),
        ("Authorization: ApiKey top-secret-value", "top-secret-value"),
        ("postgresql://service:connection-secret@db.internal/ledger", "connection-secret"),
        ("token=generic-secret-value", "generic-secret-value"),
    ],
)
def test_redact_secrets_covers_structured_and_header_credentials(value: str, secret: str) -> None:
    redacted, changed = redact_secrets(value)
    assert changed
    assert secret not in redacted


def test_prepare_evidence_hashes_original_but_never_persists_detected_secret() -> None:
    value = "Cookie: sessionid=secret-session-value"
    evidence = prepare_evidence(value, mode="full", max_excerpt_chars=1_000)
    assert evidence.redacted
    assert evidence.payload is not None and "secret-session-value" not in evidence.payload
    assert evidence.full_hash != evidence.payload
    assert evidence.full_hash != content_hash(value)


def test_redaction_does_not_change_ordinary_text() -> None:
    value = "The token budget is 100 and the report is complete."
    assert redact_secrets(value) == (value, False)


def test_tool_metadata_does_not_persist_credential_bearing_url() -> None:
    secret = "url-secret-value"
    adapted = ToolAdapterRegistry().adapt(
        "fetch_url",
        {"url": f"https://api.example.test/data?token={secret}"},
        "ok",
    )
    assert secret not in str(adapted.metadata)
