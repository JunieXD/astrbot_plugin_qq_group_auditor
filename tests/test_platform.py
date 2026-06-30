from __future__ import annotations

import pytest

from qq_group_auditor.platform import (
    PlatformActionError,
    extract_join_request,
    set_group_request,
)


class RawEvent(dict):
    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class FakeMessageObj:
    def __init__(self, raw_message):
        self.raw_message = raw_message


class FakeEvent:
    def __init__(self, raw_message):
        self.message_obj = FakeMessageObj(raw_message)


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail = False

    async def call_action(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("action failed")
        return {"status": "ok"}


class FakePlatform:
    def __init__(self, bot):
        self.bot = bot


class FakeContext:
    def __init__(self, bot):
        self.platform_manager = type("PM", (), {"platform_insts": [FakePlatform(bot)]})()


def test_extract_join_request_from_onebot_add_request():
    event = FakeEvent(
        RawEvent(
            {
                "post_type": "request",
                "request_type": "group",
                "sub_type": "add",
                "group_id": 123456,
                "user_id": 10001,
                "comment": "AutoEmailSender",
                "flag": "flag-1",
            }
        )
    )

    request = extract_join_request(event)

    assert request.group_id == "123456"
    assert request.applicant_qq == "10001"
    assert request.answer == "AutoEmailSender"
    assert request.flag == "flag-1"
    assert request.sub_type == "add"


def test_extract_join_request_ignores_non_group_request():
    event = FakeEvent(RawEvent({"post_type": "message"}))

    assert extract_join_request(event) is None


def test_extract_join_request_raises_for_missing_required_fields():
    event = FakeEvent(
        RawEvent(
            {
                "post_type": "request",
                "request_type": "group",
                "sub_type": "add",
                "group_id": 123456,
            }
        )
    )

    with pytest.raises(ValueError, match="missing"):
        extract_join_request(event)


@pytest.mark.asyncio
async def test_set_group_request_calls_onebot_action():
    bot = FakeBot()
    context = FakeContext(bot)

    await set_group_request(
        context,
        flag="flag-1",
        sub_type="add",
        approve=False,
        reason="答案不符合",
    )

    assert bot.calls == [
        {
            "action": "set_group_add_request",
            "flag": "flag-1",
            "sub_type": "add",
            "approve": False,
            "reason": "答案不符合",
        }
    ]


@pytest.mark.asyncio
async def test_set_group_request_wraps_action_failure():
    bot = FakeBot()
    bot.fail = True
    context = FakeContext(bot)

    with pytest.raises(PlatformActionError, match="set_group_add_request failed"):
        await set_group_request(
            context,
            flag="flag-1",
            sub_type="add",
            approve=True,
            reason="",
        )
