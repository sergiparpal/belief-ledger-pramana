"""Digests that make a model component's non-determinism detectable.

More than one `HostLlmClient` implementation exists: the one over `StructuredModelPort` here, and
an adapter-side one in the backward-compatible distribution. Both must produce byte-identical
digests for the same call, or the divergence query would report an adapter change as a model
divergence. That is why the computation lives here rather than in either client.

See `docs/adr/0012-llm-call-attribution.md`.
"""

from __future__ import annotations

from typing import Any

from ..dependencies import SamplingPolicy
from ..events import canonical_json, content_hash, to_primitive
from ..ingestion.tool import redact_secrets


def prompt_hash(instructions: str) -> str:
    """Identify the prompt by digesting it.

    `llm/prompts.py` calls itself versioned but carries no version constant. A digest of the
    instruction text is that version, exactly, and cannot fall out of step with the text the way a
    hand-maintained number would.
    """
    return content_hash(instructions)


def call_input_hash(
    *,
    instructions: str,
    text: str,
    schema_name: str,
    max_tokens: int,
) -> str:
    """Digest the whole request, not only its free text.

    `ComponentVerdict.input_hash` deliberately digests `text` alone, because adapters compare
    against it through `component_verdict_input_hashes`. Divergence needs something stricter: two
    calls with the same text but a different schema or token ceiling are not the same input, and
    grouping them together would report a difference in the request as a difference in the model.

    The text is redacted before hashing, exactly as `redacted_content_hash` does, so the digest
    never commits to a credential.
    """
    redacted, _ = redact_secrets(text)
    return content_hash(
        canonical_json(
            {
                "instructions": instructions,
                "max_tokens": int(max_tokens),
                "schema_name": schema_name,
                "text": redacted,
            }
        )
    )


def call_output_hash(parsed: Any) -> str:
    """Digest the canonicalised structured result.

    Canonical JSON is what makes this comparable: key order and float formatting are normalized, so
    two results differ here only when they differ in content.
    """
    return content_hash(canonical_json(to_primitive(parsed)))


def sampling_policy(temperature: Any | None = None) -> SamplingPolicy:
    """Build the policy from a configured value, or the `temperature=0.0` default when unset.

    Both clients call this so the default lives in exactly one place. An out-of-range or
    non-numeric configured value raises from `SamplingPolicy.__post_init__` rather than being
    silently clamped, because a silently corrected sampling setting is worse than a failed start:
    the recorded policy would no longer describe what was asked of the provider.
    """
    if temperature is None:
        return SamplingPolicy()
    return SamplingPolicy(float(temperature))
