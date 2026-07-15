"""Materialized projection updates derived solely from immutable events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from .events import canonical_json, content_hash
from .models import Event

ProjectionHandler = Callable[[sqlite3.Connection, Event], None]


def apply_event(connection: sqlite3.Connection, event: Event) -> None:
    """Apply one event to projections. The caller owns the transaction."""

    handler = _EVENT_HANDLERS.get(event.kind)
    if handler is not None:
        handler(connection, event)
    _advance_event_head(connection, event)


def _advance_event_head(connection: sqlite3.Connection, event: Event) -> None:
    connection.execute(
        "INSERT INTO event_heads(episode_id,seq,event_hash) VALUES (?,?,?) "
        "ON CONFLICT(episode_id) DO UPDATE SET seq=excluded.seq,event_hash=excluded.event_hash",
        (event.episode_id, event.seq, event.event_hash),
    )


def _apply_episode_created(connection: sqlite3.Connection, event: Event) -> None:
    _episode_created(connection, _record(event.payload.get("record")))


def _apply_episode_turn_started(connection: sqlite3.Connection, event: Event) -> None:
    connection.execute(
        "UPDATE episodes SET current_turn=?, updated_at=? WHERE id=?",
        (int(event.payload["current_turn"]), str(event.payload["updated_at"]), event.episode_id),
    )


def _apply_episode_stakes_changed(connection: sqlite3.Connection, event: Event) -> None:
    connection.execute(
        "UPDATE episodes SET default_stakes=?,updated_at=? WHERE id=?",
        (str(event.payload["to"]), event.timestamp.isoformat(), event.episode_id),
    )


def _apply_episode_state_changed(connection: sqlite3.Connection, event: Event) -> None:
    payload = event.payload
    connection.execute(
        "UPDATE episodes SET state=?, updated_at=?, episode_key=COALESCE(?,episode_key) WHERE id=?",
        (
            str(payload.get("state", "finalized")),
            str(payload["updated_at"]),
            str(payload["episode_key"]) if payload.get("episode_key") else None,
            event.episode_id,
        ),
    )


def _apply_source_registered(connection: sqlite3.Connection, event: Event) -> None:
    _source_registered(connection, _record(event.payload.get("record")))


def _apply_source_stats_updated(connection: sqlite3.Connection, event: Event) -> None:
    payload = event.payload
    connection.execute(
        "UPDATE sources SET stats_json=?, competence_json=? WHERE id=?",
        (
            canonical_json(payload["stats"]),
            canonical_json(payload["competence"]),
            event.aggregate_id,
        ),
    )


def _apply_source_stats_delta(connection: sqlite3.Connection, event: Event) -> None:
    row = connection.execute(
        "SELECT stats_json FROM sources WHERE id=?", (event.aggregate_id,)
    ).fetchone()
    if row is None:
        return
    existing = json.loads(str(row["stats_json"]))
    delta = event.payload.get("delta", {})
    updated = {
        key: max(0, int(existing.get(key, 0)) + int(delta.get(key, 0)))
        for key in ("confirmed", "defeated", "samples")
    }
    connection.execute(
        "UPDATE sources SET stats_json=? WHERE id=?", (canonical_json(updated), event.aggregate_id)
    )


def _apply_evidence_ingested(connection: sqlite3.Connection, event: Event) -> None:
    _evidence_ingested(connection, _record(event.payload.get("record")))


def _apply_belief_admitted(connection: sqlite3.Connection, event: Event) -> None:
    _belief_admitted(connection, _record(event.payload.get("record")))


def _apply_support_added(connection: sqlite3.Connection, event: Event) -> None:
    _support_added(connection, _record(event.payload.get("record")))


def _apply_support_activity_changed(connection: sqlite3.Connection, event: Event) -> None:
    connection.execute(
        "UPDATE ingestion_supports SET active=? WHERE id=?",
        (int(bool(event.payload["active"])), event.aggregate_id),
    )


def _apply_justification_added(connection: sqlite3.Connection, event: Event) -> None:
    _justification_added(connection, event.episode_id, _record(event.payload.get("record")))


def _apply_justification_audited(connection: sqlite3.Connection, event: Event) -> None:
    connection.execute(
        "UPDATE justifications SET audit_json=? WHERE id=?",
        (canonical_json(event.payload["audit"]), event.aggregate_id),
    )


def _apply_defeat_added(connection: sqlite3.Connection, event: Event) -> None:
    _defeat_added(connection, _record(event.payload.get("record")))


def _apply_defeat_activity_changed(connection: sqlite3.Connection, event: Event) -> None:
    connection.execute(
        "UPDATE defeats SET active=? WHERE id=?",
        (int(bool(event.payload["active"])), event.aggregate_id),
    )


def _apply_belief_status_changed(connection: sqlite3.Connection, event: Event) -> None:
    _belief_status_changed(connection, event.aggregate_id, str(event.payload["to"]))


def _apply_belief_admission_changed(connection: sqlite3.Connection, event: Event) -> None:
    connection.execute(
        "UPDATE beliefs SET admission_status=? WHERE id=?",
        (str(event.payload["to"]), event.aggregate_id),
    )


def _apply_belief_corroboration_changed(connection: sqlite3.Connection, event: Event) -> None:
    connection.execute(
        "UPDATE beliefs SET corroboration=? WHERE id=?",
        (int(event.payload["to"]), event.aggregate_id),
    )


def _apply_verification_created(connection: sqlite3.Connection, event: Event) -> None:
    _verification_created(connection, _record(event.payload.get("record")))


def _apply_verification_completed(connection: sqlite3.Connection, event: Event) -> None:
    connection.execute(
        "UPDATE verification_tasks SET result=?, state=? WHERE id=?",
        (
            event.payload.get("result"),
            str(event.payload.get("state", "completed")),
            event.aggregate_id,
        ),
    )


def _apply_conflict_opened(connection: sqlite3.Connection, event: Event) -> None:
    _conflict_opened(connection, _record(event.payload.get("record")))


def _apply_conflict_resolved(connection: sqlite3.Connection, event: Event) -> None:
    connection.execute("UPDATE conflicts SET state='resolved' WHERE id=?", (event.aggregate_id,))


def _apply_retraction_created(connection: sqlite3.Connection, event: Event) -> None:
    _retraction_created(connection, _record(event.payload.get("record")))


def _apply_retraction_state_changed(connection: sqlite3.Connection, event: Event) -> None:
    state = "acknowledged" if event.kind.endswith("ACKNOWLEDGED") else "expired"
    connection.execute(
        "UPDATE retraction_notices SET state=? WHERE id=?", (state, event.aggregate_id)
    )


def _apply_context_compiled(connection: sqlite3.Connection, event: Event) -> None:
    _context_compiled(connection, event)


def _apply_component_verdict_recorded(connection: sqlite3.Connection, event: Event) -> None:
    _component_verdict(connection, _record(event.payload.get("record")))


def _apply_llm_usage_recorded(connection: sqlite3.Connection, event: Event) -> None:
    _llm_usage(connection, _record(event.payload.get("record")))


def _apply_unpromoted_evidence_added(connection: sqlite3.Connection, event: Event) -> None:
    payload = event.payload
    connection.execute(
        "INSERT OR REPLACE INTO unpromoted_evidence(episode_id,evidence_id,source_profile,state,reason) VALUES (?,?,?,?,?)",
        (
            event.episode_id,
            str(payload["evidence_id"]),
            str(payload["source_profile"]),
            "open",
            str(payload.get("reason", "lazy_extraction")),
        ),
    )


def _apply_unpromoted_evidence_finished(connection: sqlite3.Connection, event: Event) -> None:
    payload = event.payload
    state = "resolved" if event.kind.endswith("RESOLVED") else "failed"
    connection.execute(
        "UPDATE unpromoted_evidence SET state=?, reason=? WHERE episode_id=? AND evidence_id=?",
        (state, str(payload.get("reason", "")), event.episode_id, str(payload["evidence_id"])),
    )


def _apply_lint_recorded(connection: sqlite3.Connection, event: Event) -> None:
    payload = event.payload
    connection.execute(
        "INSERT INTO lint_reports(event_id,episode_id,response_hash,passed,report_json) VALUES (?,?,?,?,?)",
        (
            event.id,
            event.episode_id,
            str(payload["response_hash"]),
            int(bool(payload["passed"])),
            canonical_json(payload["report"]),
        ),
    )


def _apply_gate_decided(connection: sqlite3.Connection, event: Event) -> None:
    payload = event.payload
    connection.execute(
        "INSERT INTO gate_decisions(event_id,episode_id,tool_name,args_hash,outcome,reason_code,detail_json) VALUES (?,?,?,?,?,?,?)",
        (
            event.id,
            event.episode_id,
            str(payload["tool_name"]),
            str(payload["args_hash"]),
            str(payload["outcome"]),
            str(payload["reason_code"]),
            canonical_json(payload.get("detail", {})),
        ),
    )


def _apply_assistant_response_recorded(connection: sqlite3.Connection, event: Event) -> None:
    payload = event.payload
    connection.execute(
        "INSERT INTO assistant_responses(event_id,episode_id,turn_id,content_hash,content) VALUES (?,?,?,?,?)",
        (
            event.id,
            event.episode_id,
            str(payload.get("turn_id", "")),
            str(payload["content_hash"]),
            str(payload["content"]),
        ),
    )


_EVENT_HANDLERS: dict[str, ProjectionHandler] = {
    "EPISODE_CREATED": _apply_episode_created,
    "EPISODE_TURN_STARTED": _apply_episode_turn_started,
    "EPISODE_STAKES_CHANGED": _apply_episode_stakes_changed,
    "EPISODE_FINALIZED": _apply_episode_state_changed,
    "EPISODE_RESET": _apply_episode_state_changed,
    "SOURCE_REGISTERED": _apply_source_registered,
    "SOURCE_STATS_UPDATED": _apply_source_stats_updated,
    "SOURCE_STATS_DELTA": _apply_source_stats_delta,
    "EVIDENCE_INGESTED": _apply_evidence_ingested,
    "BELIEF_ADMITTED": _apply_belief_admitted,
    "INGESTION_SUPPORT_ADDED": _apply_support_added,
    "INGESTION_SUPPORT_ACTIVITY_CHANGED": _apply_support_activity_changed,
    "JUSTIFICATION_ADDED": _apply_justification_added,
    "JUSTIFICATION_AUDITED": _apply_justification_audited,
    "DEFEAT_ADDED": _apply_defeat_added,
    "DEFEAT_ACTIVITY_CHANGED": _apply_defeat_activity_changed,
    "BELIEF_STATUS_CHANGED": _apply_belief_status_changed,
    "BELIEF_ADMISSION_CHANGED": _apply_belief_admission_changed,
    "BELIEF_CORROBORATION_CHANGED": _apply_belief_corroboration_changed,
    "VERIFICATION_TASK_CREATED": _apply_verification_created,
    "VERIFICATION_TASK_COMPLETED": _apply_verification_completed,
    "CONFLICT_OPENED": _apply_conflict_opened,
    "CONFLICT_RESOLVED": _apply_conflict_resolved,
    "RETRACTION_CREATED": _apply_retraction_created,
    "RETRACTION_ACKNOWLEDGED": _apply_retraction_state_changed,
    "RETRACTION_EXPIRED": _apply_retraction_state_changed,
    "CONTEXT_COMPILED": _apply_context_compiled,
    "COMPONENT_VERDICT_RECORDED": _apply_component_verdict_recorded,
    "LLM_USAGE_RECORDED": _apply_llm_usage_recorded,
    "UNPROMOTED_EVIDENCE_ADDED": _apply_unpromoted_evidence_added,
    "UNPROMOTED_EVIDENCE_RESOLVED": _apply_unpromoted_evidence_finished,
    "UNPROMOTED_EVIDENCE_FAILED": _apply_unpromoted_evidence_finished,
    "LINT_RECORDED": _apply_lint_recorded,
    "GATE_DECIDED": _apply_gate_decided,
    "ASSISTANT_RESPONSE_RECORDED": _apply_assistant_response_recorded,
}


def _episode_created(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO episodes(id,episode_key,session_id,task_id,platform,model,default_stakes,current_turn,created_at,updated_at,compatibility_mode,llm_calls_used,input_tokens_used,output_tokens_used,state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            record["id"],
            record["key"],
            record["session_id"],
            record["task_id"],
            record["platform"],
            record["model"],
            record["default_stakes"],
            record["current_turn"],
            record["created_at"],
            record["updated_at"],
            record["compatibility_mode"],
            record.get("llm_calls_used", 0),
            record.get("input_tokens_used", 0),
            record.get("output_tokens_used", 0),
            record.get("state", "active"),
        ),
    )


def _source_registered(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO sources(id,episode_id,kind,integrity,name,root,competence_json,stats_json) VALUES (?,?,?,?,?,?,?,?)",
        (
            record["id"],
            record["episode_id"],
            record["kind"],
            record["integrity"],
            record["name"],
            record["root"],
            canonical_json(record.get("competence", {})),
            canonical_json(record.get("stats", {})),
        ),
    )


def _evidence_ingested(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO evidence(id,episode_id,kind,source_id,payload,content_hash,meta_json,observed_at,redacted) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            record["id"],
            record["episode_id"],
            record["kind"],
            record["source_id"],
            record.get("payload"),
            record["content_hash"],
            canonical_json(record.get("metadata", {})),
            record["observed_at"],
            int(bool(record.get("redacted", False))),
        ),
    )


def _belief_admitted(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    fingerprint = content_hash(str(record["normalized_content"]))
    connection.execute(
        "INSERT INTO beliefs(id,episode_id,content,normalized_content,content_fingerprint,pramana,source_id,qualifiers_json,perishability,observed_at,stakes,status,admission_status,domain,confidence,corroboration,validity_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            record["id"],
            record["episode_id"],
            record["content"],
            record["normalized_content"],
            fingerprint,
            record["pramana"],
            record["source_id"],
            canonical_json(record.get("qualifiers", {})),
            record["perishability"],
            record["observed_at"],
            record["stakes"],
            record["status"],
            record["admission_status"],
            record.get("domain", "general"),
            record.get("confidence"),
            int(record.get("corroboration", 0)),
            canonical_json(record.get("validity", {})),
        ),
    )
    for evidence_ref in record.get("evidence", []):
        connection.execute(
            "INSERT INTO belief_evidence(belief_id,evidence_id,span_json) VALUES (?,?,?)",
            (
                record["id"],
                evidence_ref["evidence_id"],
                canonical_json(evidence_ref["span"])
                if evidence_ref.get("span") is not None
                else None,
            ),
        )
    for justification in record.get("justifications", []):
        _justification_added(connection, str(record["episode_id"]), justification)
    source_row = connection.execute(
        "SELECT root FROM sources WHERE id=?", (record["source_id"],)
    ).fetchone()
    source_root = str(source_row[0]) if source_row else "unknown"
    connection.execute(
        "INSERT INTO source_roots(episode_id,belief_id,root,transport) VALUES (?,?,?,?)",
        (record["episode_id"], record["id"], source_root, record.get("transport")),
    )
    connection.execute(
        "INSERT INTO content_fingerprints(episode_id,belief_id,source_root,fingerprint) VALUES (?,?,?,?)",
        (record["episode_id"], record["id"], source_root, fingerprint),
    )
    _fts_replace(
        connection, record["id"], record["episode_id"], record["status"], record["content"]
    )


def _support_added(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO ingestion_supports(id,episode_id,belief_id,evidence_id,validity_json,active) VALUES (?,?,?,?,?,?)",
        (
            record["id"],
            record["episode_id"],
            record["belief_id"],
            record["evidence_id"],
            canonical_json(record.get("validity", {})),
            int(bool(record.get("active", True))),
        ),
    )


def _justification_added(
    connection: sqlite3.Connection, episode_id: str, record: dict[str, Any]
) -> None:
    connection.execute(
        "INSERT INTO justifications(id,episode_id,belief_id,warrant,audit_json,alternatives_json) VALUES (?,?,?,?,?,?)",
        (
            record["id"],
            episode_id,
            record["belief_id"],
            record["warrant"],
            canonical_json(record["audit"]) if record.get("audit") is not None else None,
            canonical_json(record.get("alternatives", [])),
        ),
    )
    for ordinal, premise in enumerate(record.get("premises", [])):
        connection.execute(
            "INSERT INTO justification_premises(justification_id,ordinal,premise_belief_id) VALUES (?,?,?)",
            (record["id"], ordinal, premise),
        )


def _defeat_added(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO defeats(id,episode_id,attacker,target,kind,basis,active) VALUES (?,?,?,?,?,?,?)",
        (
            record["id"],
            record["episode_id"],
            record["attacker"],
            record["target"],
            record["kind"],
            record["basis"],
            int(bool(record.get("active", False))),
        ),
    )


def _belief_status_changed(connection: sqlite3.Connection, belief_id: str, status: str) -> None:
    connection.execute("UPDATE beliefs SET status=? WHERE id=?", (status, belief_id))
    row = connection.execute(
        "SELECT episode_id,content FROM beliefs WHERE id=?", (belief_id,)
    ).fetchone()
    if row:
        _fts_replace(connection, belief_id, str(row[0]), status, str(row[1]))


def _verification_created(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO verification_tasks(id,episode_id,belief_id,method,k_required,budget,result,state) VALUES (?,?,?,?,?,?,?,?)",
        (
            record["id"],
            record["episode_id"],
            record["belief_id"],
            record["method"],
            record["k_required"],
            record["budget"],
            record.get("result"),
            record.get("state", "open"),
        ),
    )


def _conflict_opened(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO conflicts(id,episode_id,left_belief_id,right_belief_id,normalized_scope_json,verification_task_id,state) VALUES (?,?,?,?,?,?,?)",
        (
            record["id"],
            record["episode_id"],
            record["left_belief_id"],
            record["right_belief_id"],
            canonical_json(record.get("normalized_scope", {})),
            record["verification_task_id"],
            record.get("state", "open"),
        ),
    )


def _retraction_created(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO retraction_notices(id,episode_id,defeated_belief_id,cause,descendants_json,created_turn,ttl_turns,state) VALUES (?,?,?,?,?,?,?,?)",
        (
            record["id"],
            record["episode_id"],
            record["defeated_belief_id"],
            record["cause"],
            canonical_json(record.get("descendants", [])),
            record["created_turn"],
            record["ttl_turns"],
            record.get("state", "active"),
        ),
    )


def _context_compiled(connection: sqlite3.Connection, event: Event) -> None:
    for rendered in event.payload.get("rendered", []):
        connection.execute(
            "INSERT OR IGNORE INTO rendered_beliefs(episode_id,belief_id,request_id,turn_number,rendered_at) VALUES (?,?,?,?,?)",
            (
                event.episode_id,
                rendered["belief_id"],
                rendered["request_id"],
                rendered["turn_number"],
                rendered["rendered_at"],
            ),
        )


def _component_verdict(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO component_verdicts(id,episode_id,component,purpose,input_hash,outcome,belief_id,detail_json) VALUES (?,?,?,?,?,?,?,?)",
        (
            record["id"],
            record["episode_id"],
            record["component"],
            record["purpose"],
            record["input_hash"],
            record["outcome"],
            record.get("belief_id"),
            canonical_json(record.get("detail", {})),
        ),
    )


def _llm_usage(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO llm_usage(id,episode_id,purpose,provider,model,input_tokens,output_tokens,cost,latency_ms,turn_number,outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            record["id"],
            record["episode_id"],
            record["purpose"],
            record["provider"],
            record["model"],
            record["input_tokens"],
            record["output_tokens"],
            record.get("cost"),
            record["latency_ms"],
            record["turn_number"],
            record["outcome"],
        ),
    )
    connection.execute(
        "UPDATE episodes SET llm_calls_used=llm_calls_used+1,input_tokens_used=input_tokens_used+?,output_tokens_used=output_tokens_used+? WHERE id=?",
        (record["input_tokens"], record["output_tokens"], record["episode_id"]),
    )


def _fts_replace(
    connection: sqlite3.Connection, belief_id: str, episode_id: str, status: str, content: str
) -> None:
    try:
        connection.execute("DELETE FROM beliefs_fts WHERE belief_id=?", (belief_id,))
        connection.execute(
            "INSERT INTO beliefs_fts(belief_id,episode_id,status,content) VALUES (?,?,?,?)",
            (belief_id, episode_id, status, content),
        )
    except sqlite3.OperationalError:
        return


def _record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("event record payload must be a mapping")
    return value
