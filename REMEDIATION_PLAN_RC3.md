# Remediation plan — post-v0.2.0 review findings

Status: ready for implementation
Author: prepared from a code review of PRs #11, #12, #13 (merged 2026-08-01)
Target: two pull requests against `main`, plus one optional release stage

---

## 0. How to use this document

This plan is self-contained. An implementing agent should be able to execute it without
reading the original review conversation.

Rules that govern this work, from `CLAUDE.md`:

1. `main` rejects direct pushes. Branch, open a pull request, wait for `ci-complete` to pass,
   then merge. No human review approval is required.
2. Never weaken a safety assertion. Every change here makes behaviour stricter or leaves it
   unchanged.
3. Semantic deviations from the specification require an ADR. Stage 1 adds one.
4. Use a temporary `HERMES_HOME` for all development and test runs.
5. Do not publish, push a tag, release remotely, sign, or purge real data without explicit
   authorization. Stage 8 is gated on an explicit answer for exactly this reason.
6. Any new CI job must be added to `ci-complete`'s `needs:` list. This plan adds no CI jobs;
   if you add one, you must also update `needs:`.

Conventions for the agent:

- **Line numbers in this document are indicative.** Locate code by the quoted snippet, not by
  line number — earlier stages shift later ones.
- Run every command from the repository root.
- Export a scratch `HERMES_HOME` once per shell session before running tests:

  ```bash
  export HERMES_HOME="$(mktemp -d)/hermes-home"
  ```

- `IMPLEMENTATION_STATE.md` is a **frozen `1.0.0rc1` historical baseline**. Do not edit it.
  Current-state updates belong in `docs/current-state-rc3.md`.
- Each stage ends with a machine-checkable gate. Continue to the next stage when the gate
  exits 0. Do not pause for human confirmation between stages.

---

## 1. Background

The repository is a host-neutral belief ledger and action-authorization layer. Relevant
architecture for this plan:

- `packages/core/src/belief_ledger_core/api.py` — the public `BeliefLedger` facade.
- `packages/core/src/belief_ledger_core/enforcement.py` — `EnforcementStore`, which owns
  approval receipts and single-use action permits.
- `packages/core/src/belief_ledger_core/store.py` — `LedgerStore`, the event store.
- `packages/gateway/src/belief_ledger_gateway/protocol.py` — newline-delimited JSON decision
  service.

Critically: **`EnforcementStore` and `LedgerStore` share one SQLite file.** `api.py` constructs
`EnforcementStore(self.store.database, ...)`. Therefore the `episodes`, `beliefs`, and
`conflicts` tables are reachable from inside an `EnforcementStore` transaction on the same
connection. PR #11 already relies on this for `_stored_supports_are_active` and
`_stored_conflicts_are_closed`. Stage 1 extends the same pattern.

### Findings addressed

| ID | Severity | Summary | Stage |
|---|---|---|---|
| F1 | High | Permits remain consumable against a finalized episode, and the condition is unrepairable | 1 |
| F2 | Medium | `MAX_LINE_BYTES` is enforced only after the whole line is in memory | 2 |
| F3 | Medium | Gateway idempotency is memory-only; no durable backstop | 4 |
| F4 | Low–Medium | Conflict predicate is broader than its inputs and internally inconsistent | 5 |
| F5 | Low | Permit token leaks through `to_primitive`; unguarded backfill; stray committed file | 6 |

---

## 2. Stage 0 — Setup and the single decision point

### 2.1 Branch

```bash
git fetch origin && git checkout -b fix/permit-lifecycle-hardening origin/main
```

### 2.2 Ask the operator three questions, then proceed uninterrupted

Use the `AskUserQuestion` tool **once**, with all three questions in a single call, before any
code is written. This is the only interruption in the plan. Record the answers in this file
under a new `## Answers` section at the end so later stages and future readers can see them.

**Question 1 — conflict predicate scope (drives Stage 5).**

`EnforcementStore._stored_conflicts_are_closed` currently rejects a permit if *any* conflict in
the episode is open, not only the conflicts named in the permit's `blocking_conflict_ids`, and
it permanently marks the permit `revoked`. Options:

