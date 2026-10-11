import asyncio

import pytest

from test_main import FakeContext, FakeRequestEvent, FakeResponse, drain_reviews, import_main, plugin_config


def request(flag="one", user=20002, comment="关键词答案"):
    event = FakeRequestEvent()
    event.message_obj.raw_message.update(flag=flag, user_id=user, time=1000, comment=comment)
    return event


@pytest.mark.asyncio
async def test_model_starts_without_waiting_for_optional_metadata_or_previous_review(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    lookup_gate, model_gate = asyncio.Event(), asyncio.Event()
    lookup_started, first_started, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    writes = []

    async def question(*args, **kwargs):
        lookup_started.set()
        await lookup_gate.wait()
        return "问题"

    async def llm(**kwargs):
        if '"20002"' in kwargs["prompt"]:
            first_started.set()
            await model_gate.wait()
        else:
            second_started.set()
        return FakeResponse()

    async def write(*args, **kwargs):
        writes.append(kwargs["flag"])

    monkeypatch.setattr(module, "get_group_question", question)
    monkeypatch.setattr(module, "set_group_request", write)
    plugin.context.llm_generate = llm
    await asyncio.wait_for(plugin.handle_group_request(request()), 1)
    await asyncio.wait_for(first_started.wait(), 1)
    await asyncio.wait_for(lookup_started.wait(), 1)
    await asyncio.wait_for(plugin.handle_group_request(request("two", 20003)), 1)
    await asyncio.wait_for(second_started.wait(), 1)
    for _ in range(20):
        await asyncio.sleep(0)
    assert writes == ["two"]
    assert not model_gate.is_set() and not lookup_gate.is_set()
    model_gate.set()
    lookup_gate.set()
    await drain_reviews(plugin)
    assert writes == ["two", "one"]
    await plugin.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("model_seconds,expected_remaining", [(3, 7), (20, 0)])
async def test_model_time_and_random_delay_take_maximum(monkeypatch, model_seconds, expected_remaining):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["automation_pacing"] = {"review_min_seconds": 10, "review_max_seconds": 10}
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    delays, writes = [], []

    async def llm(**kwargs):
        now[0] += model_seconds
        return FakeResponse()

    async def guard(**kwargs):
        delays.append(kwargs["delay"])
        now[0] += kwargs["delay"][0]
        return await kwargs["action"]()

    async def write(*args, **kwargs):
        writes.append(now[0] - 1000)

    plugin.context.llm_generate = llm
    monkeypatch.setattr(plugin.guard, "run", guard)
    monkeypatch.setattr(module, "set_group_request", write)
    await plugin.handle_group_request(request())
    await drain_reviews(plugin)
    assert delays == [(expected_remaining, expected_remaining)]
    assert writes == [max(model_seconds, 10)]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_detached_reviews_keep_three_call_limit_and_deduplicate_live_flag(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    gate, three_started = asyncio.Event(), asyncio.Event()
    active = peak = calls = 0
    writes = []

    async def llm(**kwargs):
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        if active == 3:
            three_started.set()
        try:
            await gate.wait()
            return FakeResponse()
        finally:
            active -= 1

    async def write(*args, **kwargs):
        writes.append(kwargs["flag"])

    plugin.context.llm_generate = llm
    monkeypatch.setattr(module, "set_group_request", write)
    for i in range(4):
        await plugin.handle_group_request(request(str(i), 20002 + i))
    await asyncio.wait_for(three_started.wait(), 1)
    await plugin.handle_group_request(request("0"))
    assert peak == calls == 3 and len(plugin._review_tasks) == 4
    gate.set()
    await drain_reviews(plugin)
    assert peak == 3 and calls == 4
    assert sorted(writes) == ["0", "1", "2", "3"]
    await plugin.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_first", [False, True])
async def test_total_timeout_holds_even_reject_groups_and_releases_slot(monkeypatch, invalid_first):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["llm_review"] = {"timeout_seconds": 1, "slow_seconds": .05}
    config["group_audits"][0]["failure_action"] = "reject"
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    calls = 0

    async def llm(**kwargs):
        nonlocal calls
        calls += 1
        if invalid_first and calls == 1:
            return type("Response", (), {"completion_text": "invalid"})()
        await asyncio.Event().wait()

    async def forbidden(*args, **kwargs):
        pytest.fail("technical timeout must not approve or reject")

    async def notice(*args, **kwargs):
        pass

    plugin.context.llm_generate = llm
    monkeypatch.setattr(module, "set_group_request", forbidden)
    monkeypatch.setattr(plugin, "_send_notice", notice)
    await plugin.handle_group_request(request())
    await asyncio.wait_for(drain_reviews(plugin), 3)
    rows = plugin.usage_store.query(group_ids=["123"], content=True)
    assert [r["status"] for r in rows] == (["invalid_json", "timeout"] if invalid_first else ["timeout"])
    assert rows[-1]["input_tokens"] is None and rows[-1]["output_tokens"] is None
    assert plugin._llm_limiter._value == 3 and not plugin._llm_tasks
    record = plugin.audit_store.history(group_id="123", applicant_qq="20002")[0]
    assert [(a["kind"], a["action"]) for a in record["actions"]] == [("review", "error")]
    phases = plugin.audit_store.detail(group_id="123", application_id=record["id"])["phases"]
    assert {"llm_slow", "llm_timeout"} <= {p["phase"] for p in phases}
    await plugin.terminate()


@pytest.mark.asyncio
async def test_reload_cancels_review_and_metadata_without_platform_write(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def forbidden(*args, **kwargs):
        pytest.fail("reload must not submit approval")

    plugin.context.llm_generate = blocked
    monkeypatch.setattr(module, "get_group_question", blocked)
    monkeypatch.setattr(module, "set_group_request", forbidden)
    await plugin.handle_group_request(request())
    await asyncio.wait_for(started.wait(), 1)
    await plugin.terminate()
    assert cancelled.is_set()
    assert not plugin._review_tasks and not plugin._metadata_tasks and not plugin._llm_tasks
    assert plugin.audit_store is None and plugin.usage_store is None


@pytest.mark.asyncio
async def test_timeout_in_slot_queue_does_not_start_or_record_model_call(monkeypatch):
    module, _ = import_main(monkeypatch)
    limiter = asyncio.Semaphore(0)
    context = FakeContext()
    reviewer = module.RuntimeReviewer(context, limiter=limiter, llm_options={"timeout_seconds": 1})
    event = module.extract_join_request(request())
    with pytest.raises(module.LLMReviewError, match="人工"):
        await reviewer.review(group_config=plugin_config()["group_audits"][0], request=event)
    assert context.llm_calls == [] and limiter._value == 0


@pytest.mark.asyncio
async def test_recovery_wait_remains_even_when_model_finishes_immediately(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    plugin._sync_recovery_until["napcat-1"] = 1060
    delays = []

    async def guard(**kwargs):
        delays.append(kwargs["delay"])

    monkeypatch.setattr(plugin.guard, "run", guard)
    await plugin.handle_group_request(request())
    await drain_reviews(plugin)
    assert len(plugin.context.llm_calls) == 1
    assert delays == [(60, 60)]
    await plugin.terminate()
