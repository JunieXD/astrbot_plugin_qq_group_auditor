import asyncio
import importlib.util
from types import SimpleNamespace

import pytest

from qq_group_auditor import pacing

CONFIG = {"recovery_min_seconds": 60, "recovery_max_seconds": 180,
          "failure_threshold": 2, "failure_cooldown_seconds": 3600}


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    now = [1000.0]
    sleeps = []
    real_sleep = asyncio.sleep
    async def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
        await real_sleep(0)
    monkeypatch.setattr(pacing.time, "time", lambda: now[0])
    monkeypatch.setattr(pacing.random, "uniform", lambda lo, hi: lo)
    monkeypatch.setattr(pacing.asyncio, "sleep", sleep)
    return pacing.ActionGuard(tmp_path / "state.json"), now, sleeps


async def online():
    return True


def options(action, **extra):
    return dict(account="bot", online=online, action=action, config=CONFIG,
                delay=(15, 45), gap=(10, 20), **extra)


@pytest.mark.asyncio
async def test_concurrent_operations_are_spaced_and_survive_reload(runtime):
    guard, now, sleeps = runtime
    calls = []
    async def action():
        calls.append(now[0])
    await asyncio.gather(guard.run(**options(action)), guard.run(**options(action)))
    assert calls[1] - calls[0] >= 10
    restored = pacing.ActionGuard(guard.path)
    await restored.run(**options(action))
    assert calls[2] - calls[1] >= 10
    assert calls[0] >= 1015


@pytest.mark.asyncio
async def test_uncertain_write_is_not_repeated_after_reload(runtime):
    guard, now, _ = runtime
    calls = []
    async def cancelled():
        calls.append(True)
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await guard.run(**options(cancelled, key="approve:request-1"))
    restored = pacing.ActionGuard(guard.path)
    with pytest.raises(pacing.ActionUncertain):
        await restored.run(**options(cancelled, key="approve:request-1"))
    assert calls == [True]


@pytest.mark.asyncio
async def test_failures_pause_other_actions_on_same_account(runtime):
    guard, now, _ = runtime
    calls = []
    async def fail():
        calls.append(True)
        raise TimeoutError()
    for _ in range(2):
        with pytest.raises(TimeoutError):
            await guard.run(**options(fail))
    restored = pacing.ActionGuard(guard.path)
    with pytest.raises(pacing.ActionDeferred):
        await restored.run(**options(fail))
    assert calls == [True, True]
    assert restored.accounts["bot"]["paused_until"] > now[0]


@pytest.mark.asyncio
async def test_offline_and_recovery_never_send_during_cooldown(runtime):
    guard, now, sleeps = runtime
    connected = [False]
    calls = []
    async def status():
        return connected[0]
    async def action():
        calls.append(now[0])
    kwargs = options(action)
    kwargs["online"] = status
    with pytest.raises(pacing.ActionDeferred):
        await guard.run(**kwargs)
    connected[0] = True
    now[0] += 300
    restored = pacing.ActionGuard(guard.path)
    await restored.run(**kwargs)
    assert calls == [1360]


@pytest.mark.asyncio
async def test_recheck_online_after_wait(runtime):
    guard, _, _ = runtime
    answers = iter([True, False])
    async def status():
        return next(answers)
    async def forbidden():
        pytest.fail("must not write after disconnect during delay")
    kwargs = options(forbidden)
    kwargs["online"] = status
    with pytest.raises(pacing.ActionDeferred):
        await guard.run(**kwargs)


@pytest.mark.asyncio
async def test_success_deduplication_and_storage_failure(runtime, monkeypatch):
    guard, _, _ = runtime
    calls = []
    async def action():
        calls.append(True)
    await guard.run(**options(action, key="notice:one"))
    await guard.run(**options(action, key="notice:one"))
    assert calls == [True]
    def fail_save():
        raise OSError("disk full")
    monkeypatch.setattr(guard, "_save", fail_save)
    with pytest.raises(OSError):
        await guard.run(**options(action, key="notice:two"))
    assert calls == [True]


def test_two_plugin_module_instances_share_registry_and_exceptions(tmp_path):
    spec = importlib.util.spec_from_file_location("another_plugin_pacing", pacing.__file__)
    other = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(other)
    manager = SimpleNamespace()
    a = pacing.get_guard(SimpleNamespace(platform_manager=manager), tmp_path)
    b = other.get_guard(SimpleNamespace(platform_manager=manager), tmp_path)
    assert a is b
    assert b.deferred_error is pacing.ActionDeferred


def test_invalid_ranges_are_normalized():
    defaults = {"review_min_seconds": 15, "review_max_seconds": 45, "failure_threshold": 3}
    assert pacing.normalize_pacing({"review_min_seconds": 80, "review_max_seconds": -3,
                                    "failure_threshold": float("nan")}, defaults) == {
        "review_min_seconds": 80, "review_max_seconds": 80, "failure_threshold": 3}
