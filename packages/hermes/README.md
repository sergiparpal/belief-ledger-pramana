# Hermes workspace member

The backward-compatible Hermes distribution is the workspace root rather than a nested build root.
This preserves full-repository `hermes plugins install` and directory/Git discovery for the 1.x
line. Its import package is `belief_ledger_pramana`; deterministic post-baseline behavior lives in
the sibling `packages/core` distribution.