- *(a)* Keep episode-wide (recommended): strictest, matches current shipped behaviour, no
  behaviour change. Stage 5 then only fixes the internal inconsistencies and documents the rule.
- *(b)* Scope to `blocking_conflict_ids` only: an unrelated conflict no longer kills valid
  permits, but this is a genuine relaxation and requires an ADR.

**Question 2 — gateway idempotency fingerprint (drives Stage 4).**

`GatewayService.handle` computes `fingerprint = canonical_json(request)`, which includes
`request_id`. A client retrying with a fresh `request_id` therefore receives
`IDEMPOTENCY_KEY_REUSED` instead of the cached response. This is deliberate and is pinned by
`tests/gateway/test_gateway.py` (the `{**start, "request_id": "other"}` case). Options:

- *(a)* Exclude `request_id` from the fingerprint (recommended): retries behave the way
  idempotency keys are normally expected to behave. Requires updating the existing test.
- *(b)* Keep the current behaviour and document it in `docs/gateway-protocol.md` instead.

**Question 3 — release (drives Stage 8).**

F1 is a fail-open in a boundary that the shipped `v0.2.0` release notes claim is closed. Options:

- *(a)* Prepare and publish a `v0.2.1` patch release after both PRs merge.
- *(b)* Merge the fixes only, leave them under `## Unreleased`, and decide on a release later
  (recommended if you want to review the merged result first).

If the operator does not answer within a reasonable window, default to **1(a), 2(b), 3(b)** —
the three no-behaviour-change options — note the defaults in the `## Answers` section, and
continue. Do not block the plan.

### 2.3 Gate

```bash
git rev-parse --abbrev-ref HEAD   # must print fix/permit-lifecycle-hardening
```

---

## 3. PR A — security fixes

### Stage 1 — F1: permits must not survive episode finalization

#### 3.1.1 The defect

`BeliefLedger.finalize_episode` in `packages/core/src/belief_ledger_core/api.py`:

```python
def finalize_episode(self, episode_id: str) -> EpisodeHandle:
    episode = self._episode(episode_id)
    if episode.state != "finalized":
        self.store.append_events(...)  # transaction 1
        self.enforcement.revoke_for_episode(episode_id)  # transaction 2
    return EpisodeHandle(1, episode_id, "finalized")
```

Two independent problems:

1. `BeliefLedger.consume_permission` never checks episode state. It is the only lifecycle
   method that omits `self._episode(..., require_active=True)`. Permit revocation is therefore
   the *only* thing preventing use of a permit on a closed episode.
2. If transaction 1 commits and transaction 2 fails, the episode is finalized with live
   permits. `EnforcementStore.revoke_for_episode` has no bounded busy-retry (unlike
   `LedgerStore._run_immediate_transaction`), so ordinary SQLite contention can raise
   `OperationalError` here. On a retry of `finalize_episode`, `episode.state` is now
   `"finalized"`, so the guard skips the whole block and returns success — **the revoke can
   never run**. The condition is permanent and the API reports success.

Confirmed empirically: a permit issued before finalization and consumed after it returns
`consumed=True, reason_code="CONSUMED"`.

#### 3.1.2 Change 1 — in-transaction episode check

In `packages/core/src/belief_ledger_core/enforcement.py`, add a helper next to
`_stored_supports_are_active`:

```python
def _stored_episode_is_active(self, connection: sqlite3.Connection, binding: ActionBinding) -> bool:
    if not _table_exists(connection, "episodes"):
        return True
    row = connection.execute(
        "SELECT state FROM episodes WHERE id=?", (binding.episode_id,)
    ).fetchone()
    return row is not None and str(row["state"]) == "active"
```

The `_table_exists` guard matches the existing convention for `beliefs` and `conflicts`: when
an `EnforcementStore` is opened against a standalone authorization database with no ledger
tables, the check is a no-op rather than a hard failure.

In `consume_action`, insert the check **after** the `_approval_reason` check and **before**
`support_ok = self._stored_supports_are_active(...)`:

