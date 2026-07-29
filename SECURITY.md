# Security Policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub Security Advisories](https://github.com/sergiparpal/belief-ledger-pramana/security/advisories/new)
rather than opening a public issue.

Expect an initial response within 7 days. If a report is confirmed, the fix and the advisory
are published together.

## Supported versions

Only the latest release on `main` receives security fixes.

## Scope

This project gates real-world actions taken by AI agents and records the decisions in a
hash-chained ledger. It is security-relevant by construction, so the bar here is higher than
for the other plugins in this account.

The parts most worth scrutiny are therefore:

- **Ledger integrity** — anything that lets a recorded decision be altered, reordered, or
  removed without breaking the hash chain, or that lets the chain be recomputed to conceal a
  change.
- **Gate bypass** — any path that lets an action execute without the enforcement profile it
  was supposed to clear, including through the enforcement-profile conformance surface.
- **Approval handling** — anything that lets an approval be reused, widened beyond its exact
  scope, or replayed.
- **Retraction correctness** — failure to withdraw stale support when contradictory evidence
  arrives is a security defect here, not merely a bug.
- **Adapter boundaries** — the Hermes and reference adapters, and the dependency boundaries
  enforced by `scripts/check_dependency_boundaries.py`.

Out of scope: the substantive correctness of a policy an operator chooses to write, and any
behaviour of the underlying model.
