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
