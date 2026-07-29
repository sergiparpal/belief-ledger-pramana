# Autonomous implementation rules

1. Read `docs/belief-ledger-pramana-spec-v0.1.md` and
   `docs/requirements-traceability.md` completely before implementation.
2. Work stage by stage and continue after each green machine-checkable gate.
3. Maintain `IMPLEMENTATION_STATE.md` with commands, exit codes, and artifacts.
4. Run narrow tests after changes and the complete gate before advancing.
5. Diagnose failures, add regression coverage where useful, and never weaken safety assertions.
6. Use offline scripted LLM fixtures unless live-provider spending is explicitly authorized.
7. Preserve the specification and requirements traceability; semantic deviations require an ADR.
8. Use temporary `HERMES_HOME` locations in development and tests.
9. Do not publish, push, release remotely, sign, or purge real data without authorization.
10. Keep permissions narrow and never bypass host security or approval mechanisms.
11. `main` rejects direct pushes: branch, open a pull request, and merge once `ci-complete` is green — no review approval is required, so this stays a one-person operation gated by CI.
12. Add every new CI job to `ci-complete`'s `needs:` list, and pin GitHub Actions to full commit SHAs with a trailing `# vX.Y.Z` comment, never to tags.

`ci-complete` (`.github/workflows/ci.yml`) is a single aggregating job that fails unless every
required job succeeded; `hermes-main-canary` stays out of its `needs:` because it is
`continue-on-error` by intent. The branch ruleset requires that one stable check name rather than
the individual matrix legs on purpose — a dropped Python version would otherwise become a required
check that never reports again and would block every merge permanently. A job missing from
`needs:` silently stops gating anything, which is the failure mode to watch for.

This posture — ruleset, `ci-complete`, SHA-pinned actions, `permissions: contents: read`, per-job
`timeout-minutes`, Dependabot, CodeQL — is shared across all six plugin repositories in this
account.
