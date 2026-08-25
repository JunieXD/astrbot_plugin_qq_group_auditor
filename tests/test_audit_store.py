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


def test_confirmed_member_and_late_increase_share_one_membership(tmp_path):
    store = AuditStore(tmp_path / "audit.sqlite3")
    application_id, _ = store.record_application(
        platform_id="napcat-1",
        request=request("flag-1", 1000, "答案"),
        question="问题",
        question_source="config",
        review_prompt="规则",
    )
    confirmed = GroupMemberIncrease("123", "20001", "99999", "approve", 1010, "99999")
    first = store.record_join(
        platform_id="napcat-1",
        event=confirmed,
        nickname="成员",
        application_id_hint=application_id,
        correlation_hint="member_reconcile",
    )
    late_notice = GroupMemberIncrease("123", "20001", "30001", "approve", 1010, "99999")
    second = store.record_join(
        platform_id="napcat-1",
        event=late_notice,
        nickname="成员",
    )

    assert first == (first[0], application_id, True)
    assert second == (first[0], application_id, False)
    detail = store.detail(group_id="123", application_id=application_id)
    assert len(detail["memberships"]) == 1
    assert detail["memberships"][0]["join_operator_qq"] == "30001"
    store.close()


def test_duplicate_join_can_be_linked_when_application_arrives_late(tmp_path):
    store = AuditStore(tmp_path / "audit.sqlite3")
    join = GroupMemberIncrease("123", "20001", "30001", "approve", 1010, "99999")
    membership_id, application_id, created = store.record_join(
        platform_id="napcat-1",
        event=join,
        nickname="成员",
    )
    assert application_id is None
    assert created is True

    application_id, _ = store.record_application(
        platform_id="napcat-1",
        request=request("late-flag", 1000, "答案"),
        question="问题",
        question_source="config",
        review_prompt="规则",
    )
    duplicate = store.record_join(
        platform_id="napcat-1",
        event=join,
        nickname="成员",
        application_id_hint=application_id,
        correlation_hint="card_backfill",
    )

    assert duplicate == (membership_id, application_id, False)
    detail = store.detail(group_id="123", application_id=application_id)
    assert detail["memberships"][0]["id"] == membership_id
    store.close()


def test_backfill_can_correct_an_inferred_external_rejection(tmp_path):
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
        observed_at=1010,
    )
    assert store.infer_external_rejections(
        platform_id="napcat-1",
        now=1130,
        grace_seconds=120,
    ) == 1

    application = store.find_application_for_member(
        platform_id="napcat-1",
        group_id="123",
        user_id="20001",
        joined_at=5_000_000,
    )

    assert application["id"] == application_id
    assert application["time_correlation_fallback"] == "single_candidate"
    store.close()


def test_backfill_skips_ambiguous_applications_outside_the_time_window(tmp_path):
    store = AuditStore(tmp_path / "audit.sqlite3")
    for index, requested_at in enumerate((1000, 2000), start=1):
        application_id, _ = store.record_application(
            platform_id="napcat-1",
            request=request(f"flag-{index}", requested_at, f"答案{index}"),
            question="问题",
            question_source="config",
            review_prompt="规则",
        )
        store.record_action(
            application_id=application_id,
            kind="platform",
            action="approve",
            actor_qq="99999",
            source="plugin",
            status="succeeded",
        )

    application = store.find_application_for_member(
        platform_id="napcat-1",
        group_id="123",
        user_id="20001",
        joined_at=5_000_000,
    )

    assert application is None
    store.close()


def test_backfill_uses_latest_eligible_application_and_not_stale_approval(tmp_path):
    store = AuditStore(tmp_path / "audit.sqlite3")
    first_id, _ = store.record_application(
        platform_id="napcat-1",
        request=request("approved", 1000, "第一次答案"),
        question="问题",
        question_source="config",
        review_prompt="规则",
    )
    store.record_action(
        application_id=first_id,
        kind="platform",
        action="approve",
        actor_qq="99999",
        source="plugin",
        status="succeeded",
    )
    rejected_id, _ = store.record_application(
        platform_id="napcat-1",
        request=request("rejected", 2000, "第二次答案"),
        question="问题",
        question_source="config",
        review_prompt="规则",
    )
    store.record_action(
        application_id=rejected_id,
        kind="platform",
        action="reject",
        actor_qq="99999",
        source="plugin",
        status="succeeded",
    )

    assert store.card_backfill_applications(
        platform_id="napcat-1", group_id="123"
    ) == []
    store.close()


def test_late_join_notice_merges_direct_confirmation(tmp_path):
    store = AuditStore(tmp_path / "audit.sqlite3")
    application_id, _ = store.record_application(
        platform_id="napcat-1",
        request=request("direct", 1000, "答案"),
        question="问题",
        question_source="config",
        review_prompt="规则",
    )
    direct_event = GroupMemberIncrease(
        "123", "20001", "99999", "approve", 1001, "99999"
    )
    membership_id, _, _ = store.record_join(
        platform_id="napcat-1",
        event=direct_event,
        nickname="成员",
        application_id_hint=application_id,
        correlation_hint="member_reconcile_direct",
    )

    notice_event = GroupMemberIncrease(
        "123", "20001", "30001", "approve", 1010, "99999"
    )
    merged_id, merged_application_id, created = store.record_join(
        platform_id="napcat-1",
        event=notice_event,
        nickname="成员",
        correlation_hint="group_increase",
    )

    assert (merged_id, merged_application_id, created) == (
        membership_id,
        application_id,
        False,
    )
    detail = store.detail(group_id="123", application_id=application_id)
    assert len(detail["memberships"]) == 1
    assert detail["memberships"][0]["joined_at"] == 1010
    assert detail["memberships"][0]["join_operator_qq"] == "30001"
    assert detail["memberships"][0]["correlation"] == "group_increase"
    store.close()
