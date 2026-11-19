"""Atomic file I/O, hashing, and cross-platform path helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def compute_sha256(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def atomic_write_text(path: str | Path, content: str, encoding: str = "utf-8") -> Path:
    """Atomically write text to a target file via temporary file + replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
    return target


def atomic_write_json(path: str | Path, payload: BaseModel | dict[str, Any] | list[Any]) -> Path:
    """Atomically serialize a Pydantic model or JSON-compatible object to disk."""
    if isinstance(payload, BaseModel):
        text = payload.model_dump_json(indent=2)
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    return atomic_write_text(path, text + "\n", encoding="utf-8")


def read_json(path: str | Path) -> Any:
    """Read and parse JSON file with UTF-8 encoding."""
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def read_model(path: str | Path, model_cls: type[T]) -> T:
    """Read and strictly validate a Pydantic model from JSON."""
    raw = Path(path).read_text(encoding="utf-8")
    return model_cls.model_validate_json(raw)


def safe_filename(name: str, max_length: int = 80) -> str:
    """Sanitize an identifier or filename safely across Windows and POSIX."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name.strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._ ")
    if not cleaned:
        cleaned = "item"
    return cleaned[:max_length]
