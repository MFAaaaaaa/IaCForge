import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path


OFFLINE_EVIDENCE_FILE_ENV = "IAC_KG_OFFLINE_EVIDENCE_FILE"
OFFLINE_EVIDENCE_STRICT_ENV = "IAC_KG_OFFLINE_EVIDENCE_STRICT"


def prompt_sha256(prompt):
    return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()


def _strict_enabled():
    return os.environ.get(OFFLINE_EVIDENCE_STRICT_ENV, "0").lower() in {
        "1",
        "true",
        "yes",
    }


@lru_cache(maxsize=4)
def _load_jsonl(path_text):
    path = Path(path_text).expanduser().resolve()
    if not path.exists():
        if _strict_enabled():
            raise FileNotFoundError(f"KG offline evidence file not found: {path}")
        return {}

    index = {}
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                if _strict_enabled():
                    raise ValueError(
                        f"Invalid JSONL in KG offline evidence file {path}:{line_no}"
                    ) from exc
                continue
            key = row.get("prompt_sha256") or prompt_sha256(row.get("prompt", ""))
            index.setdefault(key, []).append(row)
    return index


def get_offline_evidence(prompt):
    path = os.environ.get(OFFLINE_EVIDENCE_FILE_ENV, "").strip()
    if not path:
        return None

    key = prompt_sha256(prompt)
    rows = _load_jsonl(path).get(key, [])
    for row in rows:
        stored_prompt = row.get("prompt")
        if stored_prompt is None or str(stored_prompt) == str(prompt or ""):
            evidence = row.get("evidence")
            return evidence if isinstance(evidence, str) else json.dumps(
                evidence, indent=2, sort_keys=True
            )

    if _strict_enabled():
        raise KeyError(
            "KG offline evidence missing for prompt_sha256="
            f"{key} in {Path(path).expanduser().resolve()}"
        )
    return None
