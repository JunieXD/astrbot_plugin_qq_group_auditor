from __future__ import annotations

from qq_group_auditor.config import (
    DEFAULT_REJECT_REASON,
    DEFAULT_REVIEW_PROMPT,
    find_group_config,
    is_group_admin,
    normalize_config,
    review_delay,
)

import pytest


@pytest.mark.parametrize("overrides,expected", [
    ({}, (5, 15)),
    ({"review_min_seconds": 20, "review_max_seconds": 40}, (20, 40)),
    ({"review_min_seconds": -1, "review_max_seconds": 30}, (5, 30)),
    ({"review_min_seconds": 25, "review_max_seconds": -1}, (25, 25)),
    ({"review_min_seconds": "nan", "review_max_seconds": "invalid"}, (5, 15)),
    ({"review_min_seconds": 10, "review_max_seconds": 2}, (10, 10)),
])
def test_group_review_delay_inherits_and_normalizes(overrides, expected):
    config = normalize_config({"automation_pacing": {"review_min_seconds": 5, "review_max_seconds": 15},
                               "group_audits": [{"group_id": "123", **overrides}]})
    assert review_delay(config, config["group_audits"][0]) == expected
    assert config["automation_pacing"]["action_gap_min_seconds"] == 8


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
                    "invite_action": "approve",
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
    assert item["invite_action"] == "approve"
    assert item["audit_log_enabled"] is True
    assert item["auto_set_card"] is False
    assert item["card_template"] == "{nickname}"
    assert item["application_question"] == ""


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


def test_missing_and_invalid_invite_action_default_to_ignore():
    config = normalize_config(
        {
            "group_audits": [
                {"group_id": "1"},
                {"group_id": "2", "invite_action": "delete"},
            ]
        }
    )

    assert config["group_audits"][0]["invite_action"] == "ignore"
    assert config["group_audits"][1]["invite_action"] == "ignore"


def test_empty_review_prompt_uses_default():
    config = normalize_config(
        {
            "group_audits": [
                {"group_id": "1"},
                {"group_id": "2", "review_prompt": "   "},
            ]
        }
    )

    assert config["group_audits"][0]["review_prompt"] == DEFAULT_REVIEW_PROMPT
    assert config["group_audits"][1]["review_prompt"] == DEFAULT_REVIEW_PROMPT


def test_empty_group_id_items_are_skipped():
    config = normalize_config(
        {
            "group_audits": [
                {"group_id": "", "review_prompt": "A"},
                {"group_id": "   ", "review_prompt": "B"},
                {"review_prompt": "C"},
                {"group_id": "3", "review_prompt": "D"},
            ]
        }
    )

    assert [item["group_id"] for item in config["group_audits"]] == ["3"]


def test_string_bool_values_are_normalized():
    config = normalize_config(
        {
            "group_audits": [
                {
                    "group_id": "1",
                    "enabled": "false",
                    "notify_on_approve": "true",
                    "notify_on_reject": "0",
                    "notify_on_ignore": "on",
                    "audit_log_enabled": "false",
                    "auto_set_card": "true",
                },
                {
                    "group_id": "2",
                    "enabled": "yes",
                    "notify_on_approve": "off",
                    "notify_on_reject": "1",
                    "notify_on_ignore": "",
                },
            ]
        }
    )

    first, second = config["group_audits"]
    assert first["enabled"] is False
    assert first["notify_on_approve"] is True
    assert first["notify_on_reject"] is False
    assert first["notify_on_ignore"] is True
    assert first["audit_log_enabled"] is False
    assert first["auto_set_card"] is True
    assert second["enabled"] is True
    assert second["notify_on_approve"] is False
    assert second["notify_on_reject"] is True
    assert second["notify_on_ignore"] is False


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
