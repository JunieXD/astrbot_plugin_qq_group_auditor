from __future__ import annotations

import pytest

from qq_group_auditor.platform import (
    PlatformActionError,
    extract_join_request,
    find_onebot_bot,
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
    def __init__(
        self,
        bot,
        platform_id: str | None = None,
        *,
        meta_id: str | None = None,
        metadata_id: str | None = None,
        config_id: str | None = None,
    ):
        self.bot = bot
        self.id = platform_id
        self._meta_id = meta_id
        self.metadata = type("Metadata", (), {"id": metadata_id})()
        self.config = {"id": config_id} if config_id is not None else {}

    def meta(self):
        return type("Meta", (), {"id": self._meta_id})()


class FakeContext:
    def __init__(self, bot=None, platforms=None, use_get_insts: bool = False):
        platform_insts = platforms if platforms is not None else [FakePlatform(bot)]
        if use_get_insts:
            self.platform_manager = type(
                "PM",
                (),
                {"get_insts": lambda self: platform_insts},
            )()
        else:
            self.platform_manager = type("PM", (), {"platform_insts": platform_insts})()


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


def test_extract_join_request_ignores_invite_group_request():
    event = FakeEvent(
        RawEvent(
            {
                "post_type": "request",
                "request_type": "group",
                "sub_type": "invite",
                "group_id": 123456,
                "user_id": 10001,
                "comment": "AutoEmailSender",
                "flag": "flag-1",
            }
        )
    )

    assert extract_join_request(event) is None


@pytest.mark.parametrize("raw_sub_type", [None, ""])
def test_extract_join_request_raises_for_missing_sub_type(raw_sub_type):
    raw_event = {
        "post_type": "request",
        "request_type": "group",
        "group_id": 123456,
        "user_id": 10001,
        "comment": "AutoEmailSender",
        "flag": "flag-1",
    }
    if raw_sub_type is not None:
        raw_event["sub_type"] = raw_sub_type
    event = FakeEvent(RawEvent(raw_event))

    with pytest.raises(ValueError, match="missing required group request fields"):
        extract_join_request(event)


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
async def test_set_group_request_uses_platform_manager_get_insts():
    bot = FakeBot()
    context = FakeContext(platforms=[FakePlatform(bot)], use_get_insts=True)

    assert find_onebot_bot(context) is bot

    await set_group_request(
        context,
        flag="flag-1",
        sub_type="add",
        approve=True,
        reason="",
    )

    assert bot.calls == [
        {
            "action": "set_group_add_request",
            "flag": "flag-1",
            "sub_type": "add",
            "approve": True,
            "reason": "",
        }
    ]


@pytest.mark.parametrize(
    ("field_name", "kwargs"),
    [
        ("meta().id", {"meta_id": "onebot-b"}),
        ("metadata.id", {"metadata_id": "onebot-b"}),
        ("id", {"platform_id": "onebot-b"}),
        ('config["id"]', {"config_id": "onebot-b"}),
    ],
)
def test_find_onebot_bot_matches_supported_platform_id_fields(field_name, kwargs):
    first_bot = FakeBot()
    second_bot = FakeBot()
    context = FakeContext(
        platforms=[
            FakePlatform(first_bot, platform_id="onebot-a"),
            FakePlatform(second_bot, **kwargs),
        ]
    )

    assert find_onebot_bot(context, platform_id="onebot-b") is second_bot, field_name


@pytest.mark.asyncio
async def test_set_group_request_selects_matching_platform_id():
    first_bot = FakeBot()
    second_bot = FakeBot()
    context = FakeContext(
        platforms=[
            FakePlatform(first_bot, platform_id="onebot-a"),
            FakePlatform(second_bot, platform_id="onebot-b"),
        ]
    )

    assert find_onebot_bot(context, platform_id="onebot-b") is second_bot

    await set_group_request(
        context,
        platform_id="onebot-b",
        flag="flag-1",
        sub_type="add",
        approve=False,
        reason="答案不符合",
    )

    assert first_bot.calls == []
    assert second_bot.calls == [
        {
            "action": "set_group_add_request",
            "flag": "flag-1",
            "sub_type": "add",
            "approve": False,
            "reason": "答案不符合",
        }
    ]


def test_find_onebot_bot_raises_for_missing_platform_id():
    context = FakeContext(platforms=[FakePlatform(FakeBot(), platform_id="onebot-a")])

    with pytest.raises(PlatformActionError, match="onebot bot api not found"):
        find_onebot_bot(context, platform_id="missing")


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
