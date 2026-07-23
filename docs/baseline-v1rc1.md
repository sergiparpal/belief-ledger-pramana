# 1.0.0rc1 compatibility baseline

Status: frozen on 2026-07-22 from the completed local baseline described in
`IMPLEMENTATION_STATE.md`.

This snapshot is the compatibility reference for the post-1.0.0rc1 rearchitecture. It records
repository facts, not a new release claim.

| Surface | Frozen value | Source of truth |
|---|---|---|
| Hermes distribution/import | `belief-ledger-pramana` / `belief_ledger_pramana` | `pyproject.toml` |
| Baseline version | `1.0.0rc1` | `pyproject.toml`, `plugin.yaml` |
| Next synchronized workspace version | `1.0.0rc2` | ADR 0005 and package metadata |
| Plugin entry point | `belief-ledger-pramana = belief_ledger_pramana.plugin` | `pyproject.toml` |
| Hermes dependency | exactly `hermes-agent==0.18.2` | `pyproject.toml` |
| Audited Hermes commit | `3b2ef789dfcf92f5b7b18c08c59d25948e50857f` | `compatibility.py`, contract tests |
| Python | `>=3.11,<3.14` | `pyproject.toml` |
| Event envelope | schema version 1, SHA-256 canonical JSON chain | `events.py`, `store.py` |
| Database migrations | `0001_initial.sql`, `0002_llm_reservations.sql`; runtime schema 2 | package data, `migrations.py` |
| State root | `$HERMES_HOME/belief-ledger-pramana` | `config.py`, configuration tests |
| Config precedence | explicit argument; `BELIEF_LEDGER_PRAMANA_CONFIG`; profile-local config; packaged defaults | `config.py`, `test_config.py` |
| Database default | `<state-root>/ledger.sqlite3` | `config.py` |
| Evidence/export/lock roots | `<state-root>/evidence`, `exports`, `locks` | `config.py` |

The Hermes registration surface comprises four tools (`pramana_record_inference`,
`pramana_query`, `pramana_explain`, and `pramana_request_verification`), the `/ledger` slash
command, the `belief-ledger` operator command, `llm_request` middleware, and the 13 hooks listed
in `plugin.yaml`. The operator subcommands frozen by the baseline are `doctor`, `config`, `db`,
`episode`, `purge`, and `evaluate`. The real-manager contract tests cover both entry-point and
full-directory discovery.

The v1 projection hash is `sha256-canonical-json`, algorithm version 1, over the exact ordered
table/column manifest `PROJECTION_MANIFEST_V1` in `migrations.py`. New projection tables are
excluded from that manifest. The v2 hash has its own ordered manifest and algorithm version, so an
empty new projection table cannot redefine v1.

The synchronized post-baseline packages are versioned `1.0.0rc2`: `belief-ledger-core`,
`belief-ledger-pramana`, and `belief-ledger-reference`. Adapter distributions depend on exactly
`belief-ledger-core==1.0.0rc2` for this candidate. Workspace source overrides are development-only
and must not appear in wheel metadata. Independent package versioning and a wider core constraint
are deferred until a separate compatibility policy exists.