```python
if not self._stored_episode_is_active(connection, stored):
    connection.execute(
        "UPDATE action_decisions SET state='revoked' WHERE token_digest=? AND state='issued'",
        (token_digest,),
    )
    return self._reject(
        connection, token_digest, "EPISODE_FINALIZED", event="ACTION_DECISION_REVOKED"
    )
```

`EPISODE_FINALIZED` is already the reason code used by `revoke_for_episode` and by
`BeliefLedger._episode`, so this introduces no new vocabulary.

#### 3.1.3 Change 2 — make finalization repairable

In `api.py`, move the revoke outside the state guard:

```python
def finalize_episode(self, episode_id: str) -> EpisodeHandle:
    episode = self._episode(episode_id)
    if episode.state != "finalized":
        self.store.append_events(...)  # unchanged
    # Always revoke. revoke_for_episode only touches state='issued' rows, so it is
    # idempotent, and running it unconditionally lets a retry repair a partial finalize.
    self.enforcement.revoke_for_episode(episode_id)
    return EpisodeHandle(1, episode_id, "finalized")
```

#### 3.1.4 Change 3 — bounded busy-retry for `revoke_for_episode`

Give `revoke_for_episode` the same bounded retry policy that `LedgerStore` uses for immediate
transactions, so ordinary contention does not surface as a caller-visible failure. Reuse the
existing deadline/`_is_busy` approach rather than inventing a new one. This is defence in
depth; Change 1 is what makes the system correct.

#### 3.1.5 Tests

Add to `tests/core/test_safety_regressions.py`, following the existing helpers `_manifest`,
`_action_ledger`, and `_context` in that file:

1. **`test_permit_is_rejected_after_episode_finalization`** — issue a permit, call
   `ledger.finalize_episode(...)` normally, assert `consume_permission` returns
   `consumed=False` with `reason_code == "EPISODE_FINALIZED"`, and assert
   `ledger.enforcement.action_state(permit.decision_id) == "revoked"`.

   `EnforcementStore.action_state` takes a token digest, and `ActionPermit.decision_id` *is*
   the token digest — `api.py` sets `decision_id = issued.token_digest`. Passing
   `permit.decision_id` is correct and matches the existing assertion in
   `test_consume_rechecks_support_inside_the_authorization_database_transaction`.

2. **`test_permit_is_rejected_when_finalize_revocation_did_not_run`** — the regression that
   proves Change 1 is load-bearing. Simulate a crash between the two transactions by appending
   the `EPISODE_FINALIZED` event directly through the store and *not* calling
   `revoke_for_episode`:

   ```python
   ledger.store.append_events(
       episode.id,
       [
           EventDraft(
               "EPISODE_FINALIZED",
               "episode",
               episode.id,
               {
                   "state": "finalized",
                   "episode_key": f"closed:{episode.id}",
                   "updated_at": ledger.dependencies.clock.now(),
               },
           )
       ],
   )
   ```

   Then assert `consume_permission` fails with `EPISODE_FINALIZED`. Before Change 1 this test
   fails with `consumed=True`; confirm that by stashing the change once.

3. **`test_finalize_is_idempotent_and_repairs_missing_revocation`** — after the simulated
   partial finalize above, call `ledger.finalize_episode(...)` again and assert the permit is
   now `revoked`, proving Change 2.

Also confirm no existing test asserts that a permit survives finalization. Search for
`finalize_episode` across `tests/` before changing behaviour.

#### 3.1.6 ADR

Add `docs/adr/0008-permit-lifecycle-fails-closed-on-finalized-episodes.md`, following the
structure of the existing ADRs in that directory. It must record:

- Context: permits were validated against support and conflict state inside the authorization
  transaction, but episode lifecycle state was enforced only by out-of-transaction bookkeeping.
- Decision: episode state joins support and conflict state as an in-transaction precondition of
  permit consumption; `EPISODE_FINALIZED` is returned and the permit is revoked.
- Consequences: strictly fail-closed; no caller that was previously correct is affected; a
  permit issued and consumed within one active episode is unchanged.

Add the corresponding row to `docs/requirements-traceability.md`, matching the existing table
format. Do not remove or renumber existing rows.

