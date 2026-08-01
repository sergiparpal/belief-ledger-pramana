# Custom tool gate

This offline example registers a caller-defined CRM message tool, its JSON Schema, effect
classification, policy, and handler. It demonstrates block → evidence → approval → allow and
single execution → support retraction → block.

```console
uv run --no-sync python examples/custom_tool_gate/run.py --format json
```
