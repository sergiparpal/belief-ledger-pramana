"""Canonical provenance roots, fingerprints, and independence (R6)."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit

from ..engine.validity import normalize_content
from ..models import SourceKind
from .tool import redact_secrets, redacted_content_hash


def normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").casefold().rstrip(".")
    port = parsed.port
    netloc = host if port is None else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunsplit((parsed.scheme.casefold(), netloc, path, parsed.query, ""))


def registrable_domain(url_or_host: str) -> str:
    parsed = urlsplit(url_or_host if "://" in url_or_host else f"//{url_or_host}")
    host = (parsed.hostname or url_or_host).casefold().strip(".")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    common_second_level = {"co.uk", "com.au", "co.jp", "com.br", "co.in"}
    suffix2 = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix2 in common_second_level else suffix2


def provenance_root(
    kind: SourceKind,
    *,
    identity: str,
    publisher: str = "",
    origin: str = "",
    content: str | bytes | None = None,
) -> str:
    safe_identity = redact_secrets(identity)[0]
    safe_origin = redact_secrets(origin)[0]
    if kind is SourceKind.WEB:
        domain = registrable_domain(safe_identity)
        publisher_part = re.sub(r"\s+", "-", publisher.casefold().strip()) or "unknown"
        return f"web:{domain}:{publisher_part}"
    if kind is SourceKind.DOCUMENT:
        # A provenance root identifies the underlying document, not one
        # returned chunk/version.  Observation content is fingerprinted
        # separately so chunks from one file cannot corroborate one another as
        # independent sources.
        del content
        return f"document:{safe_origin or safe_identity or 'unknown'}"
    if kind is SourceKind.USER:
        return f"user:{safe_identity}"
    if kind is SourceKind.TOOL:
        return f"tool:{safe_identity}"
    if kind is SourceKind.MODEL:
        return f"model:{safe_identity}"
    if kind is SourceKind.LEDGER:
        return f"ledger:{safe_origin or safe_identity}"
    return f"retriever:{safe_identity}"


def fingerprint(content: str) -> str:
    return redacted_content_hash(normalize_content(content))


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(
        None, normalize_content(left), normalize_content(right), autojunk=False
    ).ratio()


def independent(
    left_root: str,
    right_root: str,
    left_content: str,
    right_content: str,
    *,
    near_duplicate_threshold: float,
) -> bool:
    if left_root == right_root:
        return False
    # Matching claims from distinct canonical roots corroborate. Mirrors and
    # chunks collapse earlier to the same publisher/document root.
    if normalize_content(left_content) == normalize_content(right_content):
        return True
    return similarity(left_content, right_content) >= near_duplicate_threshold
