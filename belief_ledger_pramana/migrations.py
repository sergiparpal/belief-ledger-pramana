"""Compatibility re-exports for host-neutral schema migrations."""

from belief_ledger_core.migrations import (
    PROJECTION_HASH_ALGORITHM,
    PROJECTION_HASH_V1_ALGORITHM_VERSION,
    PROJECTION_HASH_V2_ALGORITHM_VERSION,
    PROJECTION_MANIFEST_V1,
    PROJECTION_MANIFEST_V2,
    PROJECTION_TABLES,
    SCHEMA_V1,
    SCHEMA_V2,
    SCHEMA_V3,
    SCHEMA_V4,
    SCHEMA_V5,
    SCHEMA_V6,
    MigrationResult,
    configure_connection,
    migrate,
)

__all__ = [
    "PROJECTION_HASH_ALGORITHM",
    "PROJECTION_HASH_V1_ALGORITHM_VERSION",
    "PROJECTION_HASH_V2_ALGORITHM_VERSION",
    "PROJECTION_MANIFEST_V1",
    "PROJECTION_MANIFEST_V2",
    "PROJECTION_TABLES",
    "SCHEMA_V1",
    "SCHEMA_V2",
    "SCHEMA_V3",
    "SCHEMA_V4",
    "SCHEMA_V5",
    "SCHEMA_V6",
    "MigrationResult",
    "configure_connection",
    "migrate",
]
