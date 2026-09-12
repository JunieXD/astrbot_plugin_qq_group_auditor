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
    return guard


class ActionGuard:
    deferred_error = ActionDeferred

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
        async with self.locks.setdefault(account, asyncio.Lock()):
            state = self.accounts.setdefault(account, {})
            now = time.time()
            records = state.setdefault("operations", {})
            for old_key, record in list(records.items()):
                if record["expires"] <= now:
                    del records[old_key]
            fingerprint = hashlib.sha256(key.encode()).hexdigest() if key else None
            if fingerprint in records:
                if records[fingerprint]["status"] == "success":
                    return None
                raise ActionUncertain("此前操作结果不明，需核对状态后人工处理")
            if now < state.get("paused_until", 0):
                raise ActionDeferred("账号自动操作处于冷却期")

            async def check_online():
                try:
                    connected = await online()
                except Exception:
                    connected = False
                if not connected:
                    state["was_offline"] = True
                    state["paused_until"] = time.time() + 300
                    self._save()
                    raise ActionDeferred("QQ 离线，自动操作暂停 300 秒")

            await check_online()
            if state.pop("was_offline", False):
                ready_at = max(ready_at, time.time() + random.uniform(
                    *delay_range(config, "recovery")))
            ready_at = max(ready_at, state.get("next_at", 0))
            spacing = random.uniform(*gap)
            state["next_at"] = ready_at + spacing
            self._save()  # Persist reservations even if a reload cancels the wait.
            await asyncio.sleep(max(0, ready_at - time.time()))
            await check_online()
            if fingerprint:
                records[fingerprint] = {"status": "unknown", "expires": time.time() + dedup_seconds}
                self._save()  # Persist before a potentially ambiguous platform write.
            try:
                result = await action()
            except ActionDeferred:
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
