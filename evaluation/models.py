"""OpenAI-compatible local model client with stage-specific decoding."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

from openai import BadRequestError, OpenAI


@dataclass(frozen=True)
class GenerationOutput:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    model: str
    stage: str
    request_parameters: dict


def _stage_parameters(stage: str, max_tokens: int | None = None):
    stage = str(stage or "hcl").lower()
    prefix = "QWEN_IR" if stage == "ir" else "QWEN_HCL"
    if stage == "ir":
        defaults = {"temperature": 0.0, "top_p": 1.0, "max_tokens": 1536}
    elif stage == "repair":
        defaults = {"temperature": 0.0, "top_p": 1.0, "max_tokens": 2048}
        prefix = "QWEN_REPAIR"
    else:
        defaults = {"temperature": 0.0, "top_p": 1.0, "max_tokens": 2048}
    return {
        "temperature": float(
            os.environ.get(
                f"{prefix}_TEMPERATURE",
                os.environ.get("QWEN_TEMPERATURE", str(defaults["temperature"])),
            )
        ),
        "top_p": float(
            os.environ.get(
                f"{prefix}_TOP_P",
                os.environ.get("QWEN_TOP_P", str(defaults["top_p"])),
            )
        ),
        "max_tokens": int(
            max_tokens
            or os.environ.get(
                f"{prefix}_MAX_TOKENS",
                os.environ.get("QWEN_MAX_TOKENS", str(defaults["max_tokens"])),
            )
        ),
    }


def _usage_value(response, name):
    usage = getattr(response, "usage", None)
    return int(getattr(usage, name, 0) or 0)


def openai_compatible_chat(
    system_prompt,
    user_prompt,
    *,
    stage="hcl",
    max_tokens=None,
    guided_json=None,
):
    client = OpenAI(
        api_key=os.environ.get("QWEN_API_KEY", "EMPTY"),
        base_url=os.environ.get("QWEN_BASE_URL", "http://127.0.0.1:8000/v1"),
    )
    parameters = _stage_parameters(stage, max_tokens)
    request = {
        "model": os.environ.get("QWEN_MODEL", "qwen2.5-coder-3b"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        **parameters,
    }
    if os.environ.get("QWEN_DISABLE_THINKING", "0") == "1":
        request["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    if guided_json is None:
        guided_json = stage == "ir" and os.environ.get(
            "QWEN_IR_GUIDED_JSON", "1"
        ).lower() in {"1", "true", "yes"}
    if guided_json:
        request["response_format"] = {"type": "json_object"}

    retries = int(os.environ.get("QWEN_MAX_RETRIES", "3"))
    retry_delay = float(os.environ.get("QWEN_RETRY_DELAY_SECONDS", "2"))
    attempt = 0
    removed_unsupported_guidance = False
    started = time.perf_counter()
    while True:
        try:
            response = client.chat.completions.create(**request)
            latency_ms = round((time.perf_counter() - started) * 1000)
            return GenerationOutput(
                text=response.choices[0].message.content or "",
                input_tokens=_usage_value(response, "prompt_tokens"),
                output_tokens=_usage_value(response, "completion_tokens"),
                latency_ms=latency_ms,
                model=request["model"],
                stage=stage,
                request_parameters={
                    key: request[key]
                    for key in ("temperature", "top_p", "max_tokens", "response_format")
                    if key in request
                },
            )
        except BadRequestError as exc:
            message = str(exc)
            if (
                "response_format" in request
                and not removed_unsupported_guidance
                and any(
                    marker in message.lower()
                    for marker in ("response_format", "guided", "json_object", "unsupported")
                )
            ):
                request.pop("response_format", None)
                removed_unsupported_guidance = True
                continue
            if "maximum context length" not in message:
                raise
            model_limit = re.search(r"maximum context length is (\d+) tokens", message)
            input_tokens = re.search(
                r"prompt contains at least (\d+) input tokens", message
            )
            if not model_limit or not input_tokens:
                raise
            next_max_tokens = (
                int(model_limit.group(1)) - int(input_tokens.group(1)) - 16
            )
            next_max_tokens = max(
                128, min(request["max_tokens"] - 1, next_max_tokens)
            )
            if next_max_tokens >= request["max_tokens"]:
                raise
            request["max_tokens"] = next_max_tokens
        except Exception:
            attempt += 1
            if attempt > retries:
                raise
            time.sleep(retry_delay * attempt)


def generate_with_metadata(
    system_prompt,
    user_prompt,
    *,
    stage="hcl",
    max_tokens=None,
    guided_json=None,
):
    return openai_compatible_chat(
        system_prompt,
        user_prompt,
        stage=stage,
        max_tokens=max_tokens,
        guided_json=guided_json,
    )


def generate_text(system_prompt, user_prompt, **kwargs):
    return generate_with_metadata(system_prompt, user_prompt, **kwargs).text


def Qwen25Coder3B(system_prompt, user_prompt):
    """Backward-compatible alias retained for archived experiment code."""

    return generate_text(system_prompt, user_prompt)
