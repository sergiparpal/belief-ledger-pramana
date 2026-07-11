"""Exception-safe, JSON-only model tool handlers."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..models import Perishability, Pramana, Stakes, Status, VerificationMethod
from ..runtime import PluginRuntime


def build_tool_handlers(runtime: PluginRuntime) -> dict[str, Callable[..., str]]:
    def record_inference(args: dict[str, Any], **kwargs: Any) -> str:
        try:
            _validate_keys(
                args,
                {
                    "content",
                    "kind",
                    "premise_ids",
                    "warrant",
                    "qualifiers",
                    "perishability",
                    "stakes",
                    "explanandum",
                    "alternatives",
                    "similarity_basis",
                },
            )
            content = _required_string(args, "content", 500)
            kind = Pramana(_required_enum(args, "kind", {"anumana", "arthapatti", "upamana"}))
            premises = _string_list(args, "premise_ids", required=True, maximum=20)
            warrant = _required_string(args, "warrant", 1_000)
            qualifiers = _string_mapping(args.get("qualifiers", {}), "qualifiers")
            perishability = Perishability(
                _required_enum(args, "perishability", {"stable", "slow", "fast", "live"})
            )
            stakes = Stakes(str(args["stakes"])) if args.get("stakes") else None
            if stakes is not None and stakes.value not in {"low", "med", "high", "critical"}:
                raise ValueError("stakes is invalid")
            alternatives = _string_list(args, "alternatives", required=False, maximum=20)
            service = runtime.service(**kwargs)
            belief, event_ids = service.record_inference(
                content=content,
                pramana=kind,
                premise_ids=premises,
                warrant=warrant,
                qualifiers=qualifiers,
                perishability=perishability,
                stakes=stakes,
                alternatives=alternatives,
                explanandum=str(args.get("explanandum", "")),
                similarity_basis=str(args.get("similarity_basis", "")),
                **kwargs,
            )
            return _success(
                {
                    "belief_id": belief.id,
                    "status": belief.status.value,
                    "pramana": belief.pramana.value,
                },
                event_ids,
            )
        except Exception as exc:
            return _failure(exc)

    def query(args: dict[str, Any], **kwargs: Any) -> str:
        try:
            _validate_keys(args, {"query", "statuses", "types", "limit", "expand_graph"})
            text = _required_string(args, "query", 2_000)
            statuses = tuple(Status(item) for item in _string_list(args, "statuses", maximum=4))
            types = tuple(Pramana(item) for item in _string_list(args, "types", maximum=6))
            limit = _bounded_int(args.get("limit", 20), 1, 100, "limit")
            records = runtime.service(**kwargs).query(
                text,
                statuses=statuses,
                pramanas=types,
                limit=limit,
                expand_graph=bool(args.get("expand_graph", False)),
            )
            return _success({"beliefs": records, "count": len(records)}, ())
        except Exception as exc:
            return _failure(exc)

    def explain(args: dict[str, Any], **kwargs: Any) -> str:
        try:
            _validate_keys(args, {"belief_id", "depth"})
            belief_id = _required_string(args, "belief_id", 100)
            depth = _bounded_int(args.get("depth", 4), 1, 10, "depth")
            data = runtime.service(**kwargs).explain(belief_id, depth=depth)
            return _success(data, ())
        except Exception as exc:
            return _failure(exc)

    def request_verification(args: dict[str, Any], **kwargs: Any) -> str:
        try:
            _validate_keys(args, {"belief_id", "method"})
            belief_id = _required_string(args, "belief_id", 100)
            method = VerificationMethod(
                _required_enum(
                    args,
                    "method",
                    {"cross_source", "tool_recheck", "chain_audit", "human"},
                )
            )
            task, event_ids = runtime.service(**kwargs).request_verification(belief_id, method)
            return _success(
                {
                    "task_id": task.id,
                    "belief_id": task.belief_id,
                    "method": task.method.value,
                    "state": task.state,
                    "scheduled_is_confirmation": False,
                },
                event_ids,
            )
        except Exception as exc:
            return _failure(exc)

    return {
        "pramana_record_inference": record_inference,
        "pramana_query": query,
        "pramana_explain": explain,
        "pramana_request_verification": request_verification,
    }


def _success(data: Any, event_ids: tuple[str, ...] | list[str]) -> str:
    return json.dumps(
        {"ok": True, "data": data, "warnings": [], "event_ids": list(event_ids)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _failure(exc: Exception) -> str:
    code = _error_code(exc)
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": _safe_message(exc)}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _error_code(exc: Exception) -> str:
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "invalid_arguments"
    name = type(exc).__name__.casefold()
    if "budget" in name:
        return "budget_exhausted"
    if "unavailable" in name:
        return "runtime_unavailable"
    return "internal_error"


def _safe_message(exc: Exception) -> str:
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return str(exc)[:500]
    return f"belief-ledger operation failed ({type(exc).__name__})"


def _validate_keys(args: Any, allowed: set[str]) -> None:
    if not isinstance(args, dict):
        raise TypeError("arguments must be an object")
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ValueError("unknown arguments: " + ", ".join(unknown))


def _required_string(args: dict[str, Any], key: str, maximum: int) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    if len(value) > maximum:
        raise ValueError(f"{key} exceeds {maximum} characters")
    return value.strip()


def _required_enum(args: dict[str, Any], key: str, allowed: set[str]) -> str:
    value = _required_string(args, key, 100)
    if value not in allowed:
        raise ValueError(f"{key} must be one of: {', '.join(sorted(allowed))}")
    return value


def _string_list(
    args: dict[str, Any],
    key: str,
    *,
    required: bool = False,
    maximum: int,
) -> tuple[str, ...]:
    value = args.get(key)
    if value is None and not required:
        return ()
    if not isinstance(value, list) or (required and not value):
        raise ValueError(f"{key} must be a{' non-empty' if required else ''} string array")
    if len(value) > maximum or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{key} contains invalid values")
    result = tuple(item.strip() for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{key} must contain unique values")
    return result


def _string_mapping(value: Any, key: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(item_key, str) and isinstance(item, str) for item_key, item in value.items()
    ):
        raise ValueError(f"{key} must map strings to strings")
    return {item_key: item for item_key, item in value.items()}


def _bounded_int(value: Any, minimum: int, maximum: int, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{key} must be in [{minimum},{maximum}]")
    return value
