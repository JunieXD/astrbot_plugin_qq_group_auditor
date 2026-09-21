import asyncio
import importlib.util

import pytest

from qq_group_auditor.audit_store import AuditStore
from qq_group_auditor.models import GroupMemberDecrease, GroupMemberIncrease
from qq_group_auditor import pacing
from test_main import FakeContext, FakeRequestEvent, FakeResponse, import_main, plugin_config


def event(flag, timestamp=1000, comment="关键词答案"):
    value = FakeRequestEvent()
    value.message_obj.raw_message.update(flag=flag, time=timestamp, comment=comment)
    return value


def record_for(plugin, flag):
    app_id = plugin.audit_store.get_application_id_by_flag(
        platform_id="napcat-1", self_id="", flag=flag,
    )
    return plugin.audit_store.detail(group_id="123", application_id=app_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("comment", ["", "关键词答案"])
async def test_new_flag_supersedes_request_queued_for_approval(monkeypatch, comment):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0]["invite_action"] = "approve"
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def guarded(**kwargs):
        if kwargs.get("key", "").endswith(":old"):
            entered.set()
            await release.wait()
        return await kwargs["action"]()

    async def write(*args, **kwargs):
        calls.append(kwargs["flag"])

    monkeypatch.setattr(plugin.guard, "run", guarded)
    monkeypatch.setattr(module, "set_group_request", write)
    old = asyncio.create_task(plugin.handle_group_request(event("old", comment=comment)))
    await asyncio.wait_for(entered.wait(), 2)
    await plugin.handle_group_request(event("new", 1001, comment))
    release.set()
    await asyncio.wait_for(old, 2)
    assert calls == ["new"]
    assert [a["action"] for a in record_for(plugin, "old")["actions"]] == ["superseded"]
    await plugin.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["approve", "ignore", "llm_error"])
async def test_new_request_during_llm_discards_old_result_and_notification(monkeypatch, outcome):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    entered, release = asyncio.Event(), asyncio.Event()
    writes, notices = [], []
    original = plugin.context.llm_generate

    async def llm(**kwargs):
        if "old answer" in kwargs["prompt"]:
            entered.set()
            await release.wait()
            if outcome == "llm_error":
                raise RuntimeError("provider error")
            if outcome == "ignore":
                return type("Response", (), {"completion_text": '{"approve":false,"reason":"不符合"}'})()
        return await original(**kwargs)

    async def write(*args, **kwargs):
        writes.append(kwargs["flag"])

    async def notice(*args, **kwargs):
        notices.append(args)

    monkeypatch.setattr(plugin.context, "llm_generate", llm)
    monkeypatch.setattr(module, "set_group_request", write)
    monkeypatch.setattr(module, "send_admin_notice", notice)
    old = asyncio.create_task(plugin.handle_group_request(event("old", comment="old answer")))
    await asyncio.wait_for(entered.wait(), 2)
    await plugin.handle_group_request(event("new", 1001))
    release.set()
    await asyncio.wait_for(old, 2)
    assert writes == ["new"]
    assert not notices
    assert [a["action"] for a in record_for(plugin, "old")["actions"]] == ["superseded"]
    await plugin.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("new_flag,old_flag,old_time", [
    ("200", "100", 1000),
    ("1789989223097623", "1789989219253793", 1002),
])
async def test_out_of_order_numeric_flags_survive_reload(monkeypatch, tmp_path, new_flag, old_flag, old_time):
    module, _ = import_main(monkeypatch)
    monkeypatch.setattr(module, "_audit_database_path", lambda: tmp_path / "audit.sqlite3")
    writes = []

    async def write(*args, **kwargs):
        writes.append(kwargs["flag"])

    monkeypatch.setattr(module, "set_group_request", write)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    await plugin.handle_group_request(event(new_flag))
    await plugin.terminate()
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    await plugin.handle_group_request(event(old_flag, old_time))
    await plugin.handle_group_request(event(new_flag))
    assert writes == [new_flag]
    assert not plugin.context.llm_calls
    assert record_for(plugin, old_flag)["actions"][0]["action"] == "superseded"
    await plugin.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("handled", ["external", "join_other_flag"])
