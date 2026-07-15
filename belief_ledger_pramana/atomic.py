"""Small, reusable primitives for securely replacing private text files."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path


def write_private_text_atomically(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Replace *path* atomically, including on deep Windows directory layouts."""

    parent = path.parent
    os.makedirs(_filesystem_path(parent), mode=0o700, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".tmp-",
        dir=_filesystem_path(parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _chmod_if_supported(temporary, mode)
        os.replace(_filesystem_path(temporary), _filesystem_path(path))
        _chmod_if_supported(path, mode)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(_filesystem_path(temporary))


def _filesystem_path(path: Path) -> str:
    """Return a Windows extended-length path while preserving normal POSIX paths."""

    resolved = str(path.resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved[2:]
    return "\\\\?\\" + resolved


def _chmod_if_supported(path: Path, mode: int) -> None:
    with suppress(OSError):
        os.chmod(_filesystem_path(path), mode)
