"""Shared, durable pacing for the two QQ automation plugins.

Keep this module identical in qq_group_auditor and github_subscriber. The registry
on AstrBot's shared platform manager survives individual plugin reloads.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import time
from pathlib import Path


class ActionDeferred(Exception):
    """No platform write was attempted; leave the durable task pending."""


class ActionUncertain(Exception):
    """A previous write may have succeeded; do not blindly repeat it."""


def normalize_pacing(raw, defaults):
    raw = raw if isinstance(raw, dict) else {}
    result = dict(defaults)
    for key, default in defaults.items():
        try:
            value = float(raw.get(key, default))
            if not math.isfinite(value):
                raise ValueError(key)
            result[key] = max(0, min(86400, value))
        except (TypeError, ValueError, OverflowError):
            pass
    for key in defaults:
        if key.endswith("_min_seconds"):
            end = key.replace("_min_seconds", "_max_seconds")
            result[end] = max(result[key], result[end])
    if "failure_threshold" in result:
        result["failure_threshold"] = max(1, int(result["failure_threshold"]))
    return result


def delay_range(config, name):
    return (config[f"{name}_min_seconds"], config[f"{name}_max_seconds"])


def get_guard(context, data_root):
    owner = getattr(context, "platform_manager", None) or context
    attribute = "_qq_automation_guard_v1"
    guard = getattr(owner, attribute, None)
    if guard is None:
        guard = ActionGuard(Path(data_root) / "qq_automation_guard" / "state.json")
        setattr(owner, attribute, guard)
    elif getattr(guard, "scheduling_version", 1) < ActionGuard.scheduling_version:
        # Other plugins and in-flight calls still hold this exact object. Keep
        # their locks, state and exception identity while upgrading future calls.
        deferred_error = guard.deferred_error
        guard.__class__ = ActionGuard
        guard.deferred_error = deferred_error
    return guard


class ActionGuard:
    deferred_error = ActionDeferred
    scheduling_version = 2

    def __init__(self, path):
        self.path = Path(path)
        self.accounts = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.locks = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.accounts, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    async def run(self, *, account, online, action, config, delay=(0, 0),
                  gap=(8, 15), key=None, dedup_seconds=604800):
        ready_at = time.time() + random.uniform(*delay)
        lock = self.locks.setdefault(account, asyncio.Lock())
        state = self.accounts.setdefault(account, {})
        records = state.setdefault("operations", {})
        fingerprint = hashlib.sha256(key.encode()).hexdigest() if key else None

        def already_done():
            now = time.time()
            for old_key, record in list(records.items()):
                if record["expires"] <= now:
                    del records[old_key]
            if fingerprint in records:
                if records[fingerprint]["status"] == "success":
                    return True
                raise ActionUncertain("此前操作结果不明，需核对状态后人工处理")
            if now < state.get("paused_until", 0):
                raise self.deferred_error("账号自动操作处于冷却期")
            return False

        async def check_online():
            try:
                connected = await online()
            except Exception:
                connected = False
            if not connected:
                state["was_offline"] = True
                state["paused_until"] = time.time() + 300
                self._save()
                raise self.deferred_error("QQ 离线，自动操作暂停 300 秒")
            if state.pop("was_offline", False):
                # Recovery applies to the account, including tasks already
                # waiting outside the lock. Persist it across plugin reloads.
                state["recovery_until"] = time.time() + random.uniform(
                    *delay_range(config, "recovery"))
                self._save()

        def deadline():
            return max(ready_at, state.get("next_at", 0), state.get("recovery_until", 0))

        async with lock:
            if already_done():
                return None
            await check_online()
            spacing = random.uniform(*gap)
            self._save()

        while True:
            # A card/notification's long delay must not reserve the account or
            # prevent a later, already-ready approval from proceeding.
            await asyncio.sleep(max(0, deadline() - time.time()))
            async with lock:
                if already_done():
                    return None
                if time.time() < deadline():
                    continue
                await check_online()
                if time.time() < deadline():
                    continue
                state["next_at"] = time.time() + spacing
                if fingerprint:
                    records[fingerprint] = {"status": "unknown", "expires": time.time() + dedup_seconds}
                self._save()  # Persist before a potentially ambiguous platform write.
                try:
                    result = await action()
                except self.deferred_error:
                    if fingerprint:
                        records.pop(fingerprint, None)
                    self._save()
                    raise
                except BaseException:
                    state["failures"] = state.get("failures", 0) + 1
                    if state["failures"] >= config["failure_threshold"]:
                        state["paused_until"] = time.time() + config["failure_cooldown_seconds"]
                    self._save()
                    raise
                else:
                    state["failures"] = 0
                    if fingerprint:
                        records[fingerprint]["status"] = "success"
                    self._save()
                    return result
                finally:
                    state["next_at"] = max(state["next_at"], time.time() + spacing)
                    self._save()
