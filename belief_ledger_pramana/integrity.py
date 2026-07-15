"""Local key handling for authenticated ledger events."""

from __future__ import annotations

import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path


class IntegrityKeyError(RuntimeError):
    """Raised when the local HMAC key cannot be safely loaded."""


def load_or_create_integrity_key(path: Path) -> bytes:
    """Create a separate 256-bit key once, then reject unsafe key material."""

    path = path.absolute()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise IntegrityKeyError("ledger integrity key must not be a symbolic link")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    except OSError as exc:
        raise IntegrityKeyError(f"unable to create ledger integrity key: {exc}") from exc
    else:
        try:
            key = secrets.token_bytes(32)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            with suppress(OSError):
                path.unlink()
            raise
    try:
        _require_private_key_file(path)
        key = path.read_bytes()
    except OSError as exc:
        raise IntegrityKeyError(f"unable to read ledger integrity key: {exc}") from exc
    if len(key) != 32:
        raise IntegrityKeyError("ledger integrity key is invalid")
    return key


def _require_private_key_file(path: Path) -> None:
    if os.name == "nt":
        # The runtime validates the state-root ACL after opening the store.
        return
    metadata = path.stat()
    getuid = getattr(os, "getuid", None)
    if not callable(getuid):
        raise IntegrityKeyError("unable to verify current-user ownership on this platform")
    if metadata.st_uid != getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise IntegrityKeyError(
            "ledger integrity key must be owned by the current user and mode 0600"
        )
