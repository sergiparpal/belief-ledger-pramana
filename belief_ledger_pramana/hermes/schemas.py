"""Specific model-tool schemas understood by Hermes' tool registry."""

from __future__ import annotations

RECORD_INFERENCE_SCHEMA = {
    "name": "pramana_record_inference",
    "description": "Record one atomic derived belief when explicit IN premise IDs and a warrant are already known.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["content", "kind", "premise_ids", "warrant", "qualifiers", "perishability"],
        "properties": {
            "content": {"type": "string", "minLength": 1, "maxLength": 500},
            "kind": {"type": "string", "enum": ["anumana", "arthapatti", "upamana"]},
            "premise_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "uniqueItems": True,
                "items": {"type": "string", "pattern": "^b_[A-Za-z0-9_-]{16,}$"},
            },
            "warrant": {"type": "string", "minLength": 1, "maxLength": 1000},
            "qualifiers": {
                "type": "object",
                "maxProperties": 10,
                "additionalProperties": {"type": "string", "maxLength": 300},
            },
            "perishability": {"type": "string", "enum": ["stable", "slow", "fast", "live"]},
            "stakes": {"type": "string", "enum": ["low", "med", "high", "critical"]},
            "explanandum": {"type": "string", "pattern": "^b_[A-Za-z0-9_-]{16,}$"},
            "alternatives": {
                "type": "array",
                "maxItems": 20,
                "items": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            "similarity_basis": {"type": "string", "maxLength": 1000},
        },
    },
}

QUERY_SCHEMA = {
    "name": "pramana_query",
    "description": "Query concise active ledger records when a needed fact or premise may already exist.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 2000},
            "statuses": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "enum": ["in", "out", "pending", "quarantined"]},
            },
            "types": {
                "type": "array",
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "enum": [
                        "pratyaksha",
                        "shabda",
                        "anumana",
                        "arthapatti",
                        "upamana",
                        "anupalabdhi",
                    ],
                },
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "expand_graph": {"type": "boolean"},
        },
    },
}

EXPLAIN_SCHEMA = {
    "name": "pramana_explain",
    "description": "Explain provenance, live support, priority, defeat, and transitions for one known belief ID.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["belief_id"],
        "properties": {
            "belief_id": {"type": "string", "pattern": "^b_[A-Za-z0-9_-]{16,}$"},
            "depth": {"type": "integer", "minimum": 1, "maximum": 10},
        },
    },
}

REQUEST_VERIFICATION_SCHEMA = {
    "name": "pramana_request_verification",
    "description": "Create or deduplicate a bounded verification task for a PENDING or action-relevant belief.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["belief_id", "method"],
        "properties": {
            "belief_id": {"type": "string", "pattern": "^b_[A-Za-z0-9_-]{16,}$"},
            "method": {
                "type": "string",
                "enum": ["cross_source", "tool_recheck", "chain_audit", "human"],
            },
        },
    },
}
