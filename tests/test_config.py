from __future__ import annotations

from qq_group_auditor.config import (
    DEFAULT_REJECT_REASON,
    find_group_config,
    is_group_admin,
    normalize_config,
)


def test_normalize_config_adds_defaults():
    config = normalize_config({})

    assert config["group_audits"] == []


def test_normalize_config_preserves_template_list_and_coerces_scalars():
    config = normalize_config(
        {
            "group_audits": [
                {
                    "__template_key": "group_audit",
                    "enabled": True,
                    "group_id": 123456,
                    "review_prompt": "答案必须包含 AutoEmailSender",
                    "failure_action": "reject",
                    "reject_reason": "",
                    "admin_qq_ids": [10001, "10002", ""],
                    "notify_on_approve": True,
                    "notify_on_reject": False,
                    "notify_on_ignore": True,
                }
            ]
        }
    )

    item = config["group_audits"][0]
    assert item["__template_key"] == "group_audit"
    assert item["group_id"] == "123456"
    assert item["reject_reason"] == DEFAULT_REJECT_REASON
    assert item["admin_qq_ids"] == ["10001", "10002"]
    assert item["failure_action"] == "reject"


def test_invalid_failure_action_defaults_to_ignore():
    config = normalize_config(
        {
            "group_audits": [
                {
                    "group_id": "123456",
                    "review_prompt": "规则",
                    "failure_action": "delete",
                }
            ]
        }
    )

    assert config["group_audits"][0]["failure_action"] == "ignore"


def test_find_group_config_ignores_disabled_groups():
    config = normalize_config(
        {
            "group_audits": [
                {"group_id": "1", "enabled": False, "review_prompt": "A"},
                {"group_id": "2", "enabled": True, "review_prompt": "B"},
            ]
        }
    )

    assert find_group_config(config, "1") is None
    assert find_group_config(config, 2)["review_prompt"] == "B"


def test_is_group_admin_uses_group_specific_admin_list():
    config = normalize_config(
        {
            "group_audits": [
                {
                    "group_id": "123456",
                    "review_prompt": "规则",
                    "admin_qq_ids": ["10001"],
                }
            ]
        }
    )

    assert is_group_admin(config, "123456", "10001") is True
    assert is_group_admin(config, "123456", "10002") is False
    assert is_group_admin(config, "999999", "10001") is False
