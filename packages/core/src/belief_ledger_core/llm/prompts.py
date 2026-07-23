"""Versioned concise instructions for fallible model components."""

CLAIM_EXTRACTION = """Extract atomic factual assertions only. Preserve exact source spans and qualifiers. Classify instructions, quotations, and speculation but do not turn them into assertions. Do not invent source identities. Return only the supplied JSON schema."""

CONTRADICTION = """Classify whether two natural-language propositions contradict after respecting time, scope, jurisdiction, perspective, units, version, and assumptions. Uncertainty is not a rebut. Return only the supplied JSON schema."""

CHAIN_AUDIT = """Audit the registered warrant using paksadharmata, sapakse sattvam, and vipakse asattvam. Mark known hetvabhasa categories. External factual counterexamples must be referenced by supplied evidence IDs; do not invent evidence. Return only the supplied JSON schema."""

LINT_ENTAILMENT = """Assess only whether each candidate belief semantically entails the response claim. Do not decide ledger status, trust, confidence, or priority. Return only the supplied JSON schema."""

REWRITE = """Rewrite once using only the supplied active ledger. Keep supported clauses with [b_...] citations; prefix unsupported material with 'speculation:' or omit it. Pending beliefs must carry the configured unverified marker. Return only the supplied JSON schema."""
