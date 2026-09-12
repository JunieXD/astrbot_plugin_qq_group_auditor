import pytest

from test_main import FakeContext, FakeRequestEvent, import_main, plugin_config


@pytest.mark.asyncio
async def test_invitation_keeps_policy_but_uses_configured_delay(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0]["invite_action"] = "approve"
    config["automation_pacing"] = {"review_min_seconds": 22, "review_max_seconds": 33}
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    calls = []
    async def guarded(**kwargs):
        calls.append((kwargs["account"], kwargs["delay"]))
        return await kwargs["action"]()
    async def write(*args, **kwargs):
        calls.append(kwargs["approve"])
    monkeypatch.setattr(plugin.guard, "run", guarded)
    monkeypatch.setattr(module, "set_group_request", write)
    event = FakeRequestEvent()
    event.message_obj.raw_message["comment"] = ""
    await plugin.handle_group_request(event)
    assert calls == [("napcat-1", (22, 33)), True]
    assert not plugin.context.llm_calls
    await plugin.terminate()


@pytest.mark.asyncio
async def test_existing_card_is_rechecked_after_wait_and_preserved(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0]["auto_set_card"] = True
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    calls = []
    async def read(*args, **kwargs):
        calls.append("read")
        return module.GroupMemberInfo(nickname="name", card="人工设置", join_time=100)
    async def forbidden(*args, **kwargs):
        pytest.fail("must preserve card changed while waiting")
    async def guarded(**kwargs):
        calls.append(kwargs["delay"])
        return await kwargs["action"]()
    monkeypatch.setattr(module, "get_group_member_info", read)
    monkeypatch.setattr(module, "set_group_card", forbidden)
    monkeypatch.setattr(plugin.guard, "run", guarded)
    member = await plugin._write_card_if_empty(group_id="123", user_id="456", card="new", platform_id="bot")
    assert member.card == "人工设置"
    assert calls == [(30, 90), "read"]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_missing_member_never_uses_card_write_as_probe(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0]["auto_set_card"] = True
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    async def missing(*args, **kwargs):
        raise module.PlatformActionError("member not found")
    async def forbidden(*args, **kwargs):
        pytest.fail("member existence must be established before writing")
    monkeypatch.setattr(module, "get_group_member_info", missing)
    monkeypatch.setattr(module, "set_group_card", forbidden)
    with pytest.raises(module.PlatformActionError):
        await plugin._write_card_if_empty(group_id="123", user_id="456", card="new", platform_id="bot")
    await plugin.terminate()


@pytest.mark.asyncio
async def test_cached_question_and_duplicate_request_do_not_repeat_lookup(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    reads = []
    async def question(*args, **kwargs):
        reads.append(True)
        return "question"
    async def write(*args, **kwargs):
        pass
    monkeypatch.setattr(module, "get_group_question", question)
    monkeypatch.setattr(module, "set_group_request", write)
    event = FakeRequestEvent()
    await plugin.handle_group_request(event)
    await plugin.handle_group_request(event)
    event.message_obj.raw_message["flag"] = "second"
    await plugin.handle_group_request(event)
    assert reads == [True]
    await plugin.terminate()
