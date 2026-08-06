"""Provider alignment and canonical hashing helpers."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any


AWS_PROVIDER_VERSION = "5.90.0"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def provider_constraint() -> str:
    return os.environ.get(
        "AWS_PROVIDER_VERSION_CONSTRAINT", f"= {AWS_PROVIDER_VERSION}"
    ).strip()


def assert_version_alignment(provider_version: str = "") -> None:
    expected = AWS_PROVIDER_VERSION
    configured = provider_constraint().lstrip("= ").strip()
    actual = str(provider_version or expected).strip()
    if configured != expected or actual != expected:
        raise ValueError(
            "Provider version drift detected: "
            f"runtime={configured!r}, knowledge={actual!r}, expected={expected!r}. "
            "Rebuild schema, docs, and Full KG together before changing provider versions."
        )
