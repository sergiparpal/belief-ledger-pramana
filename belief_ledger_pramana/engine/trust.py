"""svataḥ/parataḥ/quarantine admission and learned āpta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import (
    Belief,
    Integrity,
    Perishability,
    Pramana,
    Source,
    SourceKind,
    Stakes,
    Status,
    VerificationMethod,
    max_stakes,
)


@dataclass(frozen=True, slots=True)
class TrustDecision:
    status: Status
    profile: str
    mode: str
    effective_stakes: Stakes
    k_required: int
    method: VerificationMethod | None
    reason: str


def trust_profile(belief: Belief, source: Source) -> str:
    if belief.pramana is Pramana.PRATYAKSHA:
        return "pratyaksha_tool"
    if belief.pramana is Pramana.ANUPALABDHI:
        return "anupalabdhi"
    if belief.pramana in {Pramana.ANUMANA, Pramana.ARTHAPATTI, Pramana.UPAMANA}:
        return "anumana_registered"
    if source.kind is SourceKind.USER:
        return "user_self" if bool(belief.validity.get("about_self")) else "user_world"
    if source.kind is SourceKind.WEB:
        return "shabda_web_semi" if source.integrity is Integrity.SEMI else "shabda_web_untrusted"
    if source.integrity is Integrity.TRUSTED:
        return "shabda_internal_trusted"
    return "shabda_web_untrusted"


def determine_admission(
    belief: Belief,
    source: Source,
    config: dict[str, Any],
    *,
    episode_stakes: Stakes,
    action_stakes: Stakes | None = None,
) -> TrustDecision:
    effective = max_stakes(episode_stakes, belief.stakes, action_stakes or Stakes.LOW)
    profile = trust_profile(belief, source)
    cell = config["trust"]["matrix"][profile][effective.value]
    mode = str(cell["mode"])
    k_required = int(cell.get("k", 0))
    method_raw = cell.get("method")
    method = VerificationMethod(str(method_raw)) if method_raw else None

    # R4: transport is not a source; LIVE facts must be re-observed.
    if source.kind is SourceKind.LEDGER and belief.perishability is Perishability.LIVE:
        return TrustDecision(
            Status.PENDING,
            profile,
            "paratah",
            effective,
            1,
            VerificationMethod.TOOL_RECHECK,
            "prior LIVE belief requires re-observation",
        )
    if mode == "svatah" or mode == "yogyata":
        return TrustDecision(
            Status.IN, profile, mode, effective, k_required, method, "admitted by configured policy"
        )
    if mode == "paratah":
        return TrustDecision(
            Status.PENDING, profile, mode, effective, k_required, method, "verification required"
        )
    if mode == "quarantine":
        return TrustDecision(
            Status.QUARANTINED,
            profile,
            mode,
            effective,
            k_required,
            method,
            "source is quarantined at effective stakes",
        )
    return TrustDecision(
        Status.OUT,
        profile,
        mode,
        effective,
        k_required,
        method,
        "type is not admitted at effective stakes",
    )


def effective_competence(source: Source, domain: str, config: dict[str, Any]) -> float:
    """Smoothed Beta-style competence, bounded and inert before enough samples."""

    apta = config["trust"]["apta"]
    prior = float(source.competence.get(domain, source.competence.get("general", 0.5)))
    stats = source.stats
    if stats.samples < int(apta["minimum_samples"]):
        return _bounded(prior, apta)
    alpha = float(apta["alpha_prior"]) + stats.confirmed
    beta = float(apta["beta_prior"]) + stats.defeated
    posterior = alpha / (alpha + beta)
    # The configured prior remains half of the learned estimate so sparse history is stable.
    return _bounded((prior + posterior) / 2.0, apta)


def _bounded(value: float, apta: dict[str, Any]) -> float:
    return max(float(apta["floor"]), min(float(apta["ceiling"]), value))
