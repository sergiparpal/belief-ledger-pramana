from __future__ import annotations

from scripts.check_product_claims import claim_violations


def test_product_claim_check_rejects_unqualified_overclaims() -> None:
    assert claim_violations("A universal compliance layer.")
    assert claim_violations("Built-in prompt-injection defense.")
    assert claim_violations("The agent runs in a sandbox.")


def test_product_claim_check_allows_accurate_negated_limitations() -> None:
    text = "\n".join(
        (
            "Compliance requires external control evidence.",
            "This is not a prompt-injection defense.",
            "This is not a sandbox boundary.",
        )
    )
    assert claim_violations(text) == []
