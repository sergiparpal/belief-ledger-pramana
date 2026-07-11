# ADR 0003: Plugin-only enforcement limits

Status: accepted, 2026-07-11.

Hermes catches callback exceptions, final transformers compete by registration order, and a final
transform cannot universally restart a non-coding turn. Streaming clients may display provisional
tokens. The plugin therefore wraps policy callbacks, fails closed for HIGH/CRITICAL actions/output,
reports competing precedence, uses `pre_verify` only as a bounded coding optimization, and replaces
unresolved high-stakes answers with a safe report.

These limits are public diagnostics/README claims. The plugin does not monkey-patch the host or
misrepresent provisional streaming as an accepted-final guarantee.

