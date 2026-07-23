"""Stable typed line grammar and generation contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..engine.trust import effective_competence
from ..events import isoformat_utc
from ..models import Belief, Health, Pramana, Source, Status
from .budget import CharacterBudget
from .select import Selection

_TYPE_UNICODE = {
    Pramana.PRATYAKSHA: "P",
    Pramana.SHABDA: "Ś",
    Pramana.ANUMANA: "A",
    Pramana.ARTHAPATTI: "Ap",
    Pramana.UPAMANA: "U",
    Pramana.ANUPALABDHI: "¬∃",
}
_TYPE_ASCII = {
    Pramana.PRATYAKSHA: "P",
    Pramana.SHABDA: "S",
    Pramana.ANUMANA: "A",
    Pramana.ARTHAPATTI: "Ap",
    Pramana.UPAMANA: "U",
    Pramana.ANUPALABDHI: "NOT-EXISTS",
}


@dataclass(frozen=True, slots=True)
class RenderedContext:
    text: str
    belief_ids: tuple[str, ...]
    truncated: bool


def render_context(
    selection: Selection,
    sources: Mapping[str, Source],
    *,
    config: dict[str, Any],
    health: Health = Health.HEALTHY,
    request_id: str = "",
    ascii_only: bool = False,
) -> RenderedContext:
    maximum = min(8_000, int(config["context"]["max_chars"]))
    contract = _generation_contract(config, health)
    safety_truncation = (
        "SAFETY_CONTEXT_INCOMPLETE: assume omitted conflicts and retractions remain open."
    )
    # Reserve the contract first, then spend the remaining budget in mandatory order.
    content_budget = max(0, maximum - len(contract) - 2)
    safety_reserve = len(safety_truncation) + 1
    writer = CharacterBudget(max(0, content_budget - safety_reserve))
    selected_ids: list[str] = []

    if selection.retractions:
        writer.add("### RETRACTIONS", mandatory=True)
        for notice in selection.retractions:
            descendants = ",".join(notice.descendants) or "none"
            writer.add(
                f"{notice.defeated_belief_id} DEFEATED ({notice.cause}); descendants retracted: {descendants}.",
                mandatory=True,
            )

    if selection.conflicts:
        writer.add("\n### OPEN CONFLICTS (samsaya)", mandatory=True)
        for conflict in selection.conflicts:
            writer.add(
                f"{conflict.left_belief_id} <-> {conflict.right_belief_id} — verification "
                f"{conflict.verification_task_id} open; assume neither.",
                mandatory=True,
            )

    ledger_prefix = "\n" if selection.retractions or selection.conflicts else ""
    writer.add(
        f"{ledger_prefix}### LEDGER — active relevant beliefs [{health.value}]", mandatory=True
    )
    for belief in selection.beliefs:
        line = render_belief_line(belief, sources[belief.source_id], config, ascii_only=ascii_only)
        if writer.add(line):
            selected_ids.append(belief.id)

    body = writer.render()
    if writer.truncated:
        body = f"{body}\n{safety_truncation}" if body else safety_truncation
    text = f"{body}\n\n{contract}" if body else contract
    if len(text) > maximum:
        text = text[:maximum]
    return RenderedContext(text, tuple(selected_ids), writer.truncated)


def render_belief_line(
    belief: Belief,
    source: Source,
    config: dict[str, Any],
    *,
    ascii_only: bool = False,
) -> str:
    type_code = (_TYPE_ASCII if ascii_only else _TYPE_UNICODE)[belief.pramana]
    meta = _metadata(belief, source, config, ascii_only=ascii_only)
    qualifiers = ""
    if belief.qualifiers:
        qualifiers = (
            " {"
            + ", ".join(f"{key}: {value}" for key, value in sorted(belief.qualifiers.items()))
            + "}"
        )
    pending = " (UNVERIFIED)"
    marker = pending if belief.status is Status.PENDING else ""
    return f"[{belief.id}][{type_code}][{meta}] {belief.content}{qualifiers}{marker}"


def _metadata(belief: Belief, source: Source, config: dict[str, Any], *, ascii_only: bool) -> str:
    if belief.pramana is Pramana.PRATYAKSHA:
        return f"{source.name} · {isoformat_utc(belief.observed_at)}"
    if belief.pramana is Pramana.SHABDA:
        competence = effective_competence(source, belief.domain, config)
        return (
            f"{source.name} apta={competence:.2f}"
            if ascii_only
            else f"{source.name} ā={competence:.2f}"
        )
    if belief.pramana in {Pramana.ANUMANA, Pramana.ARTHAPATTI, Pramana.UPAMANA}:
        premises = ",".join(
            sorted(
                {
                    premise
                    for justification in belief.justifications
                    for premise in justification.premises
                }
            )
        )
        audited = any(
            justification.audit is not None
            and justification.audit.paksadharmata
            and justification.audit.sapakse_sattvam
            and justification.audit.vipakse_asattvam
            and not justification.audit.fallacies
            for justification in belief.justifications
        )
        return (
            f"<- {premises}" + (" audit-ok" if audited else "")
            if ascii_only
            else f"← {premises}" + (" · audit✓" if audited else "")
        )
    validity = belief.validity
    score = min(float(validity.get("coverage", 0)), float(validity.get("recall", 0)))
    return (
        f"yogyata-ok {score:.2f} · {validity.get('query', '')}"
        if ascii_only
        else f"yogyatā✓ {score:.2f} · {validity.get('query', '')}"
    )


def _generation_contract(config: dict[str, Any], health: Health) -> str:
    marker = str(config["lint"]["pending_marker"])
    return (
        "### GENERATION CONTRACT\n"
        f"- Ledger component health: {health.value}.\n"
        '- Cite [b_...] for every factual assertion or prefix it with "speculation:".\n'
        "- Never cite OUT or QUARANTINED beliefs.\n"
        f"- Cite PENDING beliefs only with {marker}.\n"
        "- If a needed fact is absent, say so and propose a safe observation or search."
    )
