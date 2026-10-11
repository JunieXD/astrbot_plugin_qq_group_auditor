import asyncio

import pytest

from test_main import FakeContext, import_main, plugin_config
from qq_group_auditor.config import normalize_config
from qq_group_auditor.platform import is_onebot_online
from test_platform import FakeBot, FakeContext as PlatformContext


@pytest.mark.parametrize("value,expected", [(None, 600), ("bad", 600), (0, 300), ("900", 900), (999999, 86400)])
def test_background_interval_is_bounded(value, expected):
    assert normalize_config({"background_sync_interval_seconds": value})["background_sync_interval_seconds"] == expected


@pytest.mark.asyncio
async def test_sync_shares_cooldown_and_backs_off_after_failure(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    plugin.config["automation_pacing"]["sync_jitter_seconds"] = 0
    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    calls = []
    fail = [False]

    async def reconcile(platform_id):
        calls.append(platform_id)
        if fail[0]:
            raise TimeoutError()

    monkeypatch.setattr(plugin, "_reconcile_platform", reconcile)
    await plugin._poll_platform("bot")
    for _ in range(10):
        await plugin._poll_platform("bot")
    assert calls == ["bot"]
    assert plugin._sync_next_at["bot"] == 1600
    fail[0] = True
    for delay in (600, 1200, 2400, 3600, 3600):
        now[0] = plugin._sync_next_at["bot"]
        await plugin._poll_platform("bot")
        assert plugin._sync_next_at["bot"] == now[0] + delay
    fail[0] = False
    now[0] = plugin._sync_next_at["bot"]
    await plugin._poll_platform("bot")
    assert plugin._sync_next_at["bot"] == now[0] + 600
    assert "bot" not in plugin._sync_failures
    await plugin.terminate()


@pytest.mark.asyncio
async def test_offline_disabled_and_unconfigured_platforms_do_not_poll(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    status_checks = []

    async def offline(*args, **kwargs):
        status_checks.append(True)
        return False

    async def forbidden(*args, **kwargs):
        pytest.fail("offline platform must not query QQ or update cards")

    monkeypatch.setattr(module, "is_onebot_online", offline)
    monkeypatch.setattr(plugin, "_reconcile_platform", forbidden)
    await plugin._poll_platform("bot")
    await plugin._poll_platform("bot")
    assert status_checks == [True]
    plugin.config["background_sync_enabled"] = False
    await plugin._poll_platform("another-bot")
    plugin.config["background_sync_enabled"] = True
    plugin.config["group_audits"] = []
    await plugin._poll_platform("another-bot")
    assert status_checks == [True]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_concurrent_sync_calls_do_not_duplicate_requests(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def reconcile(platform_id):
        calls.append(platform_id)
        entered.set()
        await release.wait()

    monkeypatch.setattr(plugin, "_reconcile_platform", reconcile)
    first = asyncio.create_task(plugin._poll_platform("bot"))
    await entered.wait()
    await plugin._poll_platform("bot")
    release.set()
    await first
    assert calls == ["bot"]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_disconnected_onebot_skips_even_local_status_call():
    bot = FakeBot()
    bot._wsr_api_clients = {}
    assert not await is_onebot_online(PlatformContext(bot), platform_id=None)
    assert bot.calls == []
    bot._wsr_api_clients = {"bot": object()}
    bot.response = {"online": False, "good": True}
    assert not await is_onebot_online(PlatformContext(bot), platform_id=None)
    bot.response = {"online": True}
    assert await is_onebot_online(PlatformContext(bot), platform_id=None)


@pytest.mark.asyncio
async def test_reconnected_transport_clears_long_backoff_once_and_waits_for_recovery(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    now, token, connected = [1000.0], [()], [False]
    calls, status_checks = [], []
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module.random, "uniform", lambda a, b: a)
    monkeypatch.setattr(module, "onebot_connection_token", lambda *a, **k: token[0])

    async def online(*a, **k):
        status_checks.append(now[0])
        return connected[0]

    async def reconcile(pid):
        calls.append((pid, now[0]))

    monkeypatch.setattr(module, "is_onebot_online", online)
    monkeypatch.setattr(plugin, "_reconcile_platform", reconcile)
    await plugin._poll_platform("bot")
    plugin._sync_next_at["bot"] = 4600
    now[0] = 1010
    connected[0], token[0] = True, (1,)
    await plugin._poll_platform("bot")
    assert plugin._sync_next_at["bot"] == 1070
    assert calls == [] and status_checks == [1000]
    assert "bot" not in plugin._sync_failures
    now[0] = 1069
    await plugin._poll_platform("bot")
    assert calls == []
    now[0] = 1070
    await plugin._poll_platform("bot")
    assert calls == [("bot", 1070)]
    assert plugin._sync_next_at["bot"] == 1670
    await plugin._poll_platform("bot")
    assert calls == [("bot", 1070)]
    # A quick reconnect with no observed empty tick is also detected.
    now[0] = 1080
    token[0] = (2,)
    await plugin._poll_platform("bot")
    assert plugin._sync_next_at["bot"] == 1140
    await plugin.terminate()


@pytest.mark.asyncio
async def test_qq_login_recovery_probes_only_local_status_not_group_messages(monkeypatch):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    now, connected = [1000.0], [False]
    checks, calls = [], []
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module.random, "uniform", lambda a, b: a)
    monkeypatch.setattr(module, "onebot_connection_token", lambda *a, **k: (1,))

    async def status(*a, **k):
        checks.append(now[0])
        return connected[0]

    async def reconcile(pid):
        calls.append(now[0])

    monkeypatch.setattr(module, "is_onebot_online", status)
    monkeypatch.setattr(plugin, "_reconcile_platform", reconcile)
    await plugin._poll_platform("bot")
    plugin._sync_next_at["bot"] = 4600
    now[0] = 1029
    await plugin._poll_platform("bot")
    assert checks == [1000] and calls == []
    now[0], connected[0] = 1030, True
    await plugin._poll_platform("bot")
    assert checks == [1000, 1030] and calls == []
    assert plugin._sync_next_at["bot"] == 1090
    now[0] = 1090
    await plugin._poll_platform("bot")
    assert calls == [1090]
    await plugin.terminate()


def test_connection_token_observes_client_identity_without_network():
    from qq_group_auditor.platform import onebot_connection_token
    bot = FakeBot()
    bot._wsr_api_clients = {}
    assert onebot_connection_token(PlatformContext(bot), platform_id=None) == ()
    client = object()
    bot._wsr_api_clients = {"bot": client}
    assert onebot_connection_token(PlatformContext(bot), platform_id=None) == (id(client),)
    assert bot.calls == []
