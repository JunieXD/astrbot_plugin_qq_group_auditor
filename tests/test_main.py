from __future__ import annotations

import importlib
import sys
import types

import pytest


class MessageChain:
    def __init__(self, chain=None):
        self.chain = list(chain or [])

    def message(self, text: str):
        self.chain.append(text)
        return self

    def get_plain_text(self) -> str:
        return "".join(str(item) for item in self.chain)


class EventMessageType:
    ALL = "ALL"
    GROUP_MESSAGE = "GROUP_MESSAGE"
    PRIVATE_MESSAGE = "PRIVATE_MESSAGE"
    OTHER_MESSAGE = "OTHER_MESSAGE"


class PlatformAdapterType:
    AIOCQHTTP = "aiocqhttp"


class CommandGroup:
    def __init__(self, name: str):
        self.name = name

    def command(self, name: str):
        def decorator(func):
            func.__qgaudit_filter_meta__ = getattr(func, "__qgaudit_filter_meta__", [])
            func.__qgaudit_filter_meta__.append(("command", self.name, name))
            return func

        return decorator


def install_astrbot_stub(monkeypatch: pytest.MonkeyPatch):
    command_groups: list[CommandGroup] = []

    def command_group(name: str):
        def decorator(func):
            group = CommandGroup(name)
            group.func = func
            command_groups.append(group)
            func.__qgaudit_filter_meta__ = getattr(func, "__qgaudit_filter_meta__", [])
            func.__qgaudit_filter_meta__.append(("command_group", name))
            return group

        return decorator

    def event_message_type(event_type):
        def decorator(func):
            func.__qgaudit_filter_meta__ = getattr(func, "__qgaudit_filter_meta__", [])
            func.__qgaudit_filter_meta__.append(("event_message_type", event_type))
            return func

        return decorator

    def platform_adapter_type(adapter_type):
        def decorator(func):
            func.__qgaudit_filter_meta__ = getattr(func, "__qgaudit_filter_meta__", [])
            func.__qgaudit_filter_meta__.append(("platform_adapter_type", adapter_type))
            return func

        return decorator

    filter_module = types.ModuleType("astrbot.api.event.filter")
    filter_module.EventMessageType = EventMessageType
    filter_module.PlatformAdapterType = PlatformAdapterType
    filter_module.command_group = command_group
    filter_module.event_message_type = event_message_type
    filter_module.platform_adapter_type = platform_adapter_type

    event_module = types.ModuleType("astrbot.api.event")
    event_module.MessageChain = MessageChain
    event_module.filter = filter_module

    class Star:
        def __init__(self, context=None, config=None):
            self.context = context
            self.config = config

    def register(*args, **kwargs):
        def decorator(cls):
            cls.__qgaudit_register__ = (args, kwargs)
            return cls

        return decorator

    star_module = types.ModuleType("astrbot.api.star")
    star_module.Context = object
    star_module.Star = Star
    star_module.register = register

    api_module = types.ModuleType("astrbot.api")
    api_module.event = event_module
    api_module.star = star_module

    astrbot_module = types.ModuleType("astrbot")
    astrbot_module.api = api_module

    monkeypatch.setitem(sys.modules, "astrbot", astrbot_module)
    monkeypatch.setitem(sys.modules, "astrbot.api", api_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.event.filter", filter_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.star", star_module)
    sys.modules.pop("main", None)
    return command_groups


def import_main(monkeypatch: pytest.MonkeyPatch):
    command_groups = install_astrbot_stub(monkeypatch)
    module = importlib.import_module("main")
    return module, command_groups


class FakeResponse:
    completion_text = '{"approve": true, "reason": "符合规则"}'


class FakeProviderMeta:
    id = "provider-fallback"


class FakeProvider:
    def meta(self):
        return FakeProviderMeta()


class FakeContext:
    def __init__(self, current_provider_id="provider-current", fallback_provider=None):
        self.llm_calls = []
        self.current_provider_id = current_provider_id
        self.fallback_provider = FakeProvider() if fallback_provider is None else fallback_provider

    async def get_current_chat_provider_id(self, umo):
        self.current_umo = umo
        return self.current_provider_id

    def get_using_provider(self, value):
        self.fallback_arg = value
        return self.fallback_provider

    async def llm_generate(self, **kwargs):
        self.llm_calls.append(kwargs)
        return FakeResponse()


class FakeEvent:
    def __init__(
        self,
        *,
        message: str,
        sender_id: str = "10001",
        platform_id: str = "napcat-1",
    ):
        self.message_str = message
        self.sender_id = sender_id
        self.platform_id = platform_id

    def get_sender_id(self):
        return self.sender_id

    def get_platform_id(self):
        return self.platform_id

    def plain_result(self, text: str):
        return text


class FakeRequestEvent:
    def __init__(self):
        self.message_obj = types.SimpleNamespace(
            raw_message={
                "post_type": "request",
                "request_type": "group",
                "sub_type": "add",
                "group_id": 123,
                "user_id": 20002,
                "flag": "flag-1",
                "comment": "关键词答案",
            }
        )

    def get_platform_id(self):
        return "napcat-1"


def plugin_config():
    return {
        "group_audits": [
            {
                "group_id": "123",
                "enabled": True,
                "review_prompt": "答案必须包含关键词",
                "admin_qq_ids": ["10001"],
            }
        ]
    }


async def collect(async_iterable):
    return [item async for item in async_iterable]


