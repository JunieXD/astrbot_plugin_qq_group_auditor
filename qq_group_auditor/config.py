from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_REJECT_REASON = "加群答案不符合要求，请重新申请并按提示填写。"
DEFAULT_CONFIG = {"group_audits": []}
VALID_FAILURE_ACTIONS = {"ignore", "reject"}


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
        "enabled": bool(item.get("enabled", True)),
        "group_id": group_id,
        "review_prompt": str(item.get("review_prompt") or "").strip(),
        "failure_action": failure_action,
        "reject_reason": reject_reason,
        "admin_qq_ids": normalize_admin_ids(item.get("admin_qq_ids")),
        "notify_on_approve": bool(item.get("notify_on_approve", False)),
        "notify_on_reject": bool(item.get("notify_on_reject", False)),
        "notify_on_ignore": bool(item.get("notify_on_ignore", False)),
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
    normalized_group_id = normalize_id(group_id)
    for item in config.get("group_audits") or []:
        if item.get("group_id") == normalized_group_id and item.get("enabled", True):
            return item
    return None


def is_group_admin(config: dict[str, Any], group_id: Any, qq_id: Any) -> bool:
    group_config = find_group_config(config, group_id)
    if group_config is None:
        return False
    return normalize_id(qq_id) in set(group_config.get("admin_qq_ids") or [])