async def test_manual_processing_during_delay_finishes_old_request(monkeypatch, handled):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())

    async def guarded(**kwargs):
        old_id = record_for(plugin, "old")["id"]
        if handled == "external":
            plugin.audit_store.mark_external_checked(application_id=old_id, actor_qq="admin", observed_at=1001)
        else:
            other = module.JoinRequest("123", "20002", "answer", "previous", "add", requested_at=900)
            other_id, _ = plugin.audit_store.record_application(
                platform_id="napcat-1", request=other, question="", question_source="unknown", review_prompt="",
            )
            plugin.audit_store.record_join(
                platform_id="napcat-1", event=GroupMemberIncrease("123", "20002", "admin", "invite", 1001),
                application_id_hint=other_id,
            )
        return await kwargs["action"]()

    async def forbidden(*args, **kwargs):
        pytest.fail("handled request must not call approval API")

    monkeypatch.setattr(plugin.guard, "run", guarded)
    monkeypatch.setattr(module, "set_group_request", forbidden)
    await plugin.handle_group_request(event("old"))
    assert record_for(plugin, "old")["actions"][0]["action"] == "handled"
    await plugin.terminate()


@pytest.mark.asyncio
async def test_rejoin_after_leave_is_a_fresh_application(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    plugin.audit_store.record_join(
        platform_id="napcat-1", event=GroupMemberIncrease("123", "20002", "admin", "invite", 900),
    )
    plugin.audit_store.record_leave(
        platform_id="napcat-1", event=GroupMemberDecrease("123", "20002", "20002", "leave", 950),
    )
    writes = []

    async def write(*args, **kwargs):
        writes.append(kwargs["flag"])

    monkeypatch.setattr(module, "set_group_request", write)
    await plugin.handle_group_request(event("new"))
    assert writes == ["new"]
    await plugin.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_missing_request_is_terminal_without_cooldown_but_real_errors_remain_failures(monkeypatch, tmp_path, missing):
    module, _ = import_main(monkeypatch)
    monkeypatch.setattr(module, "_audit_database_path", lambda: tmp_path / "audit.sqlite3")
    # Use a separate plugin's copy: exception identities differ across modules.
    spec = importlib.util.spec_from_file_location("foreign_guard", pacing.__file__)
    other = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(other)
    guard = other.ActionGuard(tmp_path / "guard.json")
    monkeypatch.setattr(module, "get_guard", lambda *args: guard)
    config = plugin_config()
    config["automation_pacing"] = {f"{name}_{bound}_seconds": 0
                                   for name in ("review", "action_gap", "notice") for bound in ("min", "max")}
    calls, notices = [], []

    async def write(*args, **kwargs):
        calls.append(kwargs["flag"])
        if missing:
            raise module.RequestUnavailableError("request merged")
        raise module.PlatformActionError("connection reset")

    async def notice(*args, **kwargs):
        notices.append(args)

    monkeypatch.setattr(module, "set_group_request", write)
    monkeypatch.setattr(module, "send_admin_notice", notice)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    # Stub sending only, retaining real guard failure counting for approvals.
    monkeypatch.setattr(plugin, "_send_notice", notice)
    await plugin.handle_group_request(event("old"))
    state = guard.accounts["napcat-1"]
    if missing:
        assert state.get("failures", 0) == 0
        assert state.get("paused_until", 0) == 0
        assert state["operations"] == {}
        assert not notices
        assert record_for(plugin, "old")["actions"][0]["action"] == "unavailable"
    else:
        assert state["failures"] == 1
        assert list(state["operations"].values())[0]["status"] == "unknown"
        assert notices
        assert record_for(plugin, "old")["actions"][-1]["status"] == "failed"
    await plugin.terminate()
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    await plugin.handle_group_request(event("old"))
    assert calls == ["old"]
    await plugin.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [True, False])
