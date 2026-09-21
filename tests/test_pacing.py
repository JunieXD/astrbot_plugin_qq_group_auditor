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


class ManualClock:
    def __init__(self):
        self.now = 1000.0
        self.waiters = []
        self.real_sleep = asyncio.sleep

    async def sleep(self, seconds):
        if seconds <= 0:
            await self.real_sleep(0)
            return
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.now + seconds, future))
        await future

    async def settle(self):
        for _ in range(12):
            await self.real_sleep(0)

    async def advance(self, seconds):
        self.now += seconds
        for deadline, future in self.waiters:
            if deadline <= self.now and not future.done():
                future.set_result(None)
        self.waiters = [(d, f) for d, f in self.waiters if not f.done()]
        await self.settle()


@pytest.fixture
def manual_runtime(monkeypatch, tmp_path):
    clock = ManualClock()
    monkeypatch.setattr(pacing.time, "time", lambda: clock.now)
    monkeypatch.setattr(pacing.asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(pacing.random, "uniform", lambda lo, hi: lo)
    return pacing.ActionGuard(tmp_path / "state.json"), clock


@pytest.mark.asyncio
async def test_ready_approval_overtakes_earlier_card_wait(manual_runtime):
    guard, clock = manual_runtime
    calls = []

    async def record(name):
        calls.append((name, clock.now))

    card = asyncio.create_task(guard.run(account="bot", online=online,
        action=lambda: record("card"), config=CONFIG, delay=(90, 90), gap=(10, 10)))
    await clock.settle()
    approval = asyncio.create_task(guard.run(account="bot", online=online,
        action=lambda: record("approval"), config=CONFIG, delay=(5, 5), gap=(10, 10)))
    await clock.settle()
    await clock.advance(5)
    assert calls == [("approval", 1005)]
    assert not card.done()
    await clock.advance(85)
    await asyncio.gather(card, approval)
    assert calls == [("approval", 1005), ("card", 1090)]


@pytest.mark.asyncio
async def test_cancelled_long_wait_does_not_reserve_account(manual_runtime):
    guard, clock = manual_runtime
    calls = []

    async def action():
        calls.append(clock.now)

    task = asyncio.create_task(guard.run(account="bot", online=online, action=action,
        config=CONFIG, delay=(90, 90), key="card"))
    await clock.settle()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    restored = pacing.ActionGuard(guard.path)
    assert restored.accounts["bot"].get("next_at", 0) <= clock.now
    assert restored.accounts["bot"]["operations"] == {}
    approval = asyncio.create_task(restored.run(account="bot", online=online, action=action,
        config=CONFIG, delay=(5, 5), key="approval"))
    await clock.settle()
    await clock.advance(5)
    await approval
    assert calls == [1005]


@pytest.mark.asyncio
async def test_shared_gap_is_measured_after_slow_action_completes(manual_runtime):
    guard, clock = manual_runtime
    release = asyncio.Event()
    calls = []

    async def slow():
        calls.append(("slow", clock.now))
        await release.wait()

    async def next_action():
        calls.append(("next", clock.now))

    first = asyncio.create_task(guard.run(account="bot", online=online, action=slow,
        config=CONFIG, gap=(10, 10)))
    await clock.settle()
    second = asyncio.create_task(guard.run(account="bot", online=online, action=next_action,
        config=CONFIG, gap=(10, 10)))
    await clock.advance(7)
    release.set()
    await clock.settle()
    await clock.advance(9)
    assert calls == [("slow", 1000)]
    await clock.advance(1)
    await asyncio.gather(first, second)
    assert calls == [("slow", 1000), ("next", 1017)]


@pytest.mark.asyncio
async def test_recovery_wait_is_shared_by_later_tasks(manual_runtime):
    guard, clock = manual_runtime
    guard.accounts["bot"] = {"was_offline": True, "paused_until": 1000}
    calls = []

    async def action():
        calls.append(clock.now)

    first = asyncio.create_task(guard.run(account="bot", online=online, action=action,
        config=CONFIG, gap=(10, 10)))
    await clock.settle()
    second = asyncio.create_task(guard.run(account="bot", online=online, action=action,
        config=CONFIG, gap=(10, 10)))
    await clock.settle()
    assert pacing.ActionGuard(guard.path).accounts["bot"]["recovery_until"] == 1060
    await clock.advance(59)
    assert calls == []
    await clock.advance(1)
    assert calls == [1060]
    await clock.advance(10)
    await asyncio.gather(first, second)
    assert calls == [1060, 1070]


@pytest.mark.asyncio
async def test_failure_cooldown_stops_actions_already_sleeping(manual_runtime):
    guard, clock = manual_runtime
    calls = []

    async def failure():
        raise TimeoutError()

    async def action():
        calls.append(clock.now)

    config = {**CONFIG, "failure_threshold": 1}
    pending = asyncio.create_task(guard.run(account="bot", online=online, action=action,
        config=config, delay=(20, 20)))
    failing = asyncio.create_task(guard.run(account="bot", online=online, action=failure,
        config=config, delay=(5, 5)))
    await clock.settle()
    await clock.advance(5)
    await clock.advance(15)
    outcomes = await asyncio.gather(failing, pending, return_exceptions=True)
    assert isinstance(outcomes[0], TimeoutError)
    assert isinstance(outcomes[1], pacing.ActionDeferred)
    assert not calls


@pytest.mark.asyncio
async def test_concurrent_duplicate_keys_do_not_send_twice(manual_runtime):
    guard, clock = manual_runtime
    calls = []

    async def action():
        calls.append(clock.now)

    tasks = [asyncio.create_task(guard.run(account="bot", online=online, action=action,
        config=CONFIG, delay=(5, 5), key="same-request")) for _ in range(3)]
    await clock.settle()
    await clock.advance(5)
    await asyncio.gather(*tasks)
    assert calls == [1005]


@pytest.mark.asyncio
async def test_hot_reload_upgrades_shared_guard_without_replacing_lock_or_exception(manual_runtime):
    guard, clock = manual_runtime
    spec = importlib.util.spec_from_file_location("older_plugin_pacing", pacing.__file__)
    older = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(older)
    old_error = older.ActionDeferred
    entered, release = asyncio.Event(), asyncio.Event()

    class LegacyGuard(older.ActionGuard):
        scheduling_version = 1

        async def run_old_call(self):
            async with self.locks.setdefault("bot", asyncio.Lock()):
                entered.set()
                await release.wait()
                # An in-flight v1 call catches its original module's exception.
                try:
                    raise self.deferred_error("obsolete request")
                except old_error:
                    return "deferred"

    legacy = LegacyGuard(guard.path)
    manager = SimpleNamespace(_qq_automation_guard_v1=legacy)
    running = asyncio.create_task(legacy.run_old_call())
    await entered.wait()
    locks, accounts = legacy.locks, legacy.accounts
    upgraded = pacing.get_guard(SimpleNamespace(platform_manager=manager), guard.path.parent)
    assert upgraded is legacy
    assert upgraded.locks is locks and upgraded.accounts is accounts
    assert upgraded.deferred_error is old_error
    assert upgraded.scheduling_version == 2
    calls = []

    async def action():
        calls.append(clock.now)
        raise old_error("already handled")

    pending = asyncio.create_task(upgraded.run(account="bot", online=online, action=action,
        config=CONFIG, key="new-call"))
    await clock.settle()
    assert calls == []
    release.set()
    await clock.settle()
    assert await running == "deferred"
    with pytest.raises(old_error):
        await pending
    assert upgraded.accounts["bot"].get("failures", 0) == 0
    assert upgraded.accounts["bot"]["operations"] == {}