#### 3.1.7 Gate

```bash
uv run --no-sync python -m pytest -q tests/core/test_safety_regressions.py tests/core/gate/test_enforcement.py tests/conformance
```

Expected: all pass, including the three new tests.

---

### Stage 2 — F2: bound the gateway reader

#### 3.2.1 The defect

In `packages/gateway/src/belief_ledger_gateway/protocol.py`, `serve_jsonl` iterates
`for line_number, raw in enumerate(source, 1)`. Python materializes the entire line before the
`len(encoded) > max_line_bytes` check can run, so a client that sends unbounded data with no
newline exhausts memory before `LINE_TOO_LARGE` is ever raised. The limit is advisory.

#### 3.2.2 Change

Add a bounded reader that never accumulates past the limit but **still drains to the next
newline**, so the discarded remainder of an oversized line does not become garbage requests.
Resynchronization is the part a naive rewrite gets wrong.

```python
_READ_CHUNK = 65_536


def _bounded_lines(
    source: TextIO | BinaryIO, max_line_bytes: int
) -> Iterator[tuple[str | bytes, bool]]:
    """Yield (line, oversized) without ever buffering more than the limit."""

    while True:
        text_pieces: list[str] = []
        byte_pieces: list[bytes] = []
        total = 0
        oversized = False
        saw_data = False
        binary = False
        while True:
            chunk = source.readline(_READ_CHUNK)
            if not chunk:
                break
            saw_data = True
            total += len(chunk)
            keep = total <= max_line_bytes
            if not keep:
                oversized = True
            if isinstance(chunk, bytes):
                binary = True
                if keep:
                    byte_pieces.append(chunk)
                if chunk.endswith(b"\n"):
                    break
            elif keep:
                text_pieces.append(chunk)
                if chunk.endswith("\n"):
                    break
            elif chunk.endswith("\n"):
                break
        if not saw_data:
            return
        yield (b"".join(byte_pieces) if binary else "".join(text_pieces)), oversized
```

Note the two accumulation branches: they exist so `mypy --strict` can prove the join types.
Do not collapse them into one with a `str | bytes` joiner variable.

In `serve_jsonl`, iterate the generator and check the flag **in addition to** — not instead of
— the existing exact byte check:

```python
for line_number, (raw, oversized) in enumerate(_bounded_lines(source, max_line_bytes), 1):
    request_id = ""
    try:
        if oversized:
            raise ProtocolError("LINE_TOO_LARGE", f"maximum is {max_line_bytes} bytes")
        ...   # the existing decode / encode / length / json.loads body, unchanged
```

Both checks are required. `TextIO.readline(size)` counts **characters**, not bytes, so a
multi-byte line can pass the accumulation bound and still exceed `max_line_bytes` once encoded.
The existing `len(encoded) > max_line_bytes` check remains the exact test; the new flag is what
makes memory bounded. Worst-case buffering becomes 4×`max_line_bytes` for text sources, which
is bounded and therefore sufficient.

Add `Iterator` to the `collections.abc` imports.

#### 3.2.3 Tests

Add to `tests/gateway/test_gateway.py`:

1. **`test_oversized_line_is_rejected_and_the_stream_resynchronizes`** — feed an oversized line
   followed by a valid request on the next line. Assert the responses are exactly
   `["LINE_TOO_LARGE", <success>]` and that the valid request produced a real result. This is
   the case the current implementation handles by accident and that a bounded rewrite most
   easily breaks.

2. **`test_reader_does_not_buffer_beyond_the_limit`** — prove the bound directly rather than by
   allocating a huge input. Wrap a source in a small shim that records the largest `size`
   argument it receives and the total bytes served, then assert the reader stopped requesting
   data once past the limit. Alternatively, feed a source that would yield far more than
   `max_line_bytes` and assert the yielded payload length never exceeds the limit.

The existing `test_jsonl_is_bounded_deterministic_idempotent_and_observe_only` must keep
passing unchanged, including its `max_line_bytes=10` case and its
`serve_jsonl(io.BytesIO(b"\xff\n[]\n"), ...)` binary case. Both source types must work.

