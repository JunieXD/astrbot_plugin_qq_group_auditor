from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

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
    def __init__(
        self,
        current_provider_id="provider-current",
        fallback_provider=None,
        current_provider_exception: Exception | None = None,
    ):
        self.llm_calls = []
        self.current_provider_id = current_provider_id
        self.fallback_provider = FakeProvider() if fallback_provider is None else fallback_provider
        self.current_provider_exception = current_provider_exception

    async def get_current_chat_provider_id(self, umo):
        self.current_umo = umo
        if self.current_provider_exception is not None:
            raise self.current_provider_exception
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
        unified_msg_origin: str = "private-umo-1",
    ):
        self.message_str = message
        self.sender_id = sender_id
        self.platform_id = platform_id
        self.unified_msg_origin = unified_msg_origin

    def get_sender_id(self):
        return self.sender_id

    def get_platform_id(self):
        return self.platform_id

    def plain_result(self, text: str):
        return text


class FakeRequestEvent:
    def __init__(self):
        self.unified_msg_origin = "group-request-umo-1"
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


class FakeNoticeEvent:
    def __init__(self, raw_message):
        self.message_obj = types.SimpleNamespace(raw_message=raw_message)

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
    assert module.QQGroupAuditorPlugin.__qgaudit_register__[0][-1] == "0.2.3"
    assert [group.name for group in command_groups] == ["qgaudit"]

    command_meta = getattr(module.QQGroupAuditorPlugin.qgaudit_test, "__qgaudit_filter_meta__", [])
    assert ("command", "qgaudit", "test") in command_meta
    assert ("event_message_type", EventMessageType.PRIVATE_MESSAGE) in command_meta
    backfill_meta = getattr(
        module.QQGroupAuditorPlugin.qgaudit_backfill,
        "__qgaudit_filter_meta__",
        [],
    )
    assert ("command", "qgaudit", "backfill") in backfill_meta
    assert ("event_message_type", EventMessageType.PRIVATE_MESSAGE) in backfill_meta

    handler_meta = getattr(
        module.QQGroupAuditorPlugin.handle_group_request,
        "__qgaudit_filter_meta__",
        [],
    )
    assert ("event_message_type", EventMessageType.ALL) in handler_meta
    assert ("event_message_type", EventMessageType.OTHER_MESSAGE) not in handler_meta
    assert ("platform_adapter_type", PlatformAdapterType.AIOCQHTTP) in handler_meta


