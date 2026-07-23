"""Strict, versioned schemas for host-owned structured completions."""

from __future__ import annotations

CLAIM_EXTRACTION_SCHEMA = {
    "$id": "belief-ledger.claim-extraction.v1",
    "type": "object",
    "additionalProperties": False,
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "maxItems": 24,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "content",
                    "pramana",
                    "span_start",
                    "span_end",
                    "exact_excerpt",
                    "qualifiers",
                    "domain",
                    "perishability",
                    "speech_act",
                    "source_identity",
                ],
                "properties": {
                    "content": {"type": "string", "minLength": 1, "maxLength": 500},
                    "pramana": {"enum": ["shabda", "anupalabdhi"]},
                    "span_start": {"type": "integer", "minimum": 0},
                    "span_end": {"type": "integer", "minimum": 1},
                    "exact_excerpt": {"type": "string", "minLength": 1},
                    "qualifiers": {"type": "object", "additionalProperties": {"type": "string"}},
                    "domain": {"type": "string", "minLength": 1, "maxLength": 80},
                    "perishability": {"enum": ["stable", "slow", "fast", "live"]},
                    "speech_act": {"enum": ["asserting", "quoting", "speculating", "instructing"]},
                    "source_identity": {"type": "string", "maxLength": 500},
                },
            },
        }
    },
}

CONTRADICTION_SCHEMA = {
    "$id": "belief-ledger.contradiction.v1",
    "type": "object",
    "additionalProperties": False,
    "required": ["outcome", "left_scope", "right_scope", "basis"],
    "properties": {
        "outcome": {"enum": ["rebut", "compatible", "scope_mismatch", "uncertain"]},
        "left_scope": {"type": "object", "additionalProperties": {"type": "string"}},
        "right_scope": {"type": "object", "additionalProperties": {"type": "string"}},
        "basis": {"type": "string", "minLength": 1, "maxLength": 500},
    },
}

CHAIN_AUDIT_SCHEMA = {
    "$id": "belief-ledger.chain-audit.v1",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "paksadharmata",
        "sapakse_sattvam",
        "vipakse_asattvam",
        "evidence_ids",
        "fallacies",
        "basis",
    ],
    "properties": {
        "paksadharmata": {"type": "boolean"},
        "sapakse_sattvam": {"type": "boolean"},
        "vipakse_asattvam": {"type": "boolean"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "fallacies": {
            "type": "array",
            "items": {"enum": ["savyabhicara", "viruddha", "satpratipaksa", "asiddha", "badhita"]},
            "maxItems": 5,
        },
        "basis": {"type": "string", "minLength": 1, "maxLength": 800},
    },
}

LINT_ENTAILMENT_SCHEMA = {
    "$id": "belief-ledger.lint-entailment.v1",
    "type": "object",
    "additionalProperties": False,
    "required": ["pairs"],
    "properties": {
        "pairs": {
            "type": "array",
            "maxItems": 30,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claim_index", "belief_id", "entailed", "basis"],
                "properties": {
                    "claim_index": {"type": "integer", "minimum": 0},
                    "belief_id": {"type": "string", "pattern": "^b_"},
                    "entailed": {"type": "boolean"},
                    "basis": {"type": "string", "maxLength": 300},
                },
            },
        }
    },
}

REWRITE_SCHEMA = {
    "$id": "belief-ledger.rewrite.v1",
    "type": "object",
    "additionalProperties": False,
    "required": ["response"],
    "properties": {"response": {"type": "string", "maxLength": 16000}},
}
