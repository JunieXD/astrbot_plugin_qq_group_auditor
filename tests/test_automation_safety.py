import asyncio

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
async def test_invitation_uses_group_delay_and_approval_priority(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0].update(invite_action="approve", review_min_seconds=20, review_max_seconds=25)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    queued = []

    async def guarded(**kwargs):
        queued.append((kwargs["group"], kwargs["priority"], kwargs["delay"], kwargs["gap"]))
        return await kwargs["action"]()

    async def write(*args, **kwargs):
        pass

    monkeypatch.setattr(plugin.guard, "run", guarded)
    monkeypatch.setattr(module, "set_group_request", write)
    event = FakeRequestEvent()
    event.message_obj.raw_message["comment"] = ""
    await plugin.handle_group_request(event)
    assert queued == [("123", 0, (20, 25), (8, 15))]
    assert not plugin.context.llm_calls
    await plugin.terminate()


@pytest.mark.asyncio
async def test_notice_cannot_shorten_configured_account_gap(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["automation_pacing"] = {"action_gap_min_seconds": 30, "action_gap_max_seconds": 40}
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    queued = []

    async def guarded(**kwargs):
        queued.append((kwargs["group"], kwargs["priority"], kwargs["gap"]))

    monkeypatch.setattr(plugin.guard, "run", guarded)
    await plugin._send_notice(plugin.config["group_audits"][0], "test", "bot")
    assert queued == [("123", 2, (30, 40))]
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


@pytest.mark.asyncio
@pytest.mark.parametrize("first_source", ["notice", "reconcile"])
async def test_live_join_and_reconciliation_share_one_card_task(monkeypatch, first_source):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0].update(auto_set_card=True, card_template="{answer}-{nickname}")
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    group = plugin.config["group_audits"][0]
    request = module.JoinRequest("123", "20002", "2030", "card-request", "add",
                                 requested_at=1000, nickname="申请人")
    app_id, _ = plugin.audit_store.record_application(
        platform_id="napcat-1", request=request, question="毕业年份",
        question_source="config", review_prompt="规则",
    )
    plugin.audit_store.record_action(application_id=app_id, kind="platform", action="approve",
        actor_qq="bot", source="plugin", status="succeeded", occurred_at=1001)
    # Both sources may capture this snapshot before either records a membership.
    stale_application = plugin.audit_store.application_for_reconciliation(app_id)
    entered, release = asyncio.Event(), asyncio.Event()
    waits, reads, writes = [], [], []

    async def guarded(**kwargs):
        waits.append(kwargs["delay"])
        entered.set()
        await release.wait()
        return await kwargs["action"]()

    async def member(*args, **kwargs):
        reads.append(kwargs["user_id"])
        return module.GroupMemberInfo(nickname="申请人", card="", join_time=1001)

    async def write(*args, **kwargs):
        writes.append(kwargs["card"])

    async def notice():
        return await plugin._process_member_increase(
            group_config=group, increase=module.GroupMemberIncrease("123", "20002", "bot", "approve", 1001),
            platform_id="napcat-1", member_info=module.GroupMemberInfo("申请人", "", 1001),
            action_source="group_increase",
        )

    async def reconcile():
        return await plugin._reconcile_application_member(app_id, application=stale_application)

    monkeypatch.setattr(plugin.guard, "run", guarded)
    monkeypatch.setattr(module, "get_group_member_info", member)
    monkeypatch.setattr(module, "set_group_card", write)
    sources = {"notice": notice, "reconcile": reconcile}
    first = asyncio.create_task(sources[first_source]())
    await asyncio.wait_for(entered.wait(), 2)
    second = asyncio.create_task(sources["reconcile" if first_source == "notice" else "notice"]())
    await asyncio.sleep(0)
    assert len(waits) == 1
    # Even when reconciliation owns the card lock, the live join must be durable
    # immediately so pending approvals can see that the member already joined.
    pending_detail = plugin.audit_store.detail(group_id="123", application_id=app_id)
    assert len(pending_detail["memberships"]) == 1
    assert pending_detail["memberships"][0]["joined_at"] == 1001
    assert any(a["source"] == "group_increase" and a["status"] == "observed"
               for a in pending_detail["actions"])
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 2)
    await reconcile()
    await notice()
    assert waits == [(30, 90)]
    assert reads == ["20002"]
    assert writes == ["2030-申请人"]
    detail = plugin.audit_store.detail(group_id="123", application_id=app_id)
    assert len(detail["memberships"]) == 1
    assert len(detail["memberships"][0]["card_operations"]) == 1
    assert detail["memberships"][0]["card_operations"][0]["status"] == "succeeded"
    await plugin.terminate()
