# Belief Ledger reference adapter

This package supplies a deterministic, in-process strict adapter for Belief Ledger Core. It owns
effectful dispatch and response delivery, consumes bound single-use decisions immediately before
handlers run, and exposes a versioned JSONL protocol for local integration and conformance tests.
