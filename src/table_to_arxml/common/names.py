"""Validation of AUTOSAR names and deterministic naming of generated objects."""

from __future__ import annotations

import re
from hashlib import sha256

MAX_SHORT_NAME = 128
NAME_HASH_LENGTH = 8
_SHORT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_C_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def short_name(value: str, label: str = "SHORT-NAME") -> str:
    if not _SHORT_NAME.fullmatch(value) or len(value) > MAX_SHORT_NAME:
        raise ValueError(
            f"{label} must be an AUTOSAR SHORT-NAME: start with a letter, use only "
            f"ASCII letters, digits and underscores, at most {MAX_SHORT_NAME} characters: {value!r}"
        )
    return value


def autosar_path(value: str, label: str = "AUTOSAR path", minimum_parts: int = 1) -> str:
    parts = value.split("/")
    if not value.startswith("/") or len(parts) - 1 < minimum_parts:
        raise ValueError(f"{label} must be absolute with at least {minimum_parts} name(s): {value!r}")
    for part in parts[1:]:
        short_name(part, label)
    return value


def c_identifier(value: str, label: str = "Runnable Symbol") -> str:
    if not _C_IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a C identifier: {value!r}")
    return value


def generated_name(value: str) -> str:
    if len(value) > MAX_SHORT_NAME:
        digest = sha256(value.encode("utf-8")).hexdigest()[:NAME_HASH_LENGTH]
        value = f"{value[:MAX_SHORT_NAME - NAME_HASH_LENGTH - 1]}_{digest}"
    return short_name(value, "Generated name")
