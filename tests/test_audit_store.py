from __future__ import annotations

from qq_group_auditor.audit_store import AuditStore
from qq_group_auditor.models import GroupMemberDecrease, GroupMemberIncrease, JoinRequest


def request(flag: str, requested_at: int, answer: str) -> JoinRequest:
    return JoinRequest(
        group_id="123",
        applicant_qq="20001",
        answer=answer,
        flag=flag,
        sub_type="add",
        requested_at=requested_at,
        self_id="99999",
        raw_comment=answer,
    )


def test_repeated_applications_and_memberships_are_kept_as_separate_history(tmp_path):
    store = AuditStore(tmp_path / "audit.sqlite3")
    first_id, first_created = store.record_application(
        platform_id="napcat-1",
        request=request("flag-1", 1000, "第一次答案"),
        question="问题",
        question_source="config",
        review_prompt="规则一",
    )
    store.record_action(
        application_id=first_id,
        kind="platform",
        action="approve",
        actor_qq="99999",
        source="plugin",
        status="succeeded",
        occurred_at=1001,
    )
    first_join = GroupMemberIncrease("123", "20001", "99999", "approve", 1010, "99999")
    membership_id, matched_id, join_created = store.record_join(
        platform_id="napcat-1",
        event=first_join,
        nickname="成员",
    )
    store.record_leave(
        platform_id="napcat-1",
        event=GroupMemberDecrease("123", "20001", "30001", "kick", 1100, "99999"),
    )

    second_id, second_created = store.record_application(
        platform_id="napcat-1",
        request=request("flag-2", 1200, "第二次答案"),
        question="新问题",
        question_source="platform",
        review_prompt="规则二",
    )
    second_membership_id, second_matched_id, _ = store.record_join(
        platform_id="napcat-1",
        event=GroupMemberIncrease("123", "20001", "30002", "approve", 1210, "99999"),
        nickname="新昵称",
    )

    assert first_created is True
    assert second_created is True
    assert first_id != second_id
    assert membership_id != second_membership_id
    assert matched_id == first_id
    assert second_matched_id == second_id

    history = store.history(group_id="123", applicant_qq="20001", limit=10)
    assert [item["answer"] for item in history] == ["第二次答案", "第一次答案"]
    assert history[1]["memberships"][0]["leave_sub_type"] == "kick"
    assert history[1]["memberships"][0]["leave_operator_qq"] == "30001"
    store.close()


def test_duplicate_request_join_and_leave_events_are_idempotent(tmp_path):
    store = AuditStore(tmp_path / "audit.sqlite3")
    application_id, created = store.record_application(
        platform_id="napcat-1",
        request=request("same-flag", 1000, "答案"),
        question="问题",
        question_source="config",
        review_prompt="规则",
    )
    duplicate_id, duplicate_created = store.record_application(
        platform_id="napcat-1",
        request=request("same-flag", 1000, "答案"),
        question="问题",
        question_source="config",
        review_prompt="规则",
    )
    join = GroupMemberIncrease("123", "20001", "30001", "approve", 1010, "99999")
    first_membership = store.record_join(platform_id="napcat-1", event=join)
    duplicate_membership = store.record_join(platform_id="napcat-1", event=join)
    leave = GroupMemberDecrease("123", "20001", "20001", "leave", 1100, "99999")
    first_leave = store.record_leave(platform_id="napcat-1", event=leave)
    duplicate_leave = store.record_leave(platform_id="napcat-1", event=leave)

    assert created is True
    assert duplicate_created is False
    assert duplicate_id == application_id
    assert first_membership[2] is True
    assert duplicate_membership == (first_membership[0], application_id, False)
    assert first_leave[1] is True
    assert duplicate_leave == (first_leave[0], False)
    store.close()


def test_external_checked_without_join_is_inferred_as_reject(tmp_path):
    store = AuditStore(tmp_path / "audit.sqlite3")
    application_id, _ = store.record_application(
        platform_id="napcat-1",
        request=request("flag-1", 1000, "答案"),
        question="问题",
        question_source="config",
        review_prompt="规则",
    )
    store.mark_external_checked(
        application_id=application_id,
        actor_qq="30001",
        observed_at=1100,
    )

    assert store.infer_external_rejections(
        platform_id="napcat-1",
        now=1219,
        grace_seconds=120,
    ) == 0
    assert store.infer_external_rejections(
        platform_id="napcat-1",
        now=1220,
        grace_seconds=120,
    ) == 1

    detail = store.detail(group_id="123", application_id=application_id)
    action = detail["actions"][0]
    assert action["action"] == "reject"
    assert action["actor_qq"] == "30001"
    assert action["source"] == "external_inferred"
    assert action["status"] == "inferred"
    store.close()


def test_invited_join_without_application_is_queryable(tmp_path):
    store = AuditStore(tmp_path / "audit.sqlite3")
    membership_id, application_id, created = store.record_join(
        platform_id="napcat-1",
        event=GroupMemberIncrease("123", "20001", "30001", "invite", 1000, "99999"),
        nickname="被邀请成员",
    )

    history = store.history(group_id="123", applicant_qq="20001")
    detail = store.detail(group_id="123", application_id=f"J{membership_id}")

    assert created is True
    assert application_id is None
    assert history[0]["id"] == f"J{membership_id}"
    assert history[0]["record_type"] == "join_only"
    assert detail["memberships"][0]["join_sub_type"] == "invite"
    assert detail["memberships"][0]["join_operator_qq"] == "30001"
    store.close()
