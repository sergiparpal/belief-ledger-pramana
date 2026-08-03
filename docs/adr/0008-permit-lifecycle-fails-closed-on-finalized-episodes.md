# ADR 0008: Permit lifecycle fails closed on finalized episodes

Status: accepted, 2026-08-03.

## Context

`EnforcementStore.consume_action` validated a permit against supporting-belief status and conflict
state inside the same authorization transaction that flips the decision row to `consumed`. Episode
lifecycle state was not part of that transaction. It was enforced only by out-of-transaction
bookkeeping: `BeliefLedger.finalize_episode` appended `EPISODE_FINALIZED` in one transaction and
then called `EnforcementStore.revoke_for_episode` in a second, and `BeliefLedger.consume_permission`
was the one lifecycle method that did not re-read episode state at all.

That made permit revocation the only thing standing between a closed episode and a live permit, and
it made the guarantee unrepairable. If the first transaction committed and the second did not — for
example under ordinary SQLite contention, which `revoke_for_episode` did not retry — the episode was
`finalized` while its permits were still `issued`. A retry of `finalize_episode` then observed
`state == "finalized"`, skipped the guarded block that contained the revoke, and returned success.
The permit stayed consumable for its full TTL and no subsequent call could close it.

## Decision

Episode state joins support state and conflict state as an in-transaction precondition of permit
consumption. `consume_action` reads the `episodes` row on the authorization connection — the two
stores share one SQLite file — and refuses any permit whose episode is not `active`. The refusal
returns the existing `EPISODE_FINALIZED` reason code, revokes the permit, and records
`ACTION_DECISION_REVOKED`, so the permit is closed permanently rather than merely refused once.

The check follows the convention already used for `beliefs` and `conflicts`: when the `episodes`
table is absent, because an `EnforcementStore` was opened against a standalone authorization
database with no ledger tables, the check is a no-op rather than a hard failure.

Two supporting changes make the surrounding lifecycle honest rather than lucky.
`finalize_episode` now calls `revoke_for_episode` unconditionally instead of inside the
`state != "finalized"` guard; the call only touches `state='issued'` rows, so it is idempotent and a
retry repairs a partial finalize. `revoke_for_episode` runs under the same bounded busy-retry policy
`LedgerStore` uses for immediate transactions, so contention does not produce the partial state in
the first place.

## Consequences

The change is strictly fail-closed. No caller that was previously correct is affected: a permit
issued and consumed inside one active episode behaves exactly as before, and a permit presented
after a completed finalize was already refused — it is revoked by that point, so the decision-state
check reports `TOKEN_REVOKED` before the episode check is reached. What changes is the case that
previously succeeded: a permit presented against an episode that is finalized but whose revocation
never ran is now refused with `EPISODE_FINALIZED` and revoked.

The episode check is deliberately placed after the decision-state, expiry, binding and approval
checks. Those refuse on cheaper, more specific evidence, and reordering them would change reason
codes that are already pinned by tests and documented for adapter authors.

Consuming a permit after finalizing its episode was never a supported sequence, and the `v0.2.0`
release notes already claimed the boundary was closed. This ADR records that the claim is now
enforced by the authorization transaction rather than by bookkeeping that could silently fail.
