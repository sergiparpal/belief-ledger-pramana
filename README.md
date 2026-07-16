# Belief Ledger Pramāṇa for Hermes Agent

`belief-ledger-pramana` is an episode-scoped Hermes Agent plugin that types factual
support by pramāṇa, keeps a justification/defeat graph, injects a fresh bounded ledger
before every provider request, lints the accepted final answer, and gates effectful tool
calls on ledger-backed preconditions.

The durable source of truth is an append-only, hash-chained SQLite event log. Beliefs are
discrete (`IN`, `OUT`, `PENDING`, `QUARANTINED`); scalar confidence never decides defeat.

## Compatibility

Full conformance targets Hermes Agent `0.18.2` at audited commit
`3b2ef789dfcf92f5b7b18c08c59d25948e50857f`, manifest version 1, and Python
`>=3.11,<3.14`. The per-request guarantee requires Hermes' audited
`llm_request` middleware. Unsupported hosts are visibly diagnostics-only and never claim
strict enforcement. See [HERMES_COMPATIBILITY.md](HERMES_COMPATIBILITY.md).

## Install and enable

From Git/directory form:

```bash
hermes plugins install OWNER/REPO --enable
hermes belief-ledger doctor
```

For a project-local checkout:

```bash
mkdir -p .hermes/plugins
cp -R /path/to/belief-ledger-pramana .hermes/plugins/belief-ledger-pramana
export HERMES_ENABLE_PROJECT_PLUGINS=1
hermes plugins enable belief-ledger-pramana
```

For a built wheel:

```bash
python -m pip install dist/belief_ledger_pramana-*.whl
hermes plugins enable belief-ledger-pramana
```

Restart the CLI/gateway process after enabling. General plugins are opt-in;
`plugins.disabled` wins over `plugins.enabled`. `HERMES_SAFE_MODE=1` disables all plugin
discovery. `doctor` reports the loaded source/module, registered tools, middleware,
configuration, permissions, FTS5, hash chain, and competing output transformers.

## Runtime flow

For every turn the plugin ingests the original user message. Before every provider call it
compiles current relevant beliefs, open conflicts, and live structural retractions into an
ephemeral block appended to the active user item. Tool results remain byte-for-byte unchanged
in the Hermes transcript; the plugin separately records an immutable evidence object, a
PRATYAKṢA tool wrapper, and lazily validated content beliefs with the actual source.
Generic command stdout is only evidence that the command returned; it is never promoted into a
domain fact. Every terminal command string is treated as effectful because the plugin cannot
prove equivalent read-only semantics across host-selected shells.

Recognised structured observational APIs can satisfy gate prerequisites without trusting free-form
output: `stat_file`/`file_stat`/`stat_path` must return a matching JSON path with `exists: true`,
`list_directory`/`list_dir`/`list_files` must return that directory and an `entries` array, and
environment-identity APIs must return a non-empty environment identifier. These create
target-bound direct observations; arbitrary terminal text never does.

Final responses are linted under the episode stakes:

- LOW: deliver and optionally annotate grounding failures.
- MED: make at most one bounded rewrite, then mark or omit remaining unsupported clauses.
- HIGH/CRITICAL: replace unsupported output with a safe blocked-response report.

Before an effectful tool runs, a versioned action registry derives effective stakes and
checks preconditions. Missing support returns a deterministic block with a safe observation;
when explicit human confirmation is the only missing precondition, the audited Hermes
approval gate may be requested.
Textual confirmation is action-and-target-bound, expires quickly, and a negated statement never
authorizes an action. A qualifying confirmation is an affirmative, fresh user statement that
names both the action and its target; generic consent such as "yes" is not sufficient. Unknown
or ambiguous tools block in enforcing mode, and every terminal invocation is treated as
effectful regardless of the command text.

## Tools and commands

Model tools are deliberately narrow:

- `pramana_record_inference`: ANUMĀNA/ARTHĀPATTI/UPAMĀNA only, with IN premises and warrant.
- `pramana_query`: concise belief search without full evidence payloads.
- `pramana_explain`: provenance, validity, support, priority, defeat, and transitions.
- `pramana_request_verification`: persist/deduplicate a bounded task; scheduling is not confirmation.