#### 3.2.4 Gate

```bash
uv run --no-sync python -m pytest -q tests/gateway
uv run --no-sync mypy packages/gateway/src
```

---

### Stage 3 — Ship PR A

#### 3.3.1 Documentation

- `CHANGELOG.md`: add entries under the existing `## Unreleased` heading. Match the surrounding
  voice — terse, declarative, one line per change. Cover the finalized-episode permit fix and
  the bounded gateway reader.
- `docs/gateway-protocol.md`: state that a line exceeding `max_line_bytes` is rejected with
  `LINE_TOO_LARGE` and the remainder discarded up to the next newline, and that the reader
  never buffers past the limit.
- `docs/current-state-rc3.md`: note the post-`v0.2.0` permit-lifecycle correction. Do **not**
  edit `IMPLEMENTATION_STATE.md`.

#### 3.3.2 Full gate

```bash
uv run --no-sync python scripts/verify_stage.py all
```

This runs `uv lock --check`, `ruff format --check`, `ruff check`, strict `mypy` over all five
source trees, the full non-live suite with branch coverage (`fail_under = 88`), dependency and
workspace boundary checks, product claims, both examples, the gateway demo, offline evaluations,
and policy validation. It must exit 0.

If coverage drops below 88, add tests rather than lowering the gate.

#### 3.3.3 Pull request

```bash
git add -A && git commit
git push -u origin fix/permit-lifecycle-hardening
gh pr create --base main --title "fix: close the finalized-episode permit gap and bound the gateway reader"
```

The PR body must follow the established format in this repo — `## Summary`, `## Why`,
`## Impact`, `## Validation` — and the Validation section must list real commands with real
results, not predicted ones.

End the commit message with:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

End the PR body with:

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

#### 3.3.4 Wait for CI, then merge

```bash
gh pr checks --watch
```

`ci-complete` must be green. Merge only then:

```bash
gh pr merge --squash --delete-branch
```

If a check fails, diagnose and fix on the branch. Do not merge past a red `ci-complete`, and do
not weaken a test to make it pass.

---

## 4. PR B — hardening and housekeeping

```bash
git fetch origin && git checkout -b fix/gateway-idempotency-and-cleanup origin/main
```

### Stage 4 — F3: durable gateway idempotency

#### 4.1.1 The defect

`GatewayService._idempotency` is an in-memory `OrderedDict` bounded to
`MAX_IDEMPOTENCY_ENTRIES = 1_024` with LRU eviction, and it does not survive process restart.
After eviction or restart, a replayed `evidence.ingest` re-executes and double-ingests. The
core store has a durable idempotency layer keyed on `correlation["idempotency_key"]`, but the
gateway never passes a key down to it, so there is no backstop.

#### 4.1.2 Change

Thread the protocol-level `idempotency_key` into the observation's correlation for the mutating
operation. `_dispatch` does not currently receive `idem`; pass it through from `handle`.

In the `evidence.ingest` branch, before `EvidenceObservation.normalize(**value)`:

```python
if idem is not None:
    correlation = dict(value.get("correlation") or {})
    correlation["idempotency_key"] = f"gateway:{idem}"
    value = {**value, "correlation": correlation}
```

The `gateway:` prefix prevents collision with a caller-supplied key in the same episode. The
store already returns the prior events on replay and raises on fingerprint mismatch, so both
eviction and restart stop causing duplicate ingestion.

Keep the in-memory cache as the fast path — it still serves non-mutating operations and avoids
re-entering the store.

Verify that `_validate_observation` accepts a `correlation` object (it does — `correlation` is
in its `allowed` set) and that injecting the key does not trip the unknown-field check.

#### 4.1.3 Change gated on Question 2

- If the answer was **2(a)**: exclude `request_id` from the fingerprint. Compute it over the
  request minus `request_id` — for example
  `canonical_json({k: v for k, v in request.items() if k != "request_id"})`. Update the
  existing `test_jsonl_is_bounded_deterministic_idempotent_and_observe_only` case that asserts
  `responses[3]["error"]["reason_code"] == "IDEMPOTENCY_KEY_REUSED"`: with this change,
  `{**start, "request_id": "other"}` must now return the **cached** response rather than an
  error. Add a separate case proving a genuinely different payload under the same key still
  raises `IDEMPOTENCY_KEY_REUSED`.
