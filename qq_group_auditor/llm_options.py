"""Request-local ECNU settings; never mutate AstrBot's shared provider/client."""
from __future__ import annotations

from copy import copy, deepcopy
from typing import Any

from .llm_usage import field


DEFAULT_LLM_REVIEW = {
    "provider_id": "",
    "ecnu_structured_output": True,
    "ecnu_thinking": "inherit",
    "ecnu_reasoning_effort": "low",
}
ECNU_EFFORTS = {
    "ecnu-plus": {"low", "medium", "xhigh"},
    "ecnu-max": {"low", "high", "max"},
}
REVIEW_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "qq_group_review",
        "schema": {
            "type": "object",
            "properties": {"approve": {"type": "boolean"}, "reason": {"type": "string"}},
            "required": ["approve", "reason"],
            "additionalProperties": False,
        },
    },
}


def review_completion_text(response: Any) -> str:
    choices = field(field(response, "raw_completion"), "choices", [])
    if isinstance(choices, (list, tuple)) and choices:
        choice = choices[0]
        if (field(choice, "finish_reason") in {"length", "content_filter", "tool_calls", "function_call"}
                or field(field(choice, "message"), "refusal")):
            # Let the existing invalid-output retry handle even syntactically
            # valid JSON returned with truncation/refusal metadata. Keep the
            # original response for usage/finish_reason/content statistics.
            return ""
    return str(field(response, "completion_text", "") or "")


def normalize_llm_review(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    result = deepcopy(DEFAULT_LLM_REVIEW)
    result["provider_id"] = str(raw.get("provider_id") or "").strip()
    enabled = raw.get("ecnu_structured_output", True)
    result["ecnu_structured_output"] = enabled is True or (
        isinstance(enabled, str) and enabled.strip().lower() in {"true", "1", "yes", "on"}
    )
    for name, allowed in (
        ("ecnu_thinking", {"inherit", "enabled", "disabled"}),
        ("ecnu_reasoning_effort", set().union(*ECNU_EFFORTS.values())),
    ):
        value = str(raw.get(name) or result[name]).strip().lower()
        if value in allowed:
            result[name] = value
    return result


def ecnu_request_provider(provider: Any, options: dict[str, Any]) -> Any | None:
    """Return an isolated OpenAI adapter when the model is ECNU Plus/Max.

    AstrBot 4.x can drop generation kwargs in _prepare_chat_payload. Use its
    existing custom_extra_body path on a private adapter copy, preserving
    auth, proxy, timeout, retries and the normalized/raw usage response.
    """
    get_model = getattr(provider, "get_model", None)
    model = str(get_model() or "").strip().lower() if callable(get_model) else ""
    if model not in ECNU_EFFORTS:
        return None
    config = getattr(provider, "provider_config", None)
    client = getattr(provider, "client", None)
    if (not isinstance(config, dict) or config.get("type") != "openai_chat_completion"
            or not callable(getattr(client, "with_options", None))):
        raise RuntimeError("ECNU 审核设置需要 AstrBot OpenAI Chat Completion 适配器")

    request_provider = copy(provider)
    request_provider.provider_config = deepcopy(config)
    extra = request_provider.provider_config.get("custom_extra_body")
    extra = deepcopy(extra) if isinstance(extra, dict) else {}
    # False disables the constraint for this review even if the shared model
    # has response_format configured. Other model calls remain untouched.
    extra.pop("response_format", None)
    if options["ecnu_structured_output"]:
        extra["response_format"] = deepcopy(REVIEW_RESPONSE_FORMAT)
    thinking = options["ecnu_thinking"]
    if thinking != "inherit":
        extra["thinking"] = {"type": thinking}
        extra.pop("reasoning_effort", None)
        if thinking == "enabled":
            effort = options["ecnu_reasoning_effort"]
            if effort not in ECNU_EFFORTS[model]:
                raise ValueError(f"{model} 不支持思考程度 {effort}，请调整本插件的审核模型设置")
            extra["reasoning_effort"] = effort
    request_provider.provider_config["custom_extra_body"] = extra
    # text_chat rotates client.api_key, so copying only provider_config would
    # still mutate shared state. with_options isolates SDK options while
    # reusing the provider-owned HTTP connection pool; do not close it here.
    request_provider.client = client.with_options()
    if request_provider.client is client:
        raise RuntimeError("ECNU 审核无法隔离模型客户端")
    return request_provider
