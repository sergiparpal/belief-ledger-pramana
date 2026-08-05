"""Fail-closed gate decisions and auditable redacted records."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol, cast

from ..engine.trust import determine_admission
from ..events import EventDraft, canonical_json, content_hash
from ..ingestion.tool import redact_secrets
from ..models import (
    Belief,
    Conflict,
    Episode,
    Event,
    GateDecision,
    GateOutcome,
    Source,
    Stakes,
    max_stakes,
)
from .classify import ActionPolicyRegistry
from .preconditions import resolve_preconditions


class ActionGateReader(Protocol):
    """Read model required to evaluate an action policy.

    Deliberately narrowed to exactly the calls the gate makes, so both `LedgerStore` and a
    port adapter satisfy it structurally without either widening its own signature.
    """

    def get_episode(self, episode_id: str) -> Episode | None: ...

    def list_beliefs(self, episode_id: str) -> list[Belief]: ...

    def list_sources(self, episode_id: str) -> list[Source]: ...

    def list_conflicts(self, episode_id: str) -> list[Conflict]: ...


class ActionGateWriter(Protocol):
    """Append the auditable record of one gate decision."""

    def append_events(
        self,
        episode_id: str,
        drafts: Sequence[EventDraft],
        *,
        correlation: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        require_open_verification_task_id: str | None = None,
    ) -> list[Event]: ...


class ActionGate:
    """The single host-neutral action gate.

    ``reader`` and ``writer`` are structural ports: ``LedgerStore`` satisfies both, and an
    adapter may supply narrower objects instead. Adapters must not re-implement this class;
    a second copy is how the audited ``args_hash`` encoding silently diverged before.
    """

    def __init__(
        self,
        reader: ActionGateReader,
        config: dict[str, Any],
        policies: ActionPolicyRegistry,
        *,
        writer: ActionGateWriter | None = None,
    ) -> None:
        self._reader = reader
        # Legacy callers pass one LedgerStore that structurally satisfies both ports.
        # New composition roots provide a writer explicitly.
        self._writer = writer if writer is not None else cast(ActionGateWriter, reader)
        self.config = config
        self.policies = policies

    @property
    def store(self) -> ActionGateReader:
        """Backwards-compatible alias for the read port."""

        return self._reader

    def evaluate(
        self,
        episode_id: str,
        tool_name: str,
        args: dict[str, Any],
        *,
        description: str = "",
        action_stakes: Stakes | None = None,
    ) -> GateDecision:
        episode = self._reader.get_episode(episode_id)
        if episode is None:
            return GateDecision(
                GateOutcome.BLOCK,
                "EPISODE_UNAVAILABLE",
                "BLOCKED [EPISODE_UNAVAILABLE]: no ledger episode is available",
                Stakes.HIGH,
                ("ledger episode",),
                "Retry after session initialization",
            )
        enforce = self.config["mode"] == "enforce"
        classification = self.policies.classify(
            tool_name,
            args,
            description=description,
            enforce=enforce,
            unknown_tool_policy=str(self.config["gating"]["unknown_tool_policy"]),
        )
        stakes = max_stakes(
            episode.default_stakes, classification.policy.base_stakes, action_stakes or Stakes.LOW
        )
        if not bool(self.config["gating"]["enabled"]):
            decision = GateDecision(
                GateOutcome.ALLOW, "GATE_DISABLED", "Gate disabled by operator", stakes
            )
            self._record(episode_id, tool_name, args, decision, classification.reason)
            return decision
        if not classification.known and classification.policy.effectful and enforce:
            missing_rule = "operator action-policy rule"
            decision = GateDecision(
                GateOutcome.BLOCK,
                "UNKNOWN_EFFECTFUL_TOOL",
                f"BLOCKED [UNKNOWN_EFFECTFUL_TOOL]: {missing_rule} is missing for {tool_name}",
                stakes,
                (missing_rule,),
                "Add an exact or anchored action-policy rule, then retry",
            )
            self._record(episode_id, tool_name, args, decision, classification.reason)
            return decision
        if not classification.policy.effectful:
            decision = GateDecision(
                GateOutcome.ALLOW, "READ_ONLY", "Known read-only action", stakes
            )
            self._record(episode_id, tool_name, args, decision, classification.reason)
            return decision

        beliefs = self._reader.list_beliefs(episode_id)
        sources = {source.id: source for source in self._reader.list_sources(episode_id)}
        conflicts = self._reader.list_conflicts(episode_id)
        preconditions = classification.policy.preconditions
        if (
            stakes is Stakes.CRITICAL
            and bool(self.config["verification"].get("critical_human_confirmation", False))
            and "explicit_user_confirmation" not in preconditions
        ):
            preconditions = (*preconditions, "explicit_user_confirmation")
        checks = resolve_preconditions(
            preconditions,
            action_name=tool_name,
            args=args,
            target_fields=classification.policy.target_fields,
            beliefs=beliefs,
            sources=sources,
            conflicts=conflicts,
            minimum_integrity=classification.policy.minimum_priority,
            confirmation_ttl_seconds=int(self.config["gating"]["confirmation_ttl_seconds"]),
        )
        belief_map = {belief.id: belief for belief in beliefs}
        elevated_checks = []
        for check in checks:
            if check.satisfied and check.belief_id:
                belief = belief_map.get(check.belief_id)
                source = sources.get(belief.source_id) if belief is not None else None
                if belief is None or source is None:
                    # A satisfied check whose belief or source is no longer readable cannot
                    # be re-confirmed at the action's stakes, so it fails closed.
                    check = replace(
                        check,
                        satisfied=False,
                        reason="supporting belief or source is unavailable",
                        suggestion="Re-observe this precondition before retrying",
                    )
                else:
                    admission = determine_admission(
                        belief,
                        source,
                        self.config,
                        episode_stakes=episode.default_stakes,
                        action_stakes=stakes,
                    )
                    if admission.status.value != "in":
                        check = replace(
                            check,
                            satisfied=False,
                            reason=f"belief requires {admission.mode} at {stakes.value} stakes",
                            suggestion="Verify this precondition at the action's effective stakes",
                        )
            elevated_checks.append(check)
        checks = tuple(elevated_checks)
        missing_preconditions = tuple(check.proposition for check in checks if not check.satisfied)
        if not missing_preconditions:
            decision = GateDecision(
                GateOutcome.ALLOW,
                "PRECONDITIONS_SATISFIED",
                "All action preconditions are IN",
                stakes,
            )
        elif (
            classification.policy.allow_human_approval
            and bool(self.config["gating"]["allow_human_approval"])
            and len(missing_preconditions) == 1
            and next(check for check in checks if not check.satisfied).name
            == "explicit_user_confirmation"
        ):
            decision = GateDecision(
                GateOutcome.APPROVE,
                "HUMAN_CONFIRMATION_REQUIRED",
                f"Human confirmation required: {missing_preconditions[0]}",
                stakes,
                missing_preconditions,
                "Confirm or deny through the host approval surface",
                f"belief-ledger:{classification.policy.id}",
            )
        else:
            first = next(check for check in checks if not check.satisfied)
            decision = GateDecision(
                GateOutcome.BLOCK,
                "MISSING_PRECONDITION",
                f"BLOCKED [MISSING_PRECONDITION]: {first.proposition}",
                stakes,
                missing_preconditions,
                first.suggestion,
            )
        self._record(episode_id, tool_name, args, decision, classification.reason)
        return decision

    def _record(
        self,
        episode_id: str,
        tool_name: str,
        args: dict[str, Any],
        decision: GateDecision,
        classification_reason: str,
    ) -> None:
        payload = {
            "tool_name": tool_name,
            "args_hash": arguments_digest(args),
            "outcome": decision.outcome.value,
            "reason_code": decision.reason_code,
            "detail": {
                "stakes": decision.stakes.value,
                "missing": list(decision.missing),
                "suggested_observation": decision.suggested_observation,
                "classification": classification_reason,
            },
        }
        self._writer.append_events(
            episode_id,
            [EventDraft("GATE_DECIDED", "gate_decision", tool_name, payload)],
        )


def arguments_digest(args: dict[str, Any]) -> str:
    """Digest tool arguments for the audit record without ever failing the decision.

    A host may pass values canonical JSON cannot encode. Recording an audit hash must never
    turn a completed fail-closed decision into an unhandled exception, so unsupported values
    fall back to a deterministic ``repr`` encoding. Secret-like material is removed first, so
    the recorded digest never commits to a credential.
    """

    try:
        serialized = canonical_json(args)
    except (TypeError, ValueError):
        serialized = canonical_json(_encodable(args))
    return content_hash(redact_secrets(serialized)[0])


def _encodable(value: Any) -> Any:
    """Coerce an arbitrary host value into something ``canonical_json`` accepts."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, Mapping):
        return {str(key): _encodable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_encodable(item) for item in value]
        return sorted(items, key=canonical_json) if isinstance(value, (set, frozenset)) else items
    return repr(value)