- If the answer was **2(b)**: leave the fingerprint alone and document the behaviour in
  `docs/gateway-protocol.md` — clients must reuse `request_id` when retrying under an
  idempotency key.

#### 4.1.4 Tests

Add to `tests/gateway/test_gateway.py`:

1. **`test_evidence_ingest_is_idempotent_across_cache_eviction`** — ingest with an idempotency
   key, force eviction (either construct the service with a small cap or clear
   `service._idempotency`), replay the identical request, and assert the ledger contains one
   belief, not two.
2. **`test_evidence_ingest_is_idempotent_across_service_restart`** — build a second
   `GatewayService` over the same `state_root`, replay the request, assert no duplicate.

#### 4.1.5 Gate

```bash
uv run --no-sync python -m pytest -q tests/gateway tests/core/test_public_api.py
```

---

### Stage 5 — F4: conflict predicate

Implement according to the answer to Question 1.

#### 4.2.1 Fixes that apply under either answer

In `EnforcementStore._stored_conflicts_are_closed`:

1. **Unify the state predicate.** The first query uses `state='open'`; the second uses
   `state!='resolved'`. Only `open` and `resolved` are ever written (see
   `projections.py::_conflict_opened` and `_apply_conflict_resolved`), so they agree today and
   would silently diverge the moment a third state is introduced. Make both `state='open'`.
2. **Add the missing episode filter** to the `blocking_conflict_ids` query. It currently reads
   `WHERE id IN (...) AND state!='resolved'` with no episode scope, so a conflict row that was
   purged or belongs to another episode counts as closed. Add `episode_id=?` bound to
   `binding.episode_id`.

#### 4.2.2 If the answer was 1(a) — keep episode-wide

No behavioural change. Add a comment above the first query stating that the episode-wide check
is deliberate and stricter than `blocking_conflict_ids`, and document the rule in
`docs/gateway-protocol.md` or `docs/adapter-authoring.md` — whichever already describes permit
preconditions. Add a test asserting an unrelated open conflict in the same episode blocks
consumption, so the intent is pinned rather than incidental.

#### 4.2.3 If the answer was 1(b) — scope to the binding

This is a relaxation and requires `docs/adr/0009-conflict-scope-for-permit-consumption.md`
plus a `docs/requirements-traceability.md` row. Replace the episode-wide query with a check
limited to `binding.blocking_conflict_ids`. Add a test proving an unrelated open conflict no
longer blocks consumption **and** a test proving a named blocking conflict still does.

#### 4.2.4 Gate

```bash
uv run --no-sync python -m pytest -q tests/core/gate/test_enforcement.py tests/core/test_safety_regressions.py
```

---

### Stage 6 — F5: housekeeping

Four independent changes. Each is small; keep them as separate commits.

#### 4.3.1 Stop `to_primitive` from serializing the permit token

`ActionPermit._raw_token` is declared `field(repr=False)`, which protects logs, but
`to_primitive` in `packages/core/src/belief_ledger_core/events.py` iterates
`dataclasses.fields(value)` and would emit it. No current call site serializes an
`ActionPermit` or `ActionAuthorization`, so this is a latent leak rather than an active one —
the "opaque permit" guarantee currently rests on convention.

Make it structural: skip underscore-prefixed field names in `to_primitive`'s dataclass branch.

```python
if dataclasses.is_dataclass(value):
    return {
        field.name: to_primitive(getattr(value, field.name))
        for field in dataclasses.fields(value)
        if not field.name.startswith("_")
    }
```

This is safe, and it has been verified rather than assumed. `to_primitive` feeds
`canonical_json`, which feeds event hashing, so a blanket exclusion would be dangerous if any
hashed record carried an underscore-prefixed field. Two candidates exist in the codebase:

