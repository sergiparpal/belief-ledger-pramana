# ADR 0001: Hermes integration surface

Status: accepted, 2026-07-11.

Use audited public plugin hooks plus `llm_request` middleware. `pre_llm_call` ingests the original
user message; middleware recompiles/injects before every provider request; `pre_tool_call` gates;
`transform_tool_result` ingests and returns `None`; `transform_llm_output` enforces accepted final
text; `post_llm_call` records it. No Hermes monkey-patching is permitted.

This mapping realizes non-accumulative context after tool calls while preserving system prompt,
transcript, tool adjacency, credentials, and cache-routing fields. Older hook-only hosts are
explicit compatibility mode, never full conformance.

