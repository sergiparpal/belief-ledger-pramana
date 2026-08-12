"""External anchoring of the hash chain.

Hash chaining plus the private HMAC key detects mutation by an attacker who cannot read or replace
the key. It does not detect an attacker who *can*: with the key in hand, a row can be edited and
everything after it re-chained, and `db verify-chain` passes on the result because the chain is
internally consistent again. Nothing inside the database can notice, because the attacker rewrote
everything inside the database.

An anchor is a copy of the chain root at a height, written somewhere the ledger cannot reach back
into. Re-chaining changes the root, so an anchor taken before the tampering disagrees with the
recomputed root at the same height, and the disagreement names the height.

What this defends against and what it does not is stated in `docs/threat-model.md` and must not be
overstated here: a file sink on the same host raises the cost of tampering, it does not prevent it.

See `docs/adr/0013-external-chain-anchoring.md`.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ..events import isoformat_utc, parse_datetime
from ..store import ChainState

ANCHOR_RECORD_VERSION = 1
GLOBAL_SCOPE = "global"


class AnchorError(RuntimeError):
    """A sink could not be used, or a record could not be read back."""


@dataclass(frozen=True, slots=True)
class AnchorRecord:
    ledger_id: str
    scope: str
    chain_height: int
    root_hash: str
    hash_algorithm: str
    created_at: datetime
    package_version: str
    record_version: int = ANCHOR_RECORD_VERSION

    def as_json(self) -> dict[str, Any]:
        return {
            "chain_height": self.chain_height,
            "created_at": isoformat_utc(self.created_at),
            "hash_algorithm": self.hash_algorithm,
            "ledger_id": self.ledger_id,
            "package_version": self.package_version,
            "record_version": self.record_version,
            "root_hash": self.root_hash,
            "scope": self.scope,
        }

    @classmethod
    def from_json(cls, value: Any) -> AnchorRecord:
        if not isinstance(value, dict):
            raise AnchorError("anchor record must be an object")
        try:
            return cls(
                ledger_id=str(value["ledger_id"]),
                scope=str(value["scope"]),
                chain_height=int(value["chain_height"]),
                root_hash=str(value["root_hash"]),
                hash_algorithm=str(value["hash_algorithm"]),
                created_at=parse_datetime(str(value["created_at"])),
                package_version=str(value["package_version"]),
                record_version=int(value.get("record_version", ANCHOR_RECORD_VERSION)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AnchorError(f"malformed anchor record: {exc}") from exc


@dataclass(frozen=True, slots=True)
class AnchorReceipt:
    sink: str
    chain_height: int
    root_hash: str


class ChainAnchorPort(Protocol):
    def publish(self, record: AnchorRecord) -> AnchorReceipt: ...

    def fetch(self, since_height: int = 0) -> Iterable[AnchorRecord]: ...


class FileAnchorSink:
    """Append-only JSONL at a path outside the ledger directory.

    The file is opened `O_APPEND` and created `0600`. It is never truncated, never rewritten, and
    never opened for writing any other way — `O_APPEND` means a concurrent writer cannot interleave
    a partial line either. Honest limits: an attacker with write access to this file can append or
    replace it, and one who can do that *and* rewrite the ledger defeats the whole scheme. The
    point is that those are two separate accesses rather than one.
    """

    def __init__(self, path: Path, *, ledger_directory: Path) -> None:
        self._path = _validated_sink_path(path, ledger_directory=ledger_directory)

    @property
    def path(self) -> Path:
        return self._path

    def publish(self, record: AnchorRecord) -> AnchorReceipt:
        line = json.dumps(record.as_json(), sort_keys=True, separators=(",", ":")) + "\n"
        descriptor = os.open(
            self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, stat.S_IRUSR | stat.S_IWUSR
        )
        try:
            os.write(descriptor, line.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return AnchorReceipt(str(self._path), record.chain_height, record.root_hash)

    def fetch(self, since_height: int = 0) -> Iterator[AnchorRecord]:
        if not self._path.is_file():
            return
        for number, line in enumerate(self._path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AnchorError(f"{self._path}:{number}: not valid JSON: {exc}") from exc
            record = AnchorRecord.from_json(payload)
            if record.chain_height >= since_height:
                yield record


@dataclass(frozen=True, slots=True)
class AnchorComparison:
    """One anchored height checked against the chain as it stands now."""

    chain_height: int
    anchored_root: str
    local_root: str | None
    status: str

    @property
    def ok(self) -> bool:
        return self.status == "match"

    def as_json(self) -> dict[str, Any]:
        return {
            "chain_height": self.chain_height,
            "anchored_root": self.anchored_root,
            "local_root": self.local_root,
            "status": self.status,
        }


def build_record(
    state: ChainState,
    *,
    ledger_id: str,
    scope: str,
    created_at: datetime,
    package_version: str,
) -> AnchorRecord:
    return AnchorRecord(
        ledger_id=ledger_id,
        scope=scope,
        chain_height=state.chain_height,
        root_hash=state.root_hash,
        hash_algorithm=state.hash_algorithm,
        created_at=created_at,
        package_version=package_version,
    )


def compare_against_anchors(
    records: Iterable[AnchorRecord],
    *,
    local_root_at: Any,
    current_height: int,
) -> list[AnchorComparison]:
    """Recompute the local root at every anchored height and compare.

    `local_root_at(height)` is normally `store.chain_state(up_to_height=height).root_hash`. It is
    injected so this stays a pure comparison that a test can drive without a database.

    Three outcomes, and the distinction matters when reading a failure:

    - `match` — the anchored root equals the recomputed one.
    - `mismatch` — the chain at that height is not the chain that was anchored. Tamper evidence.
    - `unreachable` — the local chain never reaches the anchored height, so history that was
      anchored is now missing. Also tamper evidence, and a different failure from a mismatch.
    """
    comparisons: list[AnchorComparison] = []
    for record in sorted(records, key=lambda item: item.chain_height):
        if record.chain_height > current_height:
            comparisons.append(
                AnchorComparison(record.chain_height, record.root_hash, None, "unreachable")
            )
            continue
        local = str(local_root_at(record.chain_height))
        status = "match" if local == record.root_hash else "mismatch"
        comparisons.append(AnchorComparison(record.chain_height, record.root_hash, local, status))
    return comparisons


def _validated_sink_path(path: Path, *, ledger_directory: Path) -> Path:
    """Refuse a sink the ledger's own attacker already owns.

    A sink inside the ledger directory is not an external anchor: whoever rewrote the database is
    already standing in the directory holding the evidence against them.
    """
    resolved = path.expanduser()
    directory = ledger_directory.expanduser()
    try:
        resolved_absolute = resolved.resolve()
        directory_absolute = directory.resolve()
    except OSError as exc:  # pragma: no cover - resolution failures are platform-specific
        raise AnchorError(f"unable to resolve anchor sink path {path}: {exc}") from exc
    if resolved_absolute == directory_absolute or directory_absolute in resolved_absolute.parents:
        raise AnchorError(
            f"anchor sink {resolved_absolute} must be outside the ledger directory "
            f"{directory_absolute}"
        )
    if resolved_absolute.is_symlink():
        raise AnchorError(f"anchor sink must not be a symbolic link: {resolved_absolute}")
    resolved_absolute.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return resolved_absolute
