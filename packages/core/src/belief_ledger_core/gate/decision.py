"""Fail-closed gate decisions and auditable redacted records."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..engine.trust import determine_admission
from ..events import EventDraft, canonical_json, content_hash
from ..models import GateDecision, GateOutcome, Stakes, max_stakes
from ..store import LedgerStore
from .classify import ActionPolicyRegistry
from .preconditions import resolve_preconditions


class ActionGate:
    def __init__(
        self,
        store: LedgerStore,
        config: dict[str, Any],
        policies: ActionPolicyRegistry,
    ) -> None:
        self.store = store
        self.config = config
        self.policies = policies

    def evaluate(
        self,
        episode_id: str,
        tool_name: str,
        args: dict[str, Any],
        *,
        description: str = "",
        action_stakes: Stakes | None = None,
    ) -> GateDecision:
        episode = self.store.get_episode(episode_id)
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

        beliefs = self.store.list_beliefs(episode_id)
        sources = {source.id: source for source in self.store.list_sources(episode_id)}
        conflicts = self.store.list_conflicts(episode_id)
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
                belief = belief_map[check.belief_id]
                admission = determine_admission(
                    belief,
                    sources[belief.source_id],
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
                "Confirm or deny through Hermes approval",
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
            "args_hash": content_hash(canonical_json(args)),
            "outcome": decision.outcome.value,
            "reason_code": decision.reason_code,
            "detail": {
                "stakes": decision.stakes.value,
                "missing": list(decision.missing),
                "suggested_observation": decision.suggested_observation,
                "classification": classification_reason,
            },
        }
        self.store.append_events(
            episode_id,
            [EventDraft("GATE_DECIDED", "gate_decision", tool_name, payload)],
        )
