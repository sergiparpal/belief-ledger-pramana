# Current implementation state: 1.0.0rc3

The workspace contains five synchronized local release-candidate distributions: core, gateway,
reference, MCP, and the backward-compatible Hermes adapter. Core is the canonical Python API;
gateway owns the neutral executable; reference is strict conformance evidence; MCP provides
inspection and an action proxy; Hermes retains its 1.x public surfaces.

Frozen v1 event fixtures and released historical documents remain unchanged. Current verification
is defined by `scripts/verify_stage.py all`, including workspace boundaries, product claims, generic
examples, five-wheel inspection, Twine metadata, and clean-install modes. The repository has not
published, signed, tagged, pushed, or opened a pull request for these candidates.