def test_import_registers_qgaudit_group_and_all_request_handler(monkeypatch):
    module, command_groups = import_main(monkeypatch)

    assert hasattr(module, "QQGroupAuditorPlugin")
    assert [group.name for group in command_groups] == ["qgaudit"]

    command_meta = getattr(module.QQGroupAuditorPlugin.qgaudit_test, "__qgaudit_filter_meta__", [])
    assert ("command", "qgaudit", "test") in command_meta
    assert ("event_message_type", EventMessageType.PRIVATE_MESSAGE) in command_meta

    handler_meta = getattr(
        module.QQGroupAuditorPlugin.handle_group_request,
        "__qgaudit_filter_meta__",
        [],
    )
    assert ("event_message_type", EventMessageType.ALL) in handler_meta
    assert ("event_message_type", EventMessageType.OTHER_MESSAGE) not in handler_meta
    assert ("platform_adapter_type", PlatformAdapterType.AIOCQHTTP) in handler_meta


def test_parse_test_command_accepts_optional_slash_and_preserves_answer_spaces(monkeypatch):
    module, _ = import_main(monkeypatch)

    assert module.parse_test_command("/qgaudit test 123 答案  保留 空格") == (
        "123",
        "答案  保留 空格",
    )
    assert module.parse_test_command("qgaudit test 123 答案  保留 空格") == (
        "123",
        "答案  保留 空格",
    )
    assert module.parse_test_command("qgaudit test 123") is None


@pytest.mark.asyncio
async def test_private_test_command_requires_group_admin(monkeypatch):
    module, _ = import_main(monkeypatch)
    context = FakeContext()
    plugin = module.QQGroupAuditorPlugin(context, plugin_config())
    event = FakeEvent(message="qgaudit test 123 任何答案", sender_id="20002")

    results = await collect(plugin.qgaudit_test(event))

    assert results == ["无权限"]
    assert context.llm_calls == []


@pytest.mark.asyncio
async def test_private_test_command_reviews_with_current_provider_and_keeps_answer_spaces(
    monkeypatch,
):
    module, _ = import_main(monkeypatch)
    context = FakeContext()
    plugin = module.QQGroupAuditorPlugin(context, plugin_config())
    event = FakeEvent(message="qgaudit test 123 alpha  beta", sender_id="10001")

    results = await collect(plugin.qgaudit_test(event))

    assert results == ["approve=True reason=符合规则"]
    assert context.llm_calls[0]["chat_provider_id"] == "provider-current"
    assert "alpha  beta" in context.llm_calls[0]["prompt"]


@pytest.mark.asyncio
async def test_llm_client_falls_back_to_using_provider_meta_id(monkeypatch):
    module, _ = import_main(monkeypatch)
    context = FakeContext(current_provider_id="")
    client = module.AstrBotLLMClient(context, "umo-1")

    result = await client.generate(system_prompt="system", prompt="prompt")

    assert result == FakeResponse.completion_text
    assert context.current_umo == "umo-1"
    assert context.fallback_arg is None
    assert context.llm_calls == [
        {
            "chat_provider_id": "provider-fallback",
            "system_prompt": "system",
            "prompt": "prompt",
        }
    ]


@pytest.mark.asyncio
async def test_llm_client_raises_when_provider_is_missing(monkeypatch):
    module, _ = import_main(monkeypatch)
    context = FakeContext(current_provider_id="", fallback_provider=None)
    context.fallback_provider = None
    client = module.AstrBotLLMClient(context, "umo-1")

    with pytest.raises(RuntimeError, match="Provider not found"):
        await client.generate(system_prompt="system", prompt="prompt")

    assert context.llm_calls == []


@pytest.mark.asyncio
async def test_group_request_handler_uses_raw_request_event_and_platform_id(
    monkeypatch,
):
    module, _ = import_main(monkeypatch)
    context = FakeContext()
    plugin = module.QQGroupAuditorPlugin(context, plugin_config())
    platform_calls = []

    async def fake_set_group_request(context_arg, **kwargs):
        platform_calls.append(kwargs)

    monkeypatch.setattr(module, "set_group_request", fake_set_group_request)

    await plugin.handle_group_request(FakeRequestEvent())

    assert platform_calls == [
        {
            "flag": "flag-1",
            "sub_type": "add",
            "approve": True,
            "reason": "",
            "platform_id": "napcat-1",
        }
    ]


@pytest.mark.asyncio
async def test_runtime_notifier_logs_warning_when_send_admin_notice_fails(monkeypatch):
    module, _ = import_main(monkeypatch)
    warnings = []

    class StubLogger:
        def warning(self, msg, *args, **kwargs):
            warnings.append((msg, kwargs.get("exc_info")))

    async def failing_send_admin_notice(*args, **kwargs):
        raise RuntimeError("send failed")

    monkeypatch.setattr(module, "logger", StubLogger())
    monkeypatch.setattr(module, "send_admin_notice", failing_send_admin_notice)
    notifier = module.RuntimeNotifier(FakeContext())

    await notifier.notify(
        group_config={"admin_qq_ids": ["10001"]},
        request=module.JoinRequest(
            group_id="123",
            applicant_qq="20002",
            answer="答案",
            flag="flag-1",
            sub_type="add",
        ),
        title="加群审核通过",
        action="approve",
        reason="符合",
    )

    assert warnings == [("failed to send audit notification", True)]
