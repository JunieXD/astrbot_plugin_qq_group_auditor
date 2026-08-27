from qq_group_auditor.audit_text import format_detail


def test_detail_includes_group_card_attempts():
    text = format_detail(
        {
            "id": 7,
            "group_id": "123",
            "applicant_qq": "20002",
            "requested_at": 1_700_000_000,
            "question": "年级",
            "answer": "24级",
            "actions": [],
            "memberships": [],
            "card_attempts": [
                {
                    "attempted_at": 1_700_000_001,
                    "source": "member_reconcile_direct",
                    "status": "not_in_group",
                    "error": "群成员不存在",
                }
            ],
        }
    )

    assert "群名片尝试：" in text
    assert "来源=member_reconcile_direct 状态=not_in_group" in text
    assert "错误：群成员不存在" in text


def test_detail_labels_direct_invite_join_without_application():
    text = format_detail(
        {
            "id": "J8",
            "record_type": "join_only",
            "group_id": "123",
            "applicant_qq": "20002",
            "requested_at": 1_700_000_000,
            "question": "",
            "answer": "",
            "actions": [],
            "memberships": [{"join_sub_type": "invite", "joined_at": 1_700_000_000}],
            "card_attempts": [],
        }
    )

    assert "类型：受邀入群" in text
