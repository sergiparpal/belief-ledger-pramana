"""Local asiddha checks and structured chain-audit result validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..models import ChainAudit, Justification, Status


def local_asiddha(
    justification: Justification, premise_statuses: Mapping[str, Status]
) -> tuple[str, ...]:
    return tuple(
        premise
        for premise in justification.premises
        if premise_statuses.get(premise) is not Status.IN
    )


def validate_chain_audit(value: Any) -> ChainAudit:
    if not isinstance(value, dict):
        raise ValueError("chain audit result must be an object")
    required = ("paksadharmata", "sapakse_sattvam", "vipakse_asattvam")
    if not all(isinstance(value.get(key), bool) for key in required):
        raise ValueError("chain audit booleans are missing")
    evidence_ids = value.get("evidence_ids", [])
    fallacies = value.get("fallacies", [])
    allowed = {"savyabhicara", "viruddha", "satpratipaksa", "asiddha", "badhita"}
    if not isinstance(evidence_ids, list) or not all(
        isinstance(item, str) for item in evidence_ids
    ):
        raise ValueError("chain audit evidence_ids are invalid")
    if not isinstance(fallacies, list) or not all(item in allowed for item in fallacies):
        raise ValueError("chain audit fallacies are invalid")
    return ChainAudit(
        paksadharmata=value["paksadharmata"],
        sapakse_sattvam=value["sapakse_sattvam"],
        vipakse_asattvam=value["vipakse_asattvam"],
        evidence_ids=tuple(evidence_ids),
        fallacies=tuple(fallacies),
    )
