from __future__ import annotations

import pytest

from qq_group_auditor.models import JoinRequest, ReviewDecision
from qq_group_auditor.reviewer import LLMReviewError
from qq_group_auditor.service import AuditService


class FakeReviewer:
    def __init__(self, decision: ReviewDecision | Exception) -> None:
        self.decision = decision
        self.calls = 0

    async def review(self, *, group_config, request):
        self.calls += 1
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


class FakePlatform:
    def __init__(self) -> None:
        self.actions: list[tuple[JoinRequest, bool, str]] = []
        self.fail = False

    async def set_group_request(self, request: JoinRequest, *, approve: bool, reason: str):
        if self.fail:
            raise RuntimeError("platform failed")
        self.actions.append((request, approve, reason))


class FakeNotifier:
    def __init__(self) -> None:
        self.notices: list[tuple[list[str], str]] = []

    async def notify(self, *, group_config, request, title, action, reason="", error=""):
        self.notices.append((group_config["admin_qq_ids"], f"{title}|{action}|{reason}|{error}"))


class FailingNotifier:
    async def notify(self, *, group_config, request, title, action, reason="", error=""):
        raise RuntimeError("notify failed")


def group_config(**overrides):
    config = {
        "group_id": "123",
        "enabled": True,
        "review_prompt": "规则",
        "failure_action": "ignore",
        "reject_reason": "请重新申请",
        "admin_qq_ids": ["10001"],
        "notify_on_approve": False,
        "notify_on_reject": False,
        "notify_on_ignore": False,
    }
    config.update(overrides)
    return config


def request(answer: str = "答案") -> JoinRequest:
    return JoinRequest(
        group_id="123",
        applicant_qq="20001",
        answer=answer,
        flag="flag",
        sub_type="add",
    )


@pytest.mark.asyncio
async def test_approve_calls_platform_approve_and_optional_notice():
    platform = FakePlatform()
    notifier = FakeNotifier()
    service = AuditService(FakeReviewer(ReviewDecision(True, "符合")), platform, notifier)

    result = await service.handle_request(group_config(notify_on_approve=True), request())

    assert result.action == "approve"
    assert platform.actions == [(request(), True, "")]
    assert notifier.notices[0][1].startswith("加群审核通过|approve|符合|")


@pytest.mark.asyncio
async def test_reject_false_decision_uses_fixed_reason():
    platform = FakePlatform()
    notifier = FakeNotifier()
    service = AuditService(FakeReviewer(ReviewDecision(False, "不符合")), platform, notifier)

    result = await service.handle_request(
        group_config(failure_action="reject", notify_on_reject=True),
        request(),
    )

    assert result.action == "reject"
    assert platform.actions == [(request(), False, "请重新申请")]
    assert notifier.notices[0][1].startswith("加群审核拒绝|reject|不符合|")


@pytest.mark.asyncio
async def test_ignore_false_decision_does_not_call_platform():
    platform = FakePlatform()
    notifier = FakeNotifier()
    service = AuditService(FakeReviewer(ReviewDecision(False, "不符合")), platform, notifier)

    result = await service.handle_request(group_config(notify_on_ignore=True), request())

    assert result.action == "ignore"
    assert platform.actions == []
    assert notifier.notices[0][1].startswith("加群审核忽略|ignore|不符合|")


@pytest.mark.asyncio
async def test_empty_answer_skips_reviewer_and_follows_failure_action():
    reviewer = FakeReviewer(ReviewDecision(True, "不会调用"))
    platform = FakePlatform()
    service = AuditService(reviewer, platform, FakeNotifier())

    result = await service.handle_request(group_config(failure_action="reject"), request("   "))

    assert result.action == "reject"
    assert reviewer.calls == 0
    assert platform.actions == [(request("   "), False, "请重新申请")]


@pytest.mark.asyncio
async def test_llm_error_notifies_admin_and_leaves_request_untouched():
    platform = FakePlatform()
    notifier = FakeNotifier()
    service = AuditService(FakeReviewer(LLMReviewError("invalid json")), platform, notifier)

    result = await service.handle_request(group_config(), request())

    assert result.action == "error"
    assert platform.actions == []
    assert "LLM审核异常" in notifier.notices[0][1]
    assert "invalid json" in notifier.notices[0][1]


@pytest.mark.asyncio
async def test_approve_notice_failure_keeps_approve_result():
    platform = FakePlatform()
    service = AuditService(
        FakeReviewer(ReviewDecision(True, "符合")),
        platform,
        FailingNotifier(),
    )

    result = await service.handle_request(group_config(notify_on_approve=True), request())

    assert result.action == "approve"
    assert result.reason == "符合"
    assert platform.actions == [(request(), True, "")]


@pytest.mark.asyncio
async def test_llm_error_notice_failure_still_returns_error_result():
    platform = FakePlatform()
    service = AuditService(
        FakeReviewer(LLMReviewError("invalid json")),
        platform,
        FailingNotifier(),
    )

    result = await service.handle_request(group_config(), request())

    assert result.action == "error"
    assert result.reason == "invalid json"
    assert platform.actions == []


@pytest.mark.asyncio
async def test_platform_error_returns_error_and_notifies_admin():
    platform = FakePlatform()
    platform.fail = True
    notifier = FakeNotifier()
    service = AuditService(FakeReviewer(ReviewDecision(True, "符合")), platform, notifier)

    result = await service.handle_request(group_config(), request())

    assert result.action == "error"
    assert "platform failed" in result.reason
    assert platform.actions == []
    assert notifier.notices[0][1].startswith("平台审核接口异常|approve||platform failed")
