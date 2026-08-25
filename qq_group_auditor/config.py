from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_REJECT_REASON = "加群答案不符合要求，请重新申请并按提示填写。"
DEFAULT_REVIEW_PROMPT = (
    "请判断申请人的加群答案是否符合本群要求。只有答案明确符合要求时才 approve=true。"
)
DEFAULT_CARD_TEMPLATE = "{nickname}"
DEFAULT_CONFIG = {"group_audits": []}
VALID_FAILURE_ACTIONS = {"ignore", "reject"}
TRUE_STRINGS = {"true", "1", "yes", "on"}
FALSE_STRINGS = {"false", "0", "no", "off", ""}


def normalize_id(value: Any) -> str:
    return str(value or "").strip()


def normalize_admin_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value:
        normalized = normalize_id(item)
        if normalized:
            ids.append(normalized)
    return ids


def normalize_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUE_STRINGS:
            return True
        if normalized in FALSE_STRINGS:
            return False
    if value is None:
        return default
    return bool(value)


def normalize_group_item(item: dict[str, Any]) -> dict[str, Any] | None:
    group_id = normalize_id(item.get("group_id"))
    if not group_id:
        return None

    failure_action = str(item.get("failure_action") or "ignore").strip().lower()
    if failure_action not in VALID_FAILURE_ACTIONS:
        failure_action = "ignore"

    reject_reason = str(item.get("reject_reason") or "").strip() or DEFAULT_REJECT_REASON

    return {
        "__template_key": item.get("__template_key") or "group_audit",
        "enabled": normalize_bool(item.get("enabled"), default=True),
        "group_id": group_id,
        "review_prompt": str(item.get("review_prompt") or "").strip()
        or DEFAULT_REVIEW_PROMPT,
        "failure_action": failure_action,
        "reject_reason": reject_reason,
        "admin_qq_ids": normalize_admin_ids(item.get("admin_qq_ids")),
        "notify_on_approve": normalize_bool(item.get("notify_on_approve")),
        "notify_on_reject": normalize_bool(item.get("notify_on_reject")),
        "notify_on_ignore": normalize_bool(item.get("notify_on_ignore")),
        "audit_log_enabled": normalize_bool(
            item.get("audit_log_enabled"),
            default=True,
        ),
        "auto_set_card": normalize_bool(item.get("auto_set_card")),
        "card_template": str(item.get("card_template") or "").strip()
        or DEFAULT_CARD_TEMPLATE,
        "application_question": str(item.get("application_question") or "").strip(),
    }


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    raw = raw or {}
    for key, value in raw.items():
        config[key] = deepcopy(value)

    group_audits: list[dict[str, Any]] = []
    for item in config.get("group_audits") or []:
        if not isinstance(item, dict):
            continue
        normalized = normalize_group_item(item)
        if normalized is not None:
            group_audits.append(normalized)
    config["group_audits"] = group_audits
    return config


def find_group_config(config: dict[str, Any], group_id: Any) -> dict[str, Any] | None:
    item = find_group_policy(config, group_id)
    if item is not None and item.get("enabled", True):
        return item
    return None


def find_group_policy(config: dict[str, Any], group_id: Any) -> dict[str, Any] | None:
    normalized_group_id = normalize_id(group_id)
    for item in config.get("group_audits") or []:
        if item.get("group_id") == normalized_group_id:
            return item
    return None


def is_group_admin(config: dict[str, Any], group_id: Any, qq_id: Any) -> bool:
    group_config = find_group_policy(config, group_id)
    if group_config is None:
        return False
    return normalize_id(qq_id) in set(group_config.get("admin_qq_ids") or [])
