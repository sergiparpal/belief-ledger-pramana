"""Versioned deterministic newline-delimited JSON decision protocol."""

from __future__ import annotations

import copy
import json
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any, BinaryIO, TextIO, cast

from belief_ledger_core import (
    BeliefLedger,
    CoreConfig,
    EpisodeContext,
    EvidenceObservation,
    OutputCandidate,
    ToolInvocation,
)
from belief_ledger_core.events import canonical_json, to_primitive
from belief_ledger_core.ingestion.tool import redact_secrets

MAX_LINE_BYTES = 1_048_576
PROTOCOL_VERSION = 1
MAX_IDEMPOTENCY_ENTRIES = 1_024
_READ_CHUNK = 65_536


class ProtocolError(ValueError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(detail)


class GatewayService:
    """Stateful local decision service. It reports observe and does not execute actions."""

    def __init__(self, state_root: Path, *, ledger: BeliefLedger | None = None) -> None:
        requested_root = state_root.expanduser().absolute()
        self.ledger = ledger or open_gateway_ledger(requested_root)
        self.state_root = self.ledger.state_root
        self.episode_id: str | None = None
        self.context: EpisodeContext | None = None
        self.shutdown_requested = False
        self._idempotency: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("schema_version") != PROTOCOL_VERSION:
            raise ProtocolError("UNSUPPORTED_SCHEMA", "schema_version must be 1")
        operation = request.get("operation", request.get("op"))
        if not isinstance(operation, str) or not operation:
            raise ProtocolError("MISSING_OPERATION", "operation must be a non-empty string")
        request_id = request.get("request_id", "")
        if not isinstance(request_id, str):
            raise ProtocolError("INVALID_REQUEST_ID", "request_id must be a string")
        idem = request.get("idempotency_key")
        if idem is not None and (not isinstance(idem, str) or not idem):
            raise ProtocolError("INVALID_IDEMPOTENCY_KEY", "idempotency_key must be non-empty")
        # request_id is excluded so that a retry under the same key, which a client is
        # expected to correlate with a fresh request_id, is served the cached response
        # rather than rejected as a different request.
        fingerprint = canonical_json({k: v for k, v in request.items() if k != "request_id"})
        if idem in self._idempotency:
            previous_fingerprint, response = self._idempotency[str(idem)]
            if previous_fingerprint != fingerprint:
                raise ProtocolError(
                    "IDEMPOTENCY_KEY_REUSED", "key was already used for a different request"
                )
            self._idempotency.move_to_end(str(idem))
            return copy.deepcopy(response)
        result = self._dispatch(operation, request, idem)
        response = {
            "schema_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "ok": True,
            "result": result,
        }
        if idem is not None:
            self._idempotency[str(idem)] = (fingerprint, copy.deepcopy(response))
            self._idempotency.move_to_end(str(idem))
            while len(self._idempotency) > MAX_IDEMPOTENCY_ENTRIES:
                self._idempotency.popitem(last=False)
        return response

    def _dispatch(
        self, operation: str, request: dict[str, Any], idem: str | None = None
    ) -> dict[str, Any]:
        if operation == "capabilities":
            return {
                "profile": "observe",
                "reason_code": "DECISION_SERVICE_ONLY",
                "executes_actions": False,
                "protocol_version": PROTOCOL_VERSION,
                "max_line_bytes": MAX_LINE_BYTES,
            }
        if operation == "episode.start":
            if self.episode_id is not None:
                raise ProtocolError("EPISODE_ALREADY_STARTED", self.episode_id)
            context_value = _object(request.get("context", {}), "context")
            _validate_context(context_value)
            self.context = EpisodeContext.normalize(**context_value)
            handle = self.ledger.start_episode(self.context)
            self.episode_id = handle.id
            return asdict(handle)
        if operation == "shutdown":
            self.shutdown_requested = True
            return {"reason_code": "SHUTDOWN_REQUESTED"}
        if operation not in {
            "episode.finalize",
            "evidence.ingest",
            "action.evaluate",
            "decision.explain",
            "output.evaluate",
            "ledger.verify-chain",
            "ledger.replay",
        }:
            raise ProtocolError("UNSUPPORTED_OPERATION", "operation is not supported")
        episode_id, context = self._state()
        if operation == "episode.finalize":
            result = asdict(self.ledger.finalize_episode(episode_id))
            self.episode_id = None
            self.context = None
            return result
        if operation == "evidence.ingest":
            value = _object(request.get("observation"), "observation")
            _validate_observation(value)
            if idem is not None:
                # The in-memory cache is evictable and does not survive restart. Passing the
                # key down to the store's durable idempotency layer keeps a replay from
                # ingesting twice in either case. The prefix cannot collide with a
                # caller-supplied key in the same episode.
                correlation = dict(value.get("correlation") or {})
                correlation["idempotency_key"] = f"gateway:{idem}"
                value = {**value, "correlation": correlation}
            observation = EvidenceObservation.normalize(**value)
            return asdict(self.ledger.ingest_evidence(episode_id, observation))
        if operation == "action.evaluate":
            value = _object(request.get("invocation"), "invocation")
            arguments = _object(value.get("arguments", {}), "invocation.arguments")
            invocation = ToolInvocation.normalize(
                context,
                _string(value.get("name"), "invocation.name", non_empty=True),
                arguments,
                namespace=_string(value.get("namespace", ""), "invocation.namespace"),
                description=_string(value.get("description", ""), "invocation.description"),
            )
            authorization = self.ledger.evaluate_action(episode_id, invocation)
            return {
                "decision_id": authorization.decision_id,
                "outcome": authorization.outcome,
                "reason_code": authorization.reason_code,
                "missing": list(authorization.decision.missing),
                "policy_digest": authorization.policy_digest,
                "configuration_digest": authorization.configuration_digest,
                "enforced": False,
            }
        if operation == "decision.explain":
            decision_id = _string(request.get("decision_id"), "decision_id", non_empty=True)
            return cast(
                dict[str, Any],
                to_primitive(self.ledger.explain_decision(episode_id, decision_id)),
            )
        if operation == "output.evaluate":
            candidate = OutputCandidate(
                1,
                context,
                _string(request.get("content", ""), "content"),
                _string(request.get("stakes", "med"), "stakes", non_empty=True),
                _boolean(request.get("final", True), "final"),
            )
            evaluation = self.ledger.evaluate_output(episode_id, candidate)
            return {
                "accepted": evaluation.accepted,
                "reason_code": evaluation.reason_code,
                "lint_report_id": evaluation.lint_report_id,
                "content": evaluation.payload.decode("utf-8"),
                "delivered": False,
            }
        if operation == "ledger.verify-chain":
            return cast(dict[str, Any], to_primitive(self.ledger.verify_chain()))
        if operation == "ledger.replay":
            replay = self.ledger.replay()
            return {
                **cast(dict[str, Any], to_primitive(replay)),
                "deterministic": replay.deterministic,
            }
        raise AssertionError("validated operation was not dispatched")

    def _state(self) -> tuple[str, EpisodeContext]:
        if self.episode_id is None or self.context is None:
            raise ProtocolError("EPISODE_NOT_STARTED", "episode.start is required")
        return self.episode_id, self.context


def _bounded_lines(
    source: TextIO | BinaryIO, max_line_bytes: int
) -> Iterator[tuple[str | bytes, bool]]:
    """Yield (line, oversized) without ever buffering more than the limit.

    An oversized line is drained to the next newline and discarded rather than truncated,
    so its remainder cannot be read back as further requests.
    """

    while True:
        text_pieces: list[str] = []
        byte_pieces: list[bytes] = []
        total = 0
        oversized = False
        saw_data = False
        binary = False
        while True:
            chunk = source.readline(_READ_CHUNK)
            if not chunk:
                break
            saw_data = True
            total += len(chunk)
            keep = total <= max_line_bytes
            if not keep:
                oversized = True
            if isinstance(chunk, bytes):
                binary = True
                if keep:
                    byte_pieces.append(chunk)
                if chunk.endswith(b"\n"):
                    break
            elif keep:
                text_pieces.append(chunk)
                if chunk.endswith("\n"):
                    break
            elif chunk.endswith("\n"):
                break
        if not saw_data:
            return
        yield (b"".join(byte_pieces) if binary else "".join(text_pieces)), oversized


def serve_jsonl(
    source: TextIO | BinaryIO,
    destination: TextIO,
    *,
    state_root: Path,
    max_line_bytes: int = MAX_LINE_BYTES,
) -> int:
    """Serve one serialized client until clean EOF or an explicit shutdown request."""

    service = GatewayService(state_root)
    for line_number, (raw, oversized) in enumerate(_bounded_lines(source, max_line_bytes), 1):
        request_id = ""
        try:
            if oversized:
                raise ProtocolError("LINE_TOO_LARGE", f"maximum is {max_line_bytes} bytes")
            if isinstance(raw, bytes):
                try:
                    encoded = raw
                    line = raw.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    raise ProtocolError("INVALID_UTF8", "line is not UTF-8") from exc
            else:
                line = raw
                encoded = line.encode("utf-8")
            if len(encoded) > max_line_bytes:
                raise ProtocolError("LINE_TOO_LARGE", f"maximum is {max_line_bytes} bytes")
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProtocolError("MALFORMED_JSON", "line is not valid JSON") from exc
            if not isinstance(request, dict):
                raise ProtocolError("INVALID_ENVELOPE", "request must be an object")
            if isinstance(request.get("request_id", ""), str):
                request_id = str(request.get("request_id", ""))
            response = service.handle(request)
        except Exception as exc:
            detail = (
                redact_secrets(str(exc))[0]
                if isinstance(exc, ProtocolError)
                else "request could not be processed"
            )
            response = {
                "schema_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "ok": False,
                "error": {
                    "reason_code": getattr(exc, "reason_code", "INVALID_REQUEST"),
                    "line": line_number,
                    "detail": detail,
                },
            }
        destination.write(json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n")
        destination.flush()
        if service.shutdown_requested:
            break
    return 0


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("INVALID_FIELD", f"{label} must be an object")
    return value


def _string(value: Any, label: str, *, non_empty: bool = False) -> str:
    if not isinstance(value, str) or any(character in value for character in ("\x00", "\r")):
        raise ProtocolError("INVALID_FIELD", f"{label} must be a string")
    if non_empty and not value.strip():
        raise ProtocolError("INVALID_FIELD", f"{label} must be non-empty")
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ProtocolError("INVALID_FIELD", f"{label} must be a boolean")
    return value


def _validate_context(value: dict[str, Any]) -> None:
    allowed = {"session_id", "turn_id", "task_id", "platform", "model", "correlation"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ProtocolError("INVALID_FIELD", f"unknown context field: {unknown[0]}")
    for key in allowed - {"correlation"}:
        if key in value and value[key] is not None:
            _string(value[key], f"context.{key}")
    if "correlation" in value:
        _object(value["correlation"], "context.correlation")


def _validate_observation(value: dict[str, Any]) -> None:
    string_fields = {
        "content",
        "source_name",
        "source_kind",
        "source_integrity",
        "provenance_root",
        "kind",
        "subject",
        "target",
        "retention_mode",
        "pramana",
        "stakes",
        "belief_content",
        "perishability",
    }
    allowed = string_fields | {"correlation", "qualifiers", "validity", "derived_from"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ProtocolError("INVALID_FIELD", f"unknown observation field: {unknown[0]}")
    for key in string_fields:
        if key in value and value[key] is not None:
            _string(value[key], f"observation.{key}", non_empty=key in {"content", "source_name"})
    for key in ("correlation", "qualifiers", "validity"):
        if key in value and value[key] is not None:
            _object(value[key], f"observation.{key}")
    if "derived_from" in value and not (
        isinstance(value["derived_from"], (list, tuple))
        and all(isinstance(item, str) and item for item in value["derived_from"])
    ):
        raise ProtocolError("INVALID_FIELD", "observation.derived_from must be a string array")


def open_gateway_ledger(state_root: Path) -> BeliefLedger:
    """Open gateway-owned config and policy files from one explicit state root."""

    requested_root = state_root.expanduser().absolute()
    if requested_root.is_symlink():
        raise ProtocolError("STATE_ROOT_SYMLINK", "state root must not be a symbolic link")
    root = requested_root.resolve()
    config_path = root / "config.yaml"
    policy_path = root / "policies.json"
    if config_path.is_symlink() or policy_path.is_symlink():
        raise ProtocolError(
            "CONFIGURATION_SYMLINK",
            "gateway configuration and policy files must not be symbolic links",
        )
    manifest: dict[str, Any] | None = None
    if policy_path.is_file():
        value = json.loads(policy_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ProtocolError("INVALID_POLICY", "policies.json must contain an object")
        manifest = value
    config = CoreConfig(explicit_path=config_path) if config_path.is_file() else None
    return BeliefLedger.open(state_root=root, config=config, manifest=manifest)