async def test_sync_persists_all_requests_before_review(monkeypatch, reverse):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    writes = []
    items = [dict(request_id=flag, group_id=123, requester_uin=20002, message="答案", request_time=ts, checked=False)
             for flag, ts in ((111, 1000), (222, 1001))]

    async def requests(*args, **kwargs):
        return list(reversed(items)) if reverse else items

    async def write(*args, **kwargs):
        writes.append(kwargs["flag"])

    monkeypatch.setattr(module, "get_group_system_requests", requests)
    monkeypatch.setattr(module, "set_group_request", write)
    await plugin._reconcile_platform("napcat-1")
    assert writes == ["222"]
    assert len(plugin.context.llm_calls) == 1
    assert record_for(plugin, "111")["actions"][0]["action"] == "superseded"
    await plugin.terminate()


@pytest.mark.asyncio
async def test_same_flag_invite_enrichment_requires_llm_before_approval(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0]["invite_action"] = "approve"
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    entered, release = asyncio.Event(), asyncio.Event()
    first = True
    calls = []

    async def guarded(**kwargs):
        nonlocal first
        if first:
            first = False
            entered.set()
            await release.wait()
        return await kwargs["action"]()

    async def write(*args, **kwargs):
        calls.append(kwargs["approve"])

    monkeypatch.setattr(plugin.guard, "run", guarded)
    monkeypatch.setattr(module, "set_group_request", write)
    old = asyncio.create_task(plugin.handle_group_request(event("same", comment="")))
    await asyncio.wait_for(entered.wait(), 2)
    fresh = asyncio.create_task(plugin.handle_group_request(event("same", comment="关键词答案")))
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(asyncio.gather(old, fresh), 2)
    assert len(plugin.context.llm_calls) == 1
    assert calls == [True]
    assert record_for(plugin, "same")["request_kind"] == "application"
    await plugin.terminate()


