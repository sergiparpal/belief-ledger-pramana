"""Immutable host-neutral lifecycle and capability values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EnforcementProfile(StrEnum):
    OBSERVE = "observe"
    ACTION_ENFORCE = "action_enforce"
    ACCEPTED_FINAL = "accepted_final"
    STRICT = "strict"


@dataclass(frozen=True, slots=True)
class ProfileSelection:
    schema_version: int
    requested: EnforcementProfile
    effective: EnforcementProfile
    missing: tuple[str, ...]
    reason_codes: tuple[str, ...]
    downgraded: bool


@dataclass(frozen=True, slots=True)
class EpisodeContext:
    schema_version: int
    session_id: str | None
    turn_id: str | None
    task_id: str | None
    platform: str
    model: str
    correlation: tuple[tuple[str, str], ...] = ()

    @classmethod
    def normalize(
        cls,
        *,
        session_id: object = None,
        turn_id: object = None,
        task_id: object = None,
        platform: object = None,
        model: object = None,
        correlation: Mapping[str, object] | None = None,
    ) -> EpisodeContext:
        def optional(value: object) -> str | None:
            normalized = str(value or "").strip()
            return normalized or None

        pairs = tuple(
            sorted(
                (str(key), str(value))
                for key, value in (correlation or {}).items()
                if value is not None and str(value)
            )
        )
        return cls(
            1,
            optional(session_id),
            optional(turn_id),
            optional(task_id),
            optional(platform) or "unknown",
            optional(model) or "unknown",
            pairs,
        )

    @property
    def stable_turn_id(self) -> str:
        return self.turn_id or "unidentified-turn"

    @property
    def persisted_session_id(self) -> str:
        return self.session_id or "unidentified-session"

    @property
    def persisted_task_id(self) -> str:
        return self.task_id or "unidentified-task"


@dataclass(frozen=True, slots=True)
class HostCapabilities:
    schema_version: int = 1
    per_request_context: bool = False
    pre_action_gate: bool = False
    atomic_action_token_consume: bool = False
    accepted_final_transform: bool = False
    exclusive_final_output_gate: bool = False
    buffered_stream_delivery: bool = False
    bound_approval: bool = False
    tool_inventory: bool = False

    def missing_for(self, profile: EnforcementProfile) -> tuple[str, ...]:
        required = {
            EnforcementProfile.OBSERVE: (),
            EnforcementProfile.ACTION_ENFORCE: ("pre_action_gate",),
            EnforcementProfile.ACCEPTED_FINAL: (
                "pre_action_gate",
                "per_request_context",
                "accepted_final_transform",
            ),
            EnforcementProfile.STRICT: (
                "pre_action_gate",
                "tool_inventory",
                "per_request_context",
                "accepted_final_transform",
                "atomic_action_token_consume",
                "exclusive_final_output_gate",
                "buffered_stream_delivery",
                "bound_approval",
            ),
        }[profile]
        return tuple(name for name in required if not getattr(self, name))

    def maximum_profile(self) -> EnforcementProfile:
        for profile in reversed(tuple(EnforcementProfile)):
            if not self.missing_for(profile):
                return profile
        return EnforcementProfile.OBSERVE


def negotiate_profile(
    capabilities: HostCapabilities,
    requested: EnforcementProfile,
    *,
    allow_diagnostic_downgrade: bool = False,
    observe_only: bool = False,
) -> ProfileSelection:
    """Select an effective profile as a deterministic, side-effect-free operation."""

    missing = capabilities.missing_for(requested)
    if observe_only:
        reasons = ("OBSERVE_MODE", *tuple(f"MISSING_CAPABILITY:{name}" for name in missing))
        return ProfileSelection(
            1,
            requested,
            EnforcementProfile.OBSERVE,
            missing,
            reasons,
            requested is not EnforcementProfile.OBSERVE,
        )
    if not missing:
        return ProfileSelection(1, requested, requested, (), ("PROFILE_SUPPORTED",), False)
    reason_codes = tuple(f"MISSING_CAPABILITY:{name}" for name in missing)
    if requested is EnforcementProfile.OBSERVE:
        return ProfileSelection(1, requested, requested, missing, reason_codes, False)
    if not allow_diagnostic_downgrade:
        return ProfileSelection(
            1,
            requested,
            requested,
            missing,
            ("CAPABILITY_SHORTFALL", *reason_codes),
            False,
        )
    effective = capabilities.maximum_profile()
    return ProfileSelection(
        1,
        requested,
        effective,
        missing,
        ("PROFILE_DOWNGRADED", *reason_codes),
        effective is not requested,
    )


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    schema_version: int
    context: EpisodeContext
    namespace: str
    name: str
    arguments: tuple[tuple[str, Any], ...]
    description: str = ""

    @classmethod
    def normalize(
        cls,
        context: EpisodeContext,
        name: str,
        arguments: Mapping[str, Any],
        *,
        namespace: str = "",
        description: str = "",
    ) -> ToolInvocation:
        return cls(
            1,
            context,
            namespace.strip(),
            name.strip(),
            tuple(sorted((str(key), value) for key, value in arguments.items())),
            description.strip(),
        )

    def arguments_dict(self) -> dict[str, Any]:
        return dict(self.arguments)


@dataclass(frozen=True, slots=True)
class ToolResult:
    schema_version: int
    context: EpisodeContext
    namespace: str
    name: str
    content: str
    status: str
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    schema_version: int
    context: EpisodeContext
    approved: bool
    namespace: str
    tool_name: str
    arguments_hash: str
    target: str
    policy_id: str
    policy_revision: str
    scope: str


@dataclass(frozen=True, slots=True)
class OutputCandidate:
    schema_version: int
    context: EpisodeContext
    content: str
    stakes: str
    final: bool = True


@dataclass(frozen=True, slots=True)
class NormalizedDecision:
    schema_version: int
    outcome: str
    reason_code: str
    missing: tuple[str, ...] = ()
    suggested_observation: str | None = None
    action_token: str | None = None