def test_imports_when_loaded_as_plugin_package(monkeypatch):
    install_astrbot_stub(monkeypatch)
    plugin_dir = Path(__file__).resolve().parents[1]
    module_name = "astrbot_plugin_qq_group_auditor.main"
    sanitized_path = [
        entry
        for entry in sys.path
        if entry and Path(entry).resolve() != plugin_dir
    ]
    monkeypatch.setattr(sys, "path", sanitized_path)
    for name in list(sys.modules):
        if name == "qq_group_auditor" or name.startswith("qq_group_auditor."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    package = types.ModuleType("astrbot_plugin_qq_group_auditor")
    package.__path__ = [str(plugin_dir)]
    monkeypatch.setitem(sys.modules, "astrbot_plugin_qq_group_auditor", package)
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, plugin_dir / "main.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)

    spec.loader.exec_module(module)

    assert hasattr(module, "QQGroupAuditorPlugin")


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
    assert module.parse_backfill_command("/qgaudit backfill 123") == "123"
    assert module.parse_backfill_command("qgaudit backfill") is None


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
async def test_private_test_command_uses_event_unified_msg_origin_for_provider(
    monkeypatch,
):
    module, _ = import_main(monkeypatch)
    context = FakeContext()
    plugin = module.QQGroupAuditorPlugin(context, plugin_config())
    event = FakeEvent(
        message="qgaudit test 123 alpha",
        sender_id="10001",
        unified_msg_origin="private-umo-42",
    )

    results = await collect(plugin.qgaudit_test(event))

    assert results == ["approve=True reason=符合规则"]
    assert context.current_umo == "private-umo-42"


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
async def test_llm_client_falls_back_when_current_provider_lookup_raises(monkeypatch):
    module, _ = import_main(monkeypatch)
    context = FakeContext(current_provider_exception=RuntimeError("provider lookup failed"))
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
async def test_llm_client_enables_native_json_output_for_deepseek(monkeypatch):
    module, _ = import_main(monkeypatch)
    context = FakeContext(current_provider_id="deepseek/deepseek-v4-flash")
    client = module.AstrBotLLMClient(context, "umo-1")

    result = await client.generate(system_prompt="system json", prompt="prompt")

    assert result == FakeResponse.completion_text
    assert context.llm_calls == [
        {
            "chat_provider_id": "deepseek/deepseek-v4-flash",
            "system_prompt": "system json",
            "prompt": "prompt",
            "response_format": {"type": "json_object"},
            "max_tokens": 512,
        }
    ]


@pytest.mark.asyncio
async def test_llm_client_does_not_send_deepseek_api_options_to_other_sources(monkeypatch):
    module, _ = import_main(monkeypatch)
    provider_id = "siliconflow/deepseek-ai/DeepSeek-V3"
    context = FakeContext(current_provider_id=provider_id)
    client = module.AstrBotLLMClient(context, "umo-1")

    await client.generate(system_prompt="system json", prompt="prompt")

    assert context.llm_calls == [
        {
            "chat_provider_id": provider_id,
            "system_prompt": "system json",
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
async def test_group_request_handler_uses_event_platform_id_for_admin_notice(
    monkeypatch,
):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0]["notify_on_approve"] = True
    context = FakeContext()
    plugin = module.QQGroupAuditorPlugin(context, config)
    notice_platforms = []

    async def fake_set_group_request(*args, **kwargs):
        return None

    async def fake_send_admin_notice(context_arg, admin_qq_ids, text, platform_name="aiocqhttp"):
        notice_platforms.append(platform_name)

    monkeypatch.setattr(module, "set_group_request", fake_set_group_request)
    monkeypatch.setattr(module, "send_admin_notice", fake_send_admin_notice)

    await plugin.handle_group_request(FakeRequestEvent())

    assert notice_platforms == ["napcat-1"]


@pytest.mark.asyncio
async def test_group_request_handler_uses_event_unified_msg_origin_for_provider(
    monkeypatch,
):
    module, _ = import_main(monkeypatch)
    context = FakeContext()
    plugin = module.QQGroupAuditorPlugin(context, plugin_config())

    async def fake_set_group_request(*args, **kwargs):
        return None

    monkeypatch.setattr(module, "set_group_request", fake_set_group_request)

    await plugin.handle_group_request(FakeRequestEvent())

    assert context.current_umo == "group-request-umo-1"


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


@pytest.mark.asyncio
async def test_external_approval_sets_card_and_leave_closes_same_audit_record(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0].update(
        {
            "auto_set_card": True,
            "card_template": "{nickname}-{answer}",
            "application_question": "你从哪里知道本群？",
        }
    )
    context = FakeContext()
    plugin = module.QQGroupAuditorPlugin(context, config)
    cards = []

    async def fake_set_group_request(*args, **kwargs):
        return None

    async def fake_get_group_question(*args, **kwargs):
        return "你从哪里知道本群？"

    async def fake_get_user_nickname(*args, **kwargs):
        return "申请人"

    async def fake_set_group_card(*args, **kwargs):
        cards.append(kwargs)

    monkeypatch.setattr(module, "set_group_request", fake_set_group_request)
    monkeypatch.setattr(module, "get_group_question", fake_get_group_question)
    monkeypatch.setattr(module, "get_user_nickname", fake_get_user_nickname)
    monkeypatch.setattr(module, "set_group_card", fake_set_group_card)
    monkeypatch.setattr(module.time, "time", lambda: 1991)

    request_event = FakeRequestEvent()
    request_event.message_obj.raw_message["time"] = 1900
    request_event.message_obj.raw_message["self_id"] = 99999
    await plugin.handle_group_request(request_event)
    await plugin.handle_group_membership_notice(
        FakeNoticeEvent(
            {
                "post_type": "notice",
                "notice_type": "group_increase",
                "sub_type": "approve",
                "group_id": 123,
                "user_id": 20002,
                "operator_id": 30001,
                "self_id": 99999,
                "time": 2000,
            }
        )
    )
    await plugin.handle_group_membership_notice(
        FakeNoticeEvent(
            {
                "post_type": "notice",
                "notice_type": "group_decrease",
                "sub_type": "kick",
                "group_id": 123,
                "user_id": 20002,
                "operator_id": 30002,
                "self_id": 99999,
                "time": 2100,
            }
        )
    )

    assert cards == [
        {
            "group_id": "123",
            "user_id": "20002",
            "card": "申请人-关键词答案",
            "platform_id": "napcat-1",
        }
    ]
    records = plugin.audit_store.history(group_id="123", applicant_qq="20002")
    assert len(records) == 1
    assert records[0]["question"] == "你从哪里知道本群？"
    assert records[0]["memberships"][0]["join_operator_qq"] == "30001"
    assert records[0]["memberships"][0]["leave_sub_type"] == "kick"
    assert records[0]["memberships"][0]["leave_operator_qq"] == "30002"
    assert records[0]["memberships"][0]["card_operations"][0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_reconcile_treats_external_handled_without_join_as_reject(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    plugin.audit_store.record_application(
        platform_id="napcat-1",
        request=module.JoinRequest(
            group_id="123",
            applicant_qq="20002",
            answer="关键词答案",
            flag="12345",
            sub_type="add",
            requested_at=900,
        ),
        question="问题",
        question_source="config",
        review_prompt="规则",
    )

    async def fake_get_group_system_requests(*args, **kwargs):
        return [
            {
                "request_id": 12345,
                "group_id": 123,
                "invitor_uin": 20002,
                "message": "关键词答案",
                "requester_nick": "申请人",
                "checked": True,
                "actor": 30001,
            }
        ]

    monkeypatch.setattr(module, "get_group_system_requests", fake_get_group_system_requests)
    monkeypatch.setattr(module.time, "time", lambda: 1000)
    await plugin._reconcile_platform("napcat-1")
    monkeypatch.setattr(module.time, "time", lambda: 1120)
    await plugin._reconcile_platform("napcat-1")

    record = plugin.audit_store.history(group_id="123", applicant_qq="20002")[0]
    assert record["actions"][0]["action"] == "reject"
    assert record["actions"][0]["actor_qq"] == "30001"
    assert record["actions"][0]["source"] == "external_inferred"


@pytest.mark.asyncio
async def test_approved_request_sets_card_without_group_increase_notice(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0].update(
        {
            "auto_set_card": True,
            "card_template": "{answer}-{nickname}",
            "application_question": "毕业年份",
        }
    )
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    cards = []

    async def fake_set_group_request(*args, **kwargs):
        return None

    async def fake_get_group_question(*args, **kwargs):
        return "毕业年份"

    async def fake_get_user_nickname(*args, **kwargs):
        return "申请人"

    async def fake_set_group_card(*args, **kwargs):
        cards.append(kwargs)

    monkeypatch.setattr(module, "set_group_request", fake_set_group_request)
    monkeypatch.setattr(module, "get_group_question", fake_get_group_question)
    monkeypatch.setattr(module, "get_user_nickname", fake_get_user_nickname)
    monkeypatch.setattr(module, "set_group_card", fake_set_group_card)
    event = FakeRequestEvent()
    event.message_obj.raw_message.update(
        {
            "comment": "问题：毕业年份\n答案：2028",
            "time": 1990,
            "self_id": 99999,
        }
    )

    await plugin.handle_group_request(event)

    assert cards == [
        {
            "group_id": "123",
            "user_id": "20002",
            "card": "2028-申请人",
            "platform_id": "napcat-1",
        }
    ]
    record = plugin.audit_store.history(group_id="123", applicant_qq="20002")[0]
    assert record["answer"] == "2028"
    assert record["raw_comment"] == "问题：毕业年份\n答案：2028"
    assert len(record["memberships"]) == 1
    assert record["memberships"][0]["card_operations"][0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_backfill_updates_historical_card_once_and_is_rerunnable(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0].update(
        {
            "auto_set_card": True,
            "card_template": "{answer}-{nickname}",
            "application_question": "毕业年份",
        }
    )
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    application_id, _ = plugin.audit_store.record_application(
        platform_id="napcat-1",
        request=module.JoinRequest(
            group_id="123",
            applicant_qq="20002",
            answer="问题：毕业年份\n答案：2028",
            raw_comment="问题：毕业年份\n答案：2028",
            flag="historical-1",
            sub_type="add",
            requested_at=1900,
            self_id="99999",
            nickname="申请人",
        ),
        question="毕业年份",
        question_source="config",
        review_prompt="规则",
    )
    plugin.audit_store.record_action(
        application_id=application_id,
        kind="platform",
        action="approve",
        actor_qq="30001",
        source="plugin",
        status="succeeded",
        occurred_at=1901,
    )
    cards = []

    async def fake_set_group_card(*args, **kwargs):
        cards.append(kwargs)

    monkeypatch.setattr(module, "set_group_card", fake_set_group_card)
    event = FakeEvent(message="qgaudit backfill 123")

    first = await collect(plugin.qgaudit_backfill(event))
    second = await collect(plugin.qgaudit_backfill(event))

    assert len(cards) == 1
    assert cards[0]["card"] == "2028-申请人"
    assert "本次修改成功：1" in first[0]
    assert "此前已经处理：1" in second[0]
    record = plugin.audit_store.detail(group_id="123", application_id=application_id)
    assert record["answer"] == "2028"
    assert len(record["memberships"]) == 1
    assert len(record["memberships"][0]["card_operations"]) == 1


@pytest.mark.asyncio
async def test_backfill_direct_failure_is_retriable_without_false_membership(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0].update(
        {"auto_set_card": True, "card_template": "{answer}-{nickname}"}
    )
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    application_id, _ = plugin.audit_store.record_application(
        platform_id="napcat-1",
        request=module.JoinRequest(
            group_id="123",
            applicant_qq="20002",
            answer="2028",
            flag="historical-failure",
            sub_type="add",
            requested_at=1900,
            nickname="申请人",
        ),
        question="毕业年份",
        question_source="config",
        review_prompt="规则",
    )
    plugin.audit_store.record_action(
        application_id=application_id,
        kind="platform",
        action="approve",
        actor_qq="30001",
        source="plugin",
        status="succeeded",
        occurred_at=1901,
    )
    attempts = 0

    async def flaky_set_group_card(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise module.PlatformActionError("member is not in group")

    monkeypatch.setattr(module, "set_group_card", flaky_set_group_card)
    event = FakeEvent(message="qgaudit backfill 123")

    first = await collect(plugin.qgaudit_backfill(event))
    assert "当前不在群内：1" in first[0]
    assert plugin.audit_store.detail(
        group_id="123", application_id=application_id
    )["memberships"] == []

    second = await collect(plugin.qgaudit_backfill(event))
    assert "本次修改成功：1" in second[0]
    assert attempts == 2
    assert len(
        plugin.audit_store.detail(group_id="123", application_id=application_id)[
            "memberships"
        ]
    ) == 1


@pytest.mark.asyncio
async def test_missing_group_member_is_not_reported_as_card_failure(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0].update(
        {"auto_set_card": True, "card_template": "{answer}"}
    )
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    application_id, _ = plugin.audit_store.record_application(
        platform_id="napcat-1",
        request=module.JoinRequest(
            group_id="123",
            applicant_qq="20002",
            answer="2029",
            flag="not-joined",
            sub_type="add",
            requested_at=1900,
        ),
        question="毕业年份",
        question_source="config",
        review_prompt="规则",
    )
    plugin.audit_store.record_action(
        application_id=application_id,
        kind="platform",
        action="approve",
        actor_qq="99999",
        source="plugin",
        status="succeeded",
        occurred_at=1901,
    )
    notices = []

    async def missing_member(*args, **kwargs):
        raise module.PlatformActionError(
            "set_group_card failed: 群(123)成员20002不存在"
        )

    async def fake_notify(*args, **kwargs):
        notices.append((args, kwargs))

    monkeypatch.setattr(module, "set_group_card", missing_member)
    monkeypatch.setattr(plugin, "_notify_card_error", fake_notify)
    result = await plugin._set_card_from_application(
        group_config=module.find_group_config(plugin.config, "123"),
        application=plugin.audit_store.application_for_reconciliation(application_id),
        action_source="member_reconcile_direct",
        notify_error=True,
    )

    assert result == "not_in_group"
    assert notices == []
    assert plugin.audit_store.detail(
        group_id="123", application_id=application_id
    )["memberships"] == []


@pytest.mark.asyncio
async def test_approved_request_sets_card_without_member_cache_api(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0].update(
        {
            "auto_set_card": True,
            "card_template": "{answer}-{nickname}",
            "application_question": "毕业年份",
        }
    )
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    cards = []

    async def fake_set_group_request(*args, **kwargs):
        return None

    async def fake_get_group_question(*args, **kwargs):
        return "毕业年份"

    async def fake_get_user_nickname(*args, **kwargs):
        return "申请人"

    async def fake_set_group_card(*args, **kwargs):
        cards.append(kwargs)

    monkeypatch.setattr(module, "set_group_request", fake_set_group_request)
    monkeypatch.setattr(module, "get_group_question", fake_get_group_question)
    monkeypatch.setattr(module, "get_user_nickname", fake_get_user_nickname)
    monkeypatch.setattr(module, "set_group_card", fake_set_group_card)
    event = FakeRequestEvent()
    event.message_obj.raw_message.update(
        {
            "comment": "问题：毕业年份\n答案：2028",
            "time": 1990,
            "self_id": 99999,
        }
    )

    await plugin.handle_group_request(event)

    assert cards == [
        {
            "group_id": "123",
            "user_id": "20002",
            "card": "2028-申请人",
            "platform_id": "napcat-1",
        }
    ]
    record = plugin.audit_store.history(group_id="123", applicant_qq="20002")[0]
    assert record["nickname"] == "申请人"
    assert record["memberships"][0]["card_operations"][0]["status"] == "succeeded"
    assert any(
        action["reason"] == "通过群名片设置接口确认已入群"
        for action in record["actions"]
    )


@pytest.mark.asyncio
async def test_reconcile_confirms_external_approval_before_inferring_rejection(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0].update(
        {
            "auto_set_card": True,
            "card_template": "{answer}",
            "application_question": "毕业年份",
        }
    )
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    cards = []

    async def fake_get_group_system_requests(*args, **kwargs):
        return [
            {
                "request_id": 12345,
                "group_id": 123,
                "requester_uin": 20002,
                "message": "问题：毕业年份\n答案：2028",
                "requester_nick": "申请人",
                "request_time": 900,
                "checked": True,
                "actor": 30001,
            }
        ]

    async def fake_set_group_card(*args, **kwargs):
        cards.append(kwargs)

    monkeypatch.setattr(module, "get_group_system_requests", fake_get_group_system_requests)
    monkeypatch.setattr(module, "set_group_card", fake_set_group_card)
    monkeypatch.setattr(module.time, "time", lambda: 1100)

    await plugin._reconcile_platform("napcat-1")

    record = plugin.audit_store.history(group_id="123", applicant_qq="20002")[0]
    platform_actions = [
        action for action in record["actions"] if action["kind"] == "platform"
    ]
    assert cards[0]["card"] == "2028"
    assert any(action["action"] == "approve" for action in platform_actions)
    assert not any(action["action"] == "reject" for action in platform_actions)