- `ActionPermit._raw_token` (`api_types.py`) — the field we want excluded.
- `ToolPolicyManifest._sealed` (`manifest.py`) — **not** affected. `ToolPolicyManifest` is a
  plain class with a manual `__slots__` and `object.__setattr__` initializer, not a dataclass,
  so `dataclasses.is_dataclass()` is False and this branch never sees it. It serializes through
  its own `as_dict()`.

`ActionPermit._raw_token` is therefore the only dataclass field the exclusion changes. Re-run
the search before committing to confirm nothing new has landed:

```bash
grep -rn "    _[a-z_]*:" --include=*.py packages/*/src/ | grep -v "def \|#"
```

If a hashed record ever acquires such a field, switch to a narrower opt-out on `ActionPermit`.

Add a test asserting `to_primitive(authorization)` contains no substring of the raw token.

#### 4.3.2 Guard the decision-index backfill

`EnforcementStore._backfill_decision_indexes` runs on **every** `EnforcementStore.__init__`,
full-scanning `action_decisions` and issuing `INSERT OR IGNORE` per row, even on databases
that never needed it. Gate it on the schema version: bump
`enforcement_schema_migrations` to version 2, run the backfill only when the stored version is
below 2, and record version 2 afterwards. Keep the `_SCHEMA` `CREATE TABLE IF NOT EXISTS`
statements as they are so fresh databases are unaffected.

Add a test that opens an `EnforcementStore` twice over the same path and asserts the backfill
query runs only on the first open.

#### 4.3.3 Remove the stray committed state file

`.kg-ground-audit.jsonl.ckpt` is a local runtime checkpoint (mode 0600, dated 2026-07-30) that
was committed in PR #11. `.kg-reconcile-state.json` sits untracked beside it.

```bash
git rm --cached .kg-ground-audit.jsonl.ckpt
```

Add to `.gitignore`:

```
.kg-*
```

#### 4.3.4 Pin enforcement schema parity

The enforcement schema is defined twice — in `packages/core/src/belief_ledger_core/migrations.py`
and in `enforcement.py::_SCHEMA`. `LedgerStore.purge_episode` depends on the `migrations.py`
copy, because it builds a replacement database via `LedgerStore(...)` without ever constructing
an `EnforcementStore`. They are byte-compatible today; divergence would be silent and would
break purge.

Do **not** deduplicate — that refactor is larger than the risk justifies. Instead add a test in
`tests/core/` that creates one database through each path and asserts the resulting
`sqlite_master` entries for `enforcement_events`, `action_decisions`,
`action_decision_supports`, and `action_decision_episodes` are identical.

#### 4.3.5 Gate

```bash
uv run --no-sync python -m pytest -q tests/core tests/unit
git status --porcelain   # .kg-ground-audit.jsonl.ckpt must no longer be tracked
```

---

### Stage 7 — Ship PR B

Same procedure as Stage 3:

1. Update `CHANGELOG.md` under `## Unreleased`, and `docs/current-state-rc3.md`.
2. Run the full gate:

   ```bash
   uv run --no-sync python scripts/verify_stage.py all
   ```

3. Commit, push, `gh pr create` with the `## Summary` / `## Why` / `## Impact` /
   `## Validation` body, same co-author and footer trailers.
4. `gh pr checks --watch`, confirm `ci-complete` is green, then `gh pr merge --squash
   --delete-branch`.

---

## 5. Stage 8 — Optional release (gated on Question 3)

**Only execute this stage if the answer to Question 3 was 3(a).** `CLAUDE.md` rule 9 forbids
releasing without authorization; the answer to Question 3 is that authorization. If the answer
was 3(b), stop after Stage 7 and report that the fixes are merged and unreleased.

If authorized:

1. Branch `release/v0.2.1` from the updated `main`.
2. Move the `## Unreleased` entries into a `## v0.2.1 / 1.0.0rc4 - <date>` section, following
   the exact format of the `## v0.2.0 / 1.0.0rc3` section. Confirm whether the workspace
   package version should advance to `1.0.0rc4` — the repository keeps GitHub tags and Python
   RC versions synchronized but distinct, and `scripts/check_product_claims.py` plus
   `tests/contract/test_workspace_packages.py` enforce consistency. If versions advance, update
   all five distributions and the CI assertion in `.github/workflows/ci.yml` that pins
   `belief_ledger_core.__version__`.