In-session commands:

```text
/ledger status
/ledger conflicts
/ledger retractions
/ledger belief <id>
/ledger stakes <low|med|high|critical>
/ledger export [jsonl|markdown]
```

Operator commands:

```text
hermes belief-ledger doctor
hermes belief-ledger config show|path|validate|init
hermes belief-ledger db status|migrate|verify-chain|replay
hermes belief-ledger episode list|show|export
hermes belief-ledger purge --episode EP_ID --confirm EP_ID
hermes belief-ledger evaluate --suite all --offline
```

## Configuration and data

On first successful use the packaged enforcing defaults are atomically copied to:

```text
$HERMES_HOME/belief-ledger-pramana/config.yaml
$HERMES_HOME/belief-ledger-pramana/ledger.sqlite3
$HERMES_HOME/belief-ledger-pramana/locks/ledger.integrity.key
```

Set `BELIEF_LEDGER_PRAMANA_CONFIG` for an explicit private configuration file beneath that
profile-local state directory. Unknown keys warn only in `observe`; they are errors in `enforce`.
One turn uses one immutable config snapshot. See [config.example.yaml](config.example.yaml) and
[docs/configuration.md](docs/configuration.md).

The integrity key is a generated, private 256-bit secret used to authenticate the event history.
It is not included in episode exports and must be retained with an encrypted database backup; a
database restored without its matching key cannot authenticate its existing events.

The default evidence mode stores a bounded, additionally redacted excerpt and a hash of the
redacted post-Hermes result. `hash_only` cannot promote claims needing citation spans; `full`
is explicit opt-in. Credentials, authorization headers, raw environment dumps, and Hermes auth
files are never intentionally persisted. Directories use `0700` and files `0600` on POSIX.

## Upgrade and uninstall

Before an upgrade, stop Hermes processes using the profile, run
`hermes belief-ledger db verify-chain`, and retain a checkpointed copy of the state directory if
your retention policy allows it. Upgrade a Git-installed plugin with
`hermes plugins update belief-ledger-pramana`; upgrade a wheel with
`python -m pip install --upgrade PATH_TO_NEW_WHEEL`. Restart Hermes, then run:

```bash
hermes belief-ledger doctor
hermes belief-ledger db replay
```

Forward schema migration creates a private pre-migration database backup when needed. Include the
matching `locks/ledger.integrity.key` whenever backing up or restoring the ledger; see
[docs/operations.md](docs/operations.md). To uninstall, first run `hermes plugins disable
belief-ledger-pramana`, then use `hermes plugins remove
belief-ledger-pramana` for a Git/directory install or `python -m pip uninstall
belief-ledger-pramana` for a wheel. Durable state is intentionally retained. Purging an episode
or deleting the state directory is a separate destructive retention decision; see
[docs/operations.md](docs/operations.md).

## Honest limitations

- Python plugins run in-process with Hermes privileges. Installation is a code-trust decision,
  not a sandbox boundary.
- This is not an anti-prompt-injection layer, probabilistic reasoner, knowledge graph, or
  long-term-memory backend.
- Hermes catches callback exceptions. Safety callbacks therefore have an explicit outer
  fail-closed boundary for HIGH/CRITICAL actions/output.
- Final transforms cannot restart arbitrary turns or force tools. Unresolved high-stakes output
  is replaced with a block report.
- Competing final transformers all see the original output and first non-empty replacement wins.
  Strict enforcement is claimed only when this plugin has precedence; `doctor` checks it.
- Streaming surfaces may display provisional tokens before transformed-response reconciliation.
  The hard guarantee applies to the accepted final response.
- Tool schemas have no universal stakes metadata. Unknown or ambiguous mutation tools block in
  enforcing mode until an operator adds an anchored policy.

## Development and offline gate

```bash
uv sync --extra dev
uv run python scripts/verify_stage.py all --hermes-checkout /path/to/pinned/hermes
```

Live model tests are opt-in and never part of the default gate. Offline suites A-D and the
collapse decision write a versioned JSON report. No remote publication, signing, or public
release is performed by the repository scripts.