@pytest.mark.asyncio
async def test_stale_ignore_notice_is_checked_after_notice_delay(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0]["notify_on_ignore"] = True
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    entered, release = asyncio.Event(), asyncio.Event()
    notices = []

    async def llm(**kwargs):
        if "old answer" in kwargs["prompt"]:
            return type("Response", (), {"completion_text": '{"approve":false,"reason":"不符合"}'})()
        return FakeResponse()

    async def guarded(**kwargs):
        if kwargs.get("key", "").startswith("notice:"):
            entered.set()
            await release.wait()
        return await kwargs["action"]()

    async def write(*args, **kwargs):
        pass

    async def notice(*args, **kwargs):
        notices.append(args)

    monkeypatch.setattr(plugin.context, "llm_generate", llm)
    monkeypatch.setattr(plugin.guard, "run", guarded)
    monkeypatch.setattr(module, "set_group_request", write)
    monkeypatch.setattr(module, "send_admin_notice", notice)
    old = asyncio.create_task(plugin.handle_group_request(event("old", comment="old answer")))
    await asyncio.wait_for(entered.wait(), 2)
    await plugin.handle_group_request(event("new", 1001))
    release.set()
    await asyncio.wait_for(old, 2)
    assert not notices
    assert [a["action"] for a in record_for(plugin, "old")["actions"]] == ["superseded"]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_new_request_is_registered_before_slow_enrichment(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0]["invite_action"] = "approve"
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    entered, release = asyncio.Event(), asyncio.Event()
    lookup_entered, lookup_release = asyncio.Event(), asyncio.Event()
    writes = []

    async def guarded(**kwargs):
        if kwargs.get("key", "").endswith(":old"):
            entered.set()
            await release.wait()
        return await kwargs["action"]()

    async def question(*args, **kwargs):
        lookup_entered.set()
        await lookup_release.wait()
        return "问题"

    async def write(*args, **kwargs):
        writes.append(kwargs["flag"])

    monkeypatch.setattr(plugin.guard, "run", guarded)
    monkeypatch.setattr(module, "set_group_request", write)
    old = asyncio.create_task(plugin.handle_group_request(event("old", comment="")))
    await asyncio.wait_for(entered.wait(), 2)
    plugin._lookup_cache.clear()
    monkeypatch.setattr(module, "get_group_question", question)
    new = asyncio.create_task(plugin.handle_group_request(event("new", 1001, "")))
    await asyncio.wait_for(lookup_entered.wait(), 2)
    release.set()
    await asyncio.wait_for(old, 2)
    assert not writes
    lookup_release.set()
    await asyncio.wait_for(new, 2)
    assert writes == ["new"]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_approval_is_durable_before_notice_and_blocks_another_queued_flag(monkeypatch, tmp_path):
    module, _ = import_main(monkeypatch)
    monkeypatch.setattr(module, "_audit_database_path", lambda: tmp_path / "audit.sqlite3")
    monkeypatch.setattr(module.time, "time", lambda: 1000)
    config = plugin_config()
    config["group_audits"][0]["notify_on_approve"] = True
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    entered, release = asyncio.Event(), asyncio.Event()
    notifying, notice_release = asyncio.Event(), asyncio.Event()
    account_lock = asyncio.Lock()
    writes = []

    async def guarded(**kwargs):
        async with account_lock:
            return await kwargs["action"]()

    async def write(*args, **kwargs):
        writes.append(kwargs["flag"])
        entered.set()
        await release.wait()

    async def notice(*args, **kwargs):
        notifying.set()
        await notice_release.wait()

    monkeypatch.setattr(plugin.guard, "run", guarded)
    monkeypatch.setattr(module, "set_group_request", write)
    monkeypatch.setattr(plugin, "_send_notice", notice)
    old = asyncio.create_task(plugin.handle_group_request(event("old", 900)))
    await asyncio.wait_for(entered.wait(), 2)
    fresh = asyncio.create_task(plugin.handle_group_request(event("new", 999)))
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(notifying.wait(), 2)
    await asyncio.wait_for(fresh, 2)
    assert writes == ["old"]
    assert record_for(plugin, "old")["actions"][0]["status"] == "succeeded"
    assert record_for(plugin, "new")["actions"][0]["action"] == "handled"
    # Simulate a reload interrupting the notice after approval succeeded.
    old.cancel()
    await asyncio.gather(old, return_exceptions=True)
    await plugin.terminate()
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    await plugin.handle_group_request(event("old", 900))
    assert writes == ["old"]
    assert not plugin.context.llm_calls
    await plugin.terminate()


@pytest.mark.parametrize("different", ["platform", "account", "group", "applicant"])
def test_new_requests_do_not_supersede_other_scopes(tmp_path, different):
    from dataclasses import replace
    from qq_group_auditor.models import JoinRequest

    store = AuditStore(tmp_path / "audit.sqlite3")
    old = JoinRequest("123", "20002", "answer", "old", "add", requested_at=1000, self_id="bot-1")
    new = replace(old, flag="new", requested_at=1001)
    fields = {"account": {"self_id": "bot-2"}, "group": {"group_id": "456"}, "applicant": {"applicant_qq": "20003"}}
    new = replace(new, **fields.get(different, {}))
    options = dict(question="", question_source="unknown", review_prompt="")
    old_id, _ = store.record_application(platform_id="napcat-1", request=old, **options)
    store.record_application(platform_id="napcat-2" if different == "platform" else "napcat-1", request=new, **options)
    assert store.review_blocker(old_id) is None
    store.close()


@pytest.mark.asyncio
async def test_system_request_without_time_uses_napcat_sequence_time(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    await plugin._reconcile_system_request(
        item={"request_id": "1789989219253793", "group_id": 123, "requester_uin": "20002", "checked": False},
        platform_id="napcat-1", now=1789990000, allow_catch_up=False,
    )
    assert record_for(plugin, "1789989219253793")["requested_at"] == 1789989219
    await plugin.terminate()
