# Deterministic production deployment gate

This offline scenario shows one exact deployment request moving through evidence-backed policy
enforcement. It uses only local, versioned fixtures.

1. A production deployment is requested.
2. Missing current health evidence blocks it and recommends `health_probe`.
3. Green health evidence is ingested; exact human approval is still missing.
4. Approval bound to the tool, target, arguments, turn, and policy is recorded.
5. That exact action is allowed.
6. A later red health observation contradicts and retracts the green support.
7. A later deployment attempt is blocked.

Validate the contract with `python examples/deployment_gate/validate_fixtures.py`. Run the
host-neutral fixture with `--adapter fake`, or exercise real single-use dispatch through the strict
adapter with `--adapter reference --profile strict`.
