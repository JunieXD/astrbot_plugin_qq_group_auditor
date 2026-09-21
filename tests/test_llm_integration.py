import asyncio
import sqlite3
import sys
import types

import pytest

from test_main import FakeContext, FakeEvent, FakeRequestEvent, collect, import_main, plugin_config
from test_llm_stats import response


@pytest.mark.asyncio
async def test_test_command_records_each_attempt_with_real_usage(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    responses = [response(text="invalid"), response()]

    async def llm(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(plugin.context, "llm_generate", llm)
    text = await collect(plugin.qgaudit_test(FakeEvent(message="/qgaudit test 123 2028")))
    assert "approve=True" in text[0]
    rows = plugin.usage_store.query(group_ids=["123"])
    assert [r["status"] for r in rows] == ["invalid_json", "success"]
    assert [r["attempt"] for r in rows] == [1, 2]
    assert rows[0]["review_id"] == rows[1]["review_id"]
    assert all(r["source"] == "test" and r["cached_tokens"] == 80 for r in rows)
    assert all(r["application_id"] is None and r["duration_ms"] >= 0 for r in rows)
    await plugin.terminate()


@pytest.mark.asyncio
async def test_real_review_links_metrics_but_invite_does_not_call_llm(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["group_audits"][0]["invite_action"] = "approve"
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    calls = []

    async def approve(*args, **kwargs):
        calls.append(kwargs)
    monkeypatch.setattr(module, "set_group_request", approve)
    event = FakeRequestEvent()
    await plugin.handle_group_request(event)
    history = plugin.audit_store.history(group_id="123", applicant_qq="20002")
    rows = plugin.usage_store.query(group_ids=["123"])
    assert len(rows) == 1 and rows[0]["application_id"] == history[0]["id"]
    assert rows[0]["status"] == "success" and rows[0]["source"] == "plugin"
    assert rows[0]["input_tokens"] is None  # Fake provider has no usage.
    event.message_obj.raw_message.update(flag="invite", user_id=20003, comment="")
    await plugin.handle_group_request(event)
    assert len(plugin.usage_store.query(group_ids=["123"])) == 1
    assert len(calls) == 2
    await plugin.terminate()


@pytest.mark.asyncio
async def test_reload_cancels_inflight_model_and_persists_cancelled(monkeypatch, tmp_path):
    module, _ = import_main(monkeypatch)
    monkeypatch.setattr(module, "_audit_database_path", lambda: tmp_path / "audit.sqlite3")
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    started = asyncio.Event()

    async def hanging(**kwargs):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(plugin.context, "llm_generate", hanging)
    task = asyncio.create_task(collect(plugin.qgaudit_test(FakeEvent(message="/qgaudit test 123 test"))))
    await asyncio.wait_for(started.wait(), timeout=2)
    await plugin.terminate()
    assert task.cancelled()
    reloaded = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    rows = reloaded.usage_store.query(group_ids=["123"])
    assert len(rows) == 1 and rows[0]["status"] == "cancelled"
    assert rows[0]["input_tokens"] is None and rows[0]["duration_ms"] is not None
    await reloaded.terminate()


@pytest.mark.asyncio
async def test_provider_error_records_unknown_usage_and_error_type_only(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    async def failed(**kwargs):
        raise TimeoutError("untrusted error with secret data")
    monkeypatch.setattr(plugin.context, "llm_generate", failed)
    await collect(plugin.qgaudit_test(FakeEvent(message="/qgaudit test 123 test")))
    row = plugin.usage_store.query(group_ids=["123"], content=True)[0]
    assert row["status"] == "provider_error" and row["error_type"] == "TimeoutError"
    assert row["estimated_cost"] is None and row["output_tokens"] is None
    assert "secret data" not in str(row)
    await plugin.terminate()


@pytest.mark.asyncio
async def test_unavailable_statistics_database_does_not_block_approval(monkeypatch):
    module, _ = import_main(monkeypatch)
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("readonly database")
    monkeypatch.setattr(module, "UsageStore", unavailable)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    calls = []
    async def approve(*args, **kwargs):
        calls.append(kwargs)
    monkeypatch.setattr(module, "set_group_request", approve)
    await plugin.handle_group_request(FakeRequestEvent())
    assert calls[0]["approve"] is True
    assert plugin.usage_store is None
    await plugin.terminate()


@pytest.mark.asyncio
async def test_statistics_write_failure_keeps_valid_review(monkeypatch, caplog):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    plugin.usage_store._db.execute("DROP TABLE llm_calls")
    result = await collect(plugin.qgaudit_test(FakeEvent(message="/qgaudit test 123 2028")))
    assert "approve=True" in result[0]
    assert "统计写入失败" in caplog.text
    await plugin.terminate()


@pytest.mark.asyncio
async def test_stats_and_csv_respect_group_admin_permissions(monkeypatch, tmp_path):
    module, _ = import_main(monkeypatch)
    monkeypatch.setattr(module, "_audit_database_path", lambda: tmp_path / "audit.sqlite3")
    config = plugin_config()
    config["group_audits"].append({"group_id": "456", "admin_qq_ids": ["10002"]})
    plugin = module.QQGroupAuditorPlugin(FakeContext(), config)
    await collect(plugin.qgaudit_test(FakeEvent(message="/qgaudit test 123 2028")))
    await collect(plugin.qgaudit_test(FakeEvent(message="/qgaudit test 456 2028", sender_id="10002")))
    visible, days = plugin._statistics_query(FakeEvent(message="/qgaudit stats 7d all"))
    assert days == 7 and [r["group_id"] for r in visible] == ["123"]
    denied = await collect(plugin.qgaudit_stats(FakeEvent(message="/qgaudit stats 7d 456")))
    assert denied == ["无权限"]
    assert await collect(plugin.qgaudit_export(FakeEvent(message="/qgaudit export 7d 456"))) == ["无权限"]
    text = await collect(plugin.qgaudit_stats(FakeEvent(message="/qgaudit stats")))
    assert "调用 1 次" in text[0]
    components = types.ModuleType("astrbot.api.message_components")
    components.File = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "astrbot.api.message_components", components)
    event = FakeEvent(message="/qgaudit export 7d")
    event.chain_result = lambda chain: chain
    exported = await collect(plugin.qgaudit_export(event))
    from pathlib import Path
    import csv
    with Path(exported[0][0]["file"]).open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert [r["group_id"] for r in rows] == ["123"]
    await plugin.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["", "/qgaudit stats 0", "/qgaudit stats 3651d", "/qgaudit stats abc",
                                    "/qgaudit stats 7d all m extra"])
async def test_stats_invalid_queries_return_readable_error(monkeypatch, command):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    messages = await collect(plugin.qgaudit_stats(FakeEvent(message=command)))
    assert "用法" in messages[0] or "天数" in messages[0]
    await plugin.terminate()
