# Release state: v0.2.0 / 1.0.0rc3

The workspace contains five synchronized local release-candidate distributions: core, gateway,
reference, MCP, and the backward-compatible Hermes adapter. Core is the canonical Python API;
gateway owns the neutral executable; reference is strict conformance evidence; MCP provides
inspection and an action proxy; Hermes retains its 1.x public surfaces.

Frozen v1 event fixtures and released historical documents remain unchanged. Current verification
is defined by `scripts/verify_stage.py all`, including workspace boundaries, product claims, generic
examples, five-wheel inspection, Twine metadata, and clean-install modes. GitHub release `v0.2.0`
publishes this repository state as generated source archives. The five Python distributions remain
unpublished to package registries; no built distribution is uploaded or signed by the release.

## Post-v0.2.0 corrections on `main`

These changes are merged and unreleased. They are listed under `## Unreleased` in `CHANGELOG.md`.

`v0.2.0` claimed permits were hardened against finalized-episode reuse. That claim rested on
out-of-transaction bookkeeping: `finalize_episode` revoked permits in a second transaction, and
`consume_permission` never re-read episode state. A finalize whose revocation did not run left the
episode `finalized` with live permits, and a retry skipped the revoke entirely because the episode
was already `finalized`, so the condition could not be repaired. Episode state is now checked inside
the authorization transaction alongside support and conflict state (ADR 0008), the revoke runs
unconditionally so a retry repairs a partial finalize, and `revoke_for_episode` retries on ordinary
SQLite contention. Operators running `v0.2.0` should treat the finalized-episode permit boundary as
enforced only by revocation.

The gateway JSONL reader now enforces `max_line_bytes` while reading rather than after the whole
line is in memory. See `docs/gateway-protocol.md` for the resynchronization behaviour.
