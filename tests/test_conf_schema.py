from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_conf_schema_is_valid_json_and_uses_supported_types():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

    assert schema["group_audits"]["type"] == "template_list"
    template = schema["group_audits"]["templates"]["group_audit"]
    items = template["items"]
    assert items["group_id"]["type"] == "string"
    assert items["review_prompt"]["type"] == "text"
    assert items["failure_action"]["type"] == "string"
    assert items["invite_action"]["type"] == "string"
    assert items["invite_action"]["default"] == "ignore"
    assert items["admin_qq_ids"]["type"] == "list"
    assert items["notify_on_approve"]["type"] == "bool"
    assert items["notify_on_reject"]["type"] == "bool"
    assert items["notify_on_ignore"]["type"] == "bool"
    assert items["audit_log_enabled"]["type"] == "bool"
    assert items["auto_set_card"]["type"] == "bool"
    assert items["auto_set_card"]["default"] is False
    assert items["card_template"]["type"] == "string"
    assert items["application_question"]["type"] == "text"