3. Add a `RELEASE_NOTES.md` section. State plainly that `v0.2.0` shipped with a
   finalized-episode permit gap and that `v0.2.1` closes it — operators need to know whether
   they were exposed.
4. Full gate, PR, `ci-complete` green, merge.
5. Tag and publish the GitHub source release only after the merge, matching the existing
   release convention (generated source archives; no registry publication, no built-distribution
   upload, no signing).

---

## 6. Definition of done

- [ ] Both PRs merged to `main` with `ci-complete` green.
- [ ] A permit issued before `finalize_episode` is rejected with `EPISODE_FINALIZED` and
      revoked, including when `revoke_for_episode` never ran.
- [ ] `finalize_episode` repairs a partial finalize on retry.
- [ ] `serve_jsonl` never buffers past `max_line_bytes` and resynchronizes after an oversized
      line.
- [ ] `evidence.ingest` is idempotent across cache eviction and process restart.
- [ ] `_stored_conflicts_are_closed` uses one consistent state predicate and is episode-scoped
      in both queries.
- [ ] `to_primitive` cannot emit `_raw_token`.
- [ ] The decision-index backfill is version-guarded.
- [ ] `.kg-ground-audit.jsonl.ckpt` is untracked and ignored.
- [ ] A test pins enforcement schema parity between `migrations.py` and `enforcement.py`.
- [ ] Coverage remains at or above 88% branch coverage; no assertion was weakened to pass.
- [ ] `IMPLEMENTATION_STATE.md` is unmodified.

---

## 7. Risks and rollback

**Behavioural risk.** Stage 1 makes permit consumption strictly stricter. A caller that
depended on consuming a permit after finalizing its episode will now receive
`EPISODE_FINALIZED`. That pattern was never valid, and PR #11's own release notes claim it was
already blocked, so no supported caller should be affected. Search `tests/`, `examples/`, and
`packages/reference/` for any consume-after-finalize sequence before merging.

**Upgrade risk carried over from PR #11 (not introduced here).** `projections.py` now calls
`_require_episode_exists` and per-entity episode checks on every event, and these run during
`replay()`. Any pre-RC3 database containing a cross-episode reference will now fail replay hard
with no migration path. This plan does not change that, but it is worth validating against a
real `v0.1.x` database before any further release. If it reproduces, it needs its own issue and
a migration path — do not fold it into these PRs.

**Rollback.** Each stage is a separate commit and each finding is independent. Reverting the
Stage 1 commits restores the previous — vulnerable — behaviour without affecting Stages 2–6.
Neither PR changes any on-disk schema in a backward-incompatible way: Stage 6 bumps
`enforcement_schema_migrations` to 2, which older code ignores because it reads with
`INSERT OR IGNORE` semantics and creates tables conditionally.

---

## Answers

Answered by the operator on 2026-08-03 via the Stage 0 `AskUserQuestion` call. These are the
operator's explicit choices, not the documented defaults.

- **Q1 conflict predicate scope:** **1(a) — keep episode-wide.** No behavioural change. Stage 5
  unifies the state predicate on `state='open'`, adds the missing `episode_id` filter to the
  `blocking_conflict_ids` query, documents that the episode-wide check is deliberate and
  stricter than `blocking_conflict_ids`, and pins the intent with a test. No ADR required.
- **Q2 idempotency fingerprint:** **2(a) — exclude `request_id` from the fingerprint.** A retry
  under the same idempotency key with a fresh `request_id` returns the cached response instead
  of `IDEMPOTENCY_KEY_REUSED`. The existing pinned case in
  `test_jsonl_is_bounded_deterministic_idempotent_and_observe_only` is updated accordingly, and
  a new case proves a genuinely different payload under the same key still raises
  `IDEMPOTENCY_KEY_REUSED`.
- **Q3 release:** **3(b) — no release.** Stop after Stage 7. The fixes land on `main` under
  `## Unreleased`. Stage 8 is not executed; `CLAUDE.md` rule 9 authorization was not granted.
