from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register

try:
    from .qq_group_auditor.audit_store import AuditStore
    from .qq_group_auditor.audit_text import format_detail, format_history
    from .qq_group_auditor.card import CardTemplateError, render_card
    from .qq_group_auditor.config import (
        find_group_config,
        find_group_policy,
        is_group_admin,
        normalize_config,
        review_delay,
    )
    from .qq_group_auditor.models import (
        ActionResult,
        GroupMemberIncrease,
        GroupMemberInfo,
        JoinRequest,
        ReviewDecision,
    )
    from .qq_group_auditor.notifier import format_notice, send_admin_notice
    from .qq_group_auditor.platform import (
        get_group_member_info,
        PlatformActionError,
        RequestUnavailableError,
        extract_group_member_decrease,
        extract_group_member_increase,
        extract_join_request,
        get_group_question,
        get_group_system_requests,
        is_onebot_online,
        get_user_nickname,
        onebot_platform_ids,
        set_group_card,
        set_group_request,
    )
    from .qq_group_auditor.reviewer import LLMReviewError, review_answer
    from .qq_group_auditor.service import AuditService
    from .qq_group_auditor.text import extract_application_answer
    from .qq_group_auditor.pacing import ActionDeferred, delay_range, get_guard
except ImportError:  # pragma: no cover - supports direct local imports in tests/dev.
    from qq_group_auditor.audit_store import AuditStore
    from qq_group_auditor.audit_text import format_detail, format_history
    from qq_group_auditor.card import CardTemplateError, render_card
    from qq_group_auditor.config import (
        find_group_config,
        find_group_policy,
        is_group_admin,
        normalize_config,
        review_delay,
    )
    from qq_group_auditor.models import (
        ActionResult,
        GroupMemberIncrease,
        GroupMemberInfo,
        JoinRequest,
        ReviewDecision,
    )
    from qq_group_auditor.notifier import format_notice, send_admin_notice
    from qq_group_auditor.platform import (
        get_group_member_info,
        PlatformActionError,
        RequestUnavailableError,
        extract_group_member_decrease,
        extract_group_member_increase,
        extract_join_request,
        get_group_question,
        get_group_system_requests,
        is_onebot_online,
        get_user_nickname,
        onebot_platform_ids,
        set_group_card,
        set_group_request,
    )
    from qq_group_auditor.reviewer import LLMReviewError, review_answer
    from qq_group_auditor.service import AuditService
    from qq_group_auditor.text import extract_application_answer
    from qq_group_auditor.pacing import ActionDeferred, delay_range, get_guard


logger = logging.getLogger(__name__)

_DEEPSEEK_JSON_MAX_TOKENS = 512
_EXTERNAL_REJECTION_GRACE_SECONDS = 120
_RECONCILE_INTERVAL_SECONDS = 60
_JOIN_CONFIRM_RETRY_DELAYS = (60, 180, 600)
_CARD_ACTION_DELAY_RANGE_SECONDS = (0.8, 2.2)
_CATCH_UP_ACTION_DELAY_RANGE_SECONDS = (2.0, 5.0)
_MAX_AUTOMATIC_CARD_ATTEMPTS = 5
_MAX_CATCH_UP_REVIEWS_PER_CYCLE = 10
_EMPTY_ANSWER_WAIT_SECONDS = 30
_PLUGIN_NAME = "astrbot_plugin_qq_group_auditor"


def _is_deepseek_provider_id(provider_id: Any) -> bool:
    source_id = str(provider_id).strip().lower().partition("/")[0]
    return source_id == "deepseek" or source_id.startswith("deepseek-")


def _is_missing_group_member_error(error: Exception) -> bool:
    message = str(error).lower()
    return bool(
        re.search(r"成员.*不存在", message)
        or "member not found" in message
        or "not in group" in message
    )


def _card_action_delay_seconds() -> float:
    return random.uniform(*_CARD_ACTION_DELAY_RANGE_SECONDS)


def _catch_up_action_delay_seconds() -> float:
    return random.uniform(*_CATCH_UP_ACTION_DELAY_RANGE_SECONDS)


def _system_request_is_checked(item: dict[str, Any]) -> bool:
    value = item.get("checked")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"true", "1", "yes", "on"}


@filter.command_group("qgaudit")
def qgaudit():
    pass


class AstrBotLLMClient:
    def __init__(self, context: Context, umo: str | None = None) -> None:
        self.context = context
        self.umo = umo

    async def generate(self, *, system_prompt: str, prompt: str) -> str:
        chat_provider_id = await self._provider_id()
        generation_options: dict[str, Any] = {}
        if _is_deepseek_provider_id(chat_provider_id):
            generation_options = {
                "response_format": {"type": "json_object"},
                "max_tokens": _DEEPSEEK_JSON_MAX_TOKENS,
            }
        response = await self.context.llm_generate(
            chat_provider_id=chat_provider_id,
            system_prompt=system_prompt,
            prompt=prompt,
            **generation_options,
        )
        return str(getattr(response, "completion_text", ""))

    async def _provider_id(self) -> Any:
        if self.umo and hasattr(self.context, "get_current_chat_provider_id"):
            try:
                provider_id = await self.context.get_current_chat_provider_id(self.umo)
            except Exception:
                logger.debug("failed to resolve current chat provider", exc_info=True)
            else:
                if provider_id:
                    return provider_id
        provider = self.context.get_using_provider(None)
        meta = provider.meta() if provider is not None and hasattr(provider, "meta") else None
        provider_id = getattr(meta, "id", None)
        if not provider_id:
            raise RuntimeError("Provider not found")
        return provider_id


class RuntimeReviewer:
    def __init__(self, context: Context, umo: str | None = None) -> None:
        self.client = AstrBotLLMClient(context, umo)

    async def review(
        self,
        *,
        group_config: dict[str, Any],
        request: JoinRequest,
    ) -> ReviewDecision:
        return await review_answer(
            self.client,
            group_id=request.group_id,
            applicant_qq=request.applicant_qq,
            answer=request.answer,
            review_prompt=str(group_config.get("review_prompt") or ""),
        )


class RuntimePlatform:
    def __init__(
        self,
        context: Context,
        platform_id: str | None = None,
        action_delay_seconds: float = 0.0,
        executor: Any = None,
    ) -> None:
        self.context = context
        self.platform_id = platform_id
        self.action_delay_seconds = max(float(action_delay_seconds), 0.0)
        self.executor = executor

    async def set_group_request(
        self,
        request: JoinRequest,
        *,
        approve: bool,
        reason: str,
    ) -> None:
        if self.executor is not None:
            return await self.executor(request, approve=approve, reason=reason)
        if self.action_delay_seconds > 0:
            await asyncio.sleep(self.action_delay_seconds)
        await set_group_request(
            self.context,
            flag=request.flag,
            sub_type=request.sub_type,
            approve=approve,
            reason=reason,
            platform_id=self.platform_id,
        )


class RuntimeNotifier:
    def __init__(self, context: Context, platform_id: str | None = None, sender=None, check_request=None) -> None:
        self.context = context
        self.platform_id = platform_id
        self.sender = sender
        self.check_request = check_request

    async def notify(
        self,
        *,
        group_config: dict[str, Any],
        request: JoinRequest,
        title: str,
        action: str,
        reason: str = "",
        error: str = "",
    ) -> None:
        text = format_notice(title, request, action, reason=reason, error=error)
        try:
            if self.sender is not None:
                options = {}
                if self.check_request is not None and action in {"ignore", "error"}:
                    options["check_request"] = self.check_request
                await self.sender(group_config, text, self.platform_id, **options)
                return
            await send_admin_notice(
                self.context,
                list(group_config.get("admin_qq_ids") or []),
                text,
                platform_name=self.platform_id or "aiocqhttp",
            )
        except ActionDeferred:
            raise
        except Exception:
            logger.warning("failed to send audit notification", exc_info=True)


def parse_test_command(message_str: str) -> tuple[str, str] | None:
    match = re.match(r"^\s*/?qgaudit\s+test\s+(\S+)\s+(.+)\s*$", message_str, re.S)
    if match is None:
        return None
    return match.group(1), match.group(2)


def parse_history_command(message_str: str) -> tuple[str, str, int] | None:
    match = re.match(r"^\s*/?qgaudit\s+history\s+(\S+)\s+(\S+)(?:\s+(\d+))?\s*$", message_str)
    if match is None:
        return None
    limit = min(max(int(match.group(3) or 5), 1), 10)
    return match.group(1), match.group(2), limit


def parse_detail_command(message_str: str) -> tuple[str, int | str] | None:
    match = re.match(r"^\s*/?qgaudit\s+detail\s+(\S+)\s+([Jj]\d+|\d+)\s*$", message_str)
    if match is None:
        return None
    record_id = match.group(2)
    return match.group(1), record_id.upper() if record_id[0].isalpha() else int(record_id)


def parse_backfill_command(message_str: str) -> str | None:
    match = re.match(r"^\s*/?qgaudit\s+backfill\s+(\S+)\s*$", message_str)
    return match.group(1) if match else None


def _event_message_text(event: Any) -> str:
    for attr in ("message_str", "message", "raw_message"):
        value = getattr(event, attr, None)
        if isinstance(value, str):
            return value
    message_chain = getattr(event, "message_obj", None)
    get_plain_text = getattr(message_chain, "get_plain_text", None)
    if callable(get_plain_text):
        return str(get_plain_text())
    return ""


def _sender_id(event: Any) -> str:
    get_sender_id = getattr(event, "get_sender_id", None)
    if callable(get_sender_id):
        return str(get_sender_id())
    value = getattr(event, "sender_id", "")
    return str(value)


def _platform_id(event: Any) -> str | None:
    get_platform_id = getattr(event, "get_platform_id", None)
    if callable(get_platform_id):
        value = get_platform_id()
        return str(value) if value is not None else None
    value = getattr(event, "platform_id", None)
    return str(value) if value is not None else None


def _timestamp(value: Any, default: int) -> int:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return default
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    return timestamp if timestamp > 0 else default


def _audit_database_path() -> str | Path:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
    except (ImportError, ModuleNotFoundError):
        return ":memory:"
    return Path(get_astrbot_data_path()) / "plugin_data" / _PLUGIN_NAME / "audit.sqlite3"


def _tracks_requests(group_config: dict[str, Any]) -> bool:
    return bool(
        group_config.get("enabled", False)
        or group_config.get("audit_log_enabled", True)
        or group_config.get("auto_set_card", False)
    )


@dataclass
class _PendingEmptyReview:
    requests: dict[str, tuple[JoinRequest, int | None]] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None


@register("qq_group_auditor", "Junie", "QQ group join request auditor", "0.2.10")
class QQGroupAuditorPlugin(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context=context, config=config)
        self.context = context
        self.config = normalize_config(dict(config or {}))
        self._reconcile_task: asyncio.Task[None] | None = None
        self._application_tasks: dict[int, asyncio.Task[None]] = {}
        self._review_locks: dict[int, asyncio.Lock] = {}
        self._sync_locks: dict[str, asyncio.Lock] = {}
        self._sync_next_at: dict[str, float] = {}
        self._sync_failures: dict[str, int] = {}
        self._pending_empty_reviews: dict[tuple[str, str, str], _PendingEmptyReview] = {}
        self._empty_answer_tasks: set[asyncio.Task[None]] = set()
        self._member_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self._backfill_locks: dict[str, asyncio.Lock] = {}
        self._action_tasks: set[asyncio.Task] = set()
        self._stopped = False
        self._lookup_cache: dict[tuple, tuple[float, Any]] = {}
        self._lookup_locks: dict[tuple, asyncio.Lock] = {}
        self.guard = get_guard(context, Path(_audit_database_path()).parent.parent)
        try:
            self.audit_store: AuditStore | None = AuditStore(_audit_database_path())
        except Exception:
            logger.exception("failed to initialize audit database")
            self.audit_store = None

    async def initialize(self) -> None:
        if self.audit_store is not None and self._reconcile_task is None:
            self._reconcile_task = asyncio.create_task(self._reconcile_loop())

    async def terminate(self) -> None:
        self._stopped = True
        tasks = list(self._action_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        empty_tasks = list(self._empty_answer_tasks)
        self._pending_empty_reviews.clear()
        for task in empty_tasks:
            task.cancel()
        if empty_tasks:
            await asyncio.gather(*empty_tasks, return_exceptions=True)
        self._empty_answer_tasks.clear()
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconcile_task
            self._reconcile_task = None
        application_tasks = list(self._application_tasks.values())
        self._application_tasks.clear()
        for task in application_tasks:
            task.cancel()
        if application_tasks:
            await asyncio.gather(*application_tasks, return_exceptions=True)
        if self.audit_store is not None:
            self.audit_store.close()
            self.audit_store = None

    async def _run_guarded(self, platform_id, action, *, delay, gap=None, key=None,
                           dedup_seconds=604800, priority=1, group=None, label="自动操作"):
        if self._stopped:
            raise ActionDeferred("插件正在重载")
        pacing = self.config["automation_pacing"]
        account_gap = delay_range(pacing, "action_gap")
        spacing = tuple(max(account, operation) for account, operation
                        in zip(account_gap, gap or account_gap))
        task = asyncio.current_task()
        self._action_tasks.add(task)
        async def checked_action():
            if self._stopped:
                raise self.guard.deferred_error("插件正在重载")
            return await action()
        try:
            return await self.guard.run(
                account=platform_id or "aiocqhttp",
                online=lambda: is_onebot_online(self.context, platform_id=platform_id or "aiocqhttp"),
                action=checked_action, config=pacing, delay=delay,
                gap=spacing, key=key,
                dedup_seconds=dedup_seconds,
                priority=priority, group=group, label=label,
            )
        except self.guard.deferred_error as exc:
            logger.info("QQ 操作暂缓或跳过：账号=%s，任务=%s，群=%s，原因=%s",
                        platform_id, label, group or "其他", exc)
            raise ActionDeferred(str(exc)) from exc
        finally:
            self._action_tasks.discard(task)

    async def _send_notice(self, group_config, text, platform_id, *, check_request=None):
        pacing = self.config["automation_pacing"]
        for qq_id in dict.fromkeys(group_config.get("admin_qq_ids") or []):
            async def send():
                if check_request is not None:
                    try:
                        check_request()
                    except ActionDeferred as exc:
                        raise self.guard.deferred_error(str(exc)) from exc
                await send_admin_notice(self.context, [qq_id], text, platform_name=platform_id or "aiocqhttp")
            try:
                await self._run_guarded(
                    platform_id,
                    send,
                    delay=delay_range(pacing, "notice"), gap=delay_range(pacing, "notice"),
                    key=f"notice:{qq_id}:{text}", dedup_seconds=pacing["notice_dedup_seconds"],
                    priority=2, group=group_config.get("group_id"), label="管理员通知",
                )
            except ActionDeferred:
                if check_request is not None:
                    check_request()
                logger.info("审核通知已跳过：QQ 离线或账号冷却中")

    async def _cached_lookup(self, key, action):
        async with self._lookup_locks.setdefault(key, asyncio.Lock()):
            cached = self._lookup_cache.get(key)
            if cached and time.monotonic() < cached[0]:
                return cached[1]
            value = await action()
            self._lookup_cache[key] = (time.monotonic() + self.config["automation_pacing"]["lookup_cache_seconds"], value)
            return value

    def _finish_obsolete_request(self, request, application_id, action, reason):
        assert self.audit_store is not None
        if application_id is not None and not self.audit_store.has_review_action(application_id):
            self.audit_store.record_action(
                application_id=application_id, kind="review", action=action,
                actor_qq=request.self_id, source="request_lifecycle", status="completed", reason=reason,
            )
            logger.info("群审核请求已结束：记录 #%s，%s", application_id, reason)

    def _check_review_request(self, request, application_id):
        if self._stopped:
            raise ActionDeferred("插件正在重载")
        if find_group_config(self.config, request.group_id) is None:
            raise ActionDeferred("该群自动审核已关闭")
        if self.audit_store is None:
            raise ActionDeferred("审计数据库不可用")
        if application_id is None:
            raise ActionDeferred("申请尚未持久化，暂停审核")
        if self.audit_store.has_review_action(application_id):
            raise ActionDeferred("该申请已有处理记录")
        detail = self.audit_store.detail(group_id=request.group_id, application_id=application_id)
        if detail and any(a["kind"] == "platform" and a["status"] == "succeeded"
                          and a["action"] in {"approve", "reject"} for a in detail["actions"]):
            raise ActionDeferred("该申请已完成平台审批")
        blocker = self.audit_store.review_blocker(application_id)
        if blocker:
            self._finish_obsolete_request(request, application_id, *blocker)
            raise ActionDeferred(blocker[1])
        if detail and (detail["answer"] != request.answer or detail["request_kind"] != request.request_kind):
            # A same-flag enrichment is reviewed by its waiting handler, not by
            # the older empty-answer/invitation decision. It is not terminal.
            raise ActionDeferred("申请内容已更新，等待使用新内容审核")

    async def _execute_review_action(self, request, application_id, platform_id, *, source="plugin", **kwargs):
        try:
            self._check_review_request(request, application_id)
        except ActionDeferred as exc:
            # The guard may be shared with another plugin module instance.
            raise self.guard.deferred_error(str(exc)) from exc
        try:
            await set_group_request(self.context, flag=request.flag, sub_type=request.sub_type,
                                    platform_id=platform_id, **kwargs)
        except RequestUnavailableError as exc:
            self._finish_obsolete_request(request, application_id, "unavailable", str(exc))
            raise self.guard.deferred_error(str(exc)) from exc
        # Save the confirmed platform outcome before a potentially slow notice.
        # Another flag may already be queued, or a reload may interrupt notices.
        assert self.audit_store is not None
        self.audit_store.record_action(
            application_id=application_id, kind="platform",
            action="approve" if kwargs["approve"] else "reject", actor_qq=request.self_id,
            source=source, status="succeeded", reason="平台审批接口已确认成功",
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def handle_group_request(self, event: Any) -> None:
        try:
            request = extract_join_request(event)
        except ValueError:
            logger.warning("invalid group request event", exc_info=True)
            return
        if request is None:
            return

        group_policy = find_group_policy(self.config, request.group_id)
        if group_policy is None:
            return

        platform_id = _platform_id(event)
        if self.audit_store is None:
            logger.warning("审计数据库不可用，暂停自动审核")
            return
        existing = self.audit_store.get_application_id_by_flag(
            platform_id=platform_id or "aiocqhttp", self_id=request.self_id, flag=request.flag,
        )
        if existing is not None and self.audit_store.has_review_action(existing):
            return
        application_id: int | None = None
        if self.audit_store is not None and _tracks_requests(group_policy):
            try:
                request, application_id, already_reviewed = await self._persist_request(
                    request,
                    group_policy,
                    platform_id,
                )
            except Exception:
                logger.exception("failed to persist group join request")
                return  # No automatic writes without a durable deduplication record.
            else:
                if already_reviewed:
                    return

        group_config = find_group_config(self.config, request.group_id)
        if group_config is None:
            return

        await self._review_application(
            group_config=group_config,
            request=request,
            application_id=application_id,
            platform_id=platform_id,
            unified_msg_origin=getattr(event, "unified_msg_origin", None),
            action_source=(
                "invite_policy" if request.request_kind == "invite" else "plugin"
            ),
        )

    async def _review_application(
        self,
        *,
        group_config: dict[str, Any],
        request: JoinRequest,
        application_id: int | None,
        platform_id: str | None,
        unified_msg_origin: str | None,
        action_source: str,
    ) -> ActionResult | None:
        kwargs = dict(
            group_config=group_config,
            request=request,
            application_id=application_id,
            platform_id=platform_id,
            unified_msg_origin=unified_msg_origin,
            action_source=action_source,
        )
        try:
            self._check_review_request(request, application_id)
        except ActionDeferred:
            return None
        if request.request_kind == "invite":
            return await self._review_application_now(**kwargs)
        if (
            application_id is not None
            and self.audit_store is not None
            and self.audit_store.has_review_action(application_id)
        ):
            return None
        key = (platform_id or "aiocqhttp", request.group_id, request.applicant_qq)
        pending = self._pending_empty_reviews.get(key)
        if request.answer.strip():
            if pending is not None:
                self._pending_empty_reviews.pop(key)
                if pending.task is not None:
                    pending.task.cancel()
                self._supersede_empty_reviews(pending, request, application_id)
            return await self._review_application_now(**kwargs)

        if pending is not None:
            # Repeated empty events do not extend the first event's deadline.
            pending.requests[request.flag] = (request, application_id)
            return None
        pending = _PendingEmptyReview(requests={request.flag: (request, application_id)})
        self._pending_empty_reviews[key] = pending

        async def wait_for_answer() -> None:
            await asyncio.sleep(_EMPTY_ANSWER_WAIT_SECONDS)
            if self._pending_empty_reviews.get(key) is not pending:
                return
            self._pending_empty_reviews.pop(key)
            latest_request, latest_id = list(pending.requests.values())[-1]
            self._supersede_empty_reviews(pending, latest_request, latest_id)
            current_config = find_group_config(self.config, latest_request.group_id)
            if current_config is None:
                return
            await self._review_application_now(
                **{
                    **kwargs,
                    "group_config": current_config,
                    "request": latest_request,
                    "application_id": latest_id,
                }
            )

        task = asyncio.create_task(wait_for_answer())
        pending.task = task
        self._empty_answer_tasks.add(task)

        def completed(done: asyncio.Task[None]) -> None:
            self._empty_answer_tasks.discard(done)
            if self._pending_empty_reviews.get(key) is pending:
                self._pending_empty_reviews.pop(key)
            if not done.cancelled() and done.exception() is not None:
                logger.warning("failed to review pending empty answer: %s", done.exception())

        task.add_done_callback(completed)
        return None

    def _supersede_empty_reviews(
        self,
        pending: _PendingEmptyReview,
        replacement: JoinRequest,
        replacement_id: int | None,
    ) -> None:
        if self.audit_store is None:
            return
        for previous, previous_id in pending.requests.values():
            if previous_id is None or previous_id == replacement_id:
                continue
            self.audit_store.record_action(
                application_id=previous_id,
                kind="review",
                action="superseded",
                actor_qq=previous.self_id,
                source="empty_answer_wait",
                status="completed",
                reason=(
                    f"等待期间收到{'有答案的' if replacement.answer.strip() else '新的空答案'}申请"
                    f"（记录 #{replacement_id}），旧请求不再审批或通知"
                ),
            )

    async def _review_application_now(
        self,
        *,
        group_config: dict[str, Any],
        request: JoinRequest,
        application_id: int | None,
        platform_id: str | None,
        unified_msg_origin: str | None,
        action_source: str,
    ) -> ActionResult | None:
        if application_id is None:
            return await self._run_application_review(
                group_config=group_config,
                request=request,
                application_id=None,
                platform_id=platform_id,
                unified_msg_origin=unified_msg_origin,
                action_source=action_source,
            )
        lock = self._review_locks.setdefault(application_id, asyncio.Lock())
        async with lock:
            if (
                self.audit_store is None
                or self.audit_store.has_review_action(application_id)
            ):
                return None
            return await self._run_application_review(
                group_config=group_config,
                request=request,
                application_id=application_id,
                platform_id=platform_id,
                unified_msg_origin=unified_msg_origin,
                action_source=action_source,
            )

    async def _run_application_review(
        self,
        *,
        group_config: dict[str, Any],
        request: JoinRequest,
        application_id: int | None,
        platform_id: str | None,
        unified_msg_origin: str | None,
        action_source: str,
    ) -> ActionResult:
        action_delay_seconds = (
            _catch_up_action_delay_seconds()
            if action_source == "plugin_catch_up"
            else 0.0
        )
        service = AuditService(
            RuntimeReviewer(self.context, unified_msg_origin),
            RuntimePlatform(
                self.context,
                platform_id=platform_id,
                action_delay_seconds=action_delay_seconds,
                executor=lambda req, **kw: self._run_guarded(
                    platform_id,
                    lambda: self._execute_review_action(req, application_id, platform_id, source=action_source, **kw),
                    delay=review_delay(self.config, group_config),
                    key=f"review:{req.group_id}:{req.applicant_qq}:{req.flag}",
                    priority=0, group=req.group_id,
                    label=f"{'邀请' if req.request_kind == 'invite' else '申请'}审批 #{application_id}",
                ),
            ),
            RuntimeNotifier(
                self.context, platform_id=platform_id, sender=self._send_notice,
                check_request=lambda: self._check_review_request(request, application_id),
            ),
            logger=logger,
            check_request=lambda: self._check_review_request(request, application_id),
        )
        result = await service.handle_request(group_config, request)
        if application_id is None:
            return result
        try:
            self._record_service_result(
                application_id,
                request,
                result,
                source=action_source,
            )
        except Exception:
            logger.exception("failed to persist group review result")
            return result
        if (
            result.platform_action == "approve"
            and result.platform_status == "succeeded"
            and group_config.get("auto_set_card", False)
            and request.request_kind != "invite"
        ):
            confirmed = await self._reconcile_application_member(application_id)
            if confirmed in {None, "failed", "not_in_group"}:
                self._schedule_application_reconciliation(application_id)
        return result

    async def _persist_request(
        self,
        request: JoinRequest,
        group_config: dict[str, Any],
        platform_id: str | None,
    ) -> tuple[JoinRequest, int, bool]:
        question = str(group_config.get("application_question") or "").strip()
        question_source = "config" if question else "unknown"
        # Register new flags before network lookups so queued older actions see
        # them even while question/nickname enrichment is slow.
        assert self.audit_store is not None
        self.audit_store.record_application(
            platform_id=platform_id or "aiocqhttp", request=request,
            question=question, question_source=question_source,
            review_prompt=str(group_config.get("review_prompt") or ""),
        )
        try:
            platform_question = await self._cached_lookup(
                ("question", platform_id, request.group_id),
                lambda: get_group_question(self.context, group_id=request.group_id, platform_id=platform_id),
            )
        except Exception:
            logger.debug("failed to fetch group application question", exc_info=True)
        else:
            if platform_question:
                question = platform_question
                question_source = "platform"
        raw_comment = request.raw_comment or request.answer
        needs_nickname = "{nickname}" in str(
            group_config.get("card_template") or "{nickname}"
        )
        if (
            group_config.get("auto_set_card", False)
            and needs_nickname
            and not request.nickname
            and request.request_kind != "invite"
        ):
            try:
                nickname = await self._cached_lookup(
                    ("nickname", platform_id, request.applicant_qq),
                    lambda: get_user_nickname(self.context, user_id=request.applicant_qq, platform_id=platform_id),
                )
            except PlatformActionError:
                logger.debug("failed to fetch applicant QQ nickname", exc_info=True)
            else:
                request = replace(request, nickname=nickname)
        request = replace(
            request,
            question=question,
            answer=extract_application_answer(raw_comment),
            raw_comment=raw_comment,
        )
        assert self.audit_store is not None
        application_id, _ = self.audit_store.record_application(
            platform_id=platform_id or "aiocqhttp",
            request=request,
            question=question,
            question_source=question_source,
            review_prompt=str(group_config.get("review_prompt") or ""),
        )
        detail = self.audit_store.detail(group_id=request.group_id, application_id=application_id)
        if detail:
            request = replace(request, answer=detail["answer"], raw_comment=detail["raw_comment"],
                              request_kind=detail["request_kind"], requested_at=detail["requested_at"])
        return request, application_id, self.audit_store.has_review_action(application_id)

    def _record_service_result(
        self,
        application_id: int,
        request: JoinRequest,
        result: Any,
        *,
        source: str = "plugin",
    ) -> None:
        if self.audit_store is None:
            return
        if result.review_action:
            self.audit_store.record_action(
                application_id=application_id,
                kind="review",
                action=result.review_action,
                actor_qq=request.self_id,
                source=source,
                status="failed" if result.review_action == "error" else "completed",
                reason=result.reason,
            )
        if result.platform_action:
            self.audit_store.record_action(
                application_id=application_id,
                kind="platform",
                action=result.platform_action,
                actor_qq=request.self_id,
                source=source,
                status=result.platform_status,
                reason=result.reason,
            )
        elif result.review_action == "ignore":
            self.audit_store.record_action(
                application_id=application_id,
                kind="platform",
                action="no_action",
                actor_qq=request.self_id,
                source=source,
                status="observed",
                reason=(
                    result.reason
                    if source == "invite_policy"
                    else "配置为 ignore，未调用平台审批接口"
                ),
            )

    @filter.event_message_type(filter.EventMessageType.ALL)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def handle_group_membership_notice(self, event: Any) -> None:
        try:
            increase = extract_group_member_increase(event)
            decrease = extract_group_member_decrease(event)
        except ValueError:
            logger.warning("invalid group membership notice", exc_info=True)
            return
        if increase is not None:
            await self._handle_member_increase(event, increase)
        elif decrease is not None:
            self._handle_member_decrease(event, decrease)

    async def _handle_member_increase(self, event: Any, increase: Any) -> None:
        group_config = find_group_policy(self.config, increase.group_id)
        if group_config is None or not _tracks_requests(group_config):
            return
        platform_id = _platform_id(event)
        nickname = ""
        if group_config.get("auto_set_card", False) and "{nickname}" in str(group_config.get("card_template") or "{nickname}"):
            try:
                nickname = await self._cached_lookup(
                    ("nickname", platform_id, increase.user_id),
                    lambda: get_user_nickname(self.context, user_id=increase.user_id, platform_id=platform_id),
                )
            except PlatformActionError:
                logger.debug("failed to load QQ nickname", exc_info=True)
        member_info = GroupMemberInfo(
            nickname=nickname,
            card="",
            join_time=increase.occurred_at,
        )
        result = await self._process_member_increase(
            group_config=group_config,
            increase=increase,
            platform_id=platform_id,
            member_info=member_info,
            action_source="group_increase",
        )
        if result in {"failed", "deferred"} and self.audit_store is not None:
            application = self.audit_store.find_application_for_member(
                platform_id=platform_id or "aiocqhttp",
                group_id=increase.group_id,
                user_id=increase.user_id,
                joined_at=increase.occurred_at,
            )
            if application is not None:
                self._schedule_application_reconciliation(int(application["id"]))

    async def _process_member_increase(
        self,
        *,
        group_config: dict[str, Any],
        increase: GroupMemberIncrease,
        platform_id: str | None,
        member_info: GroupMemberInfo,
        member_error: str = "",
        application_id_hint: int | None = None,
        action_source: str,
        force_card: bool = False,
        notify_error: bool = True,
        preapplied_card: str = "",
    ) -> str:
        recorded_join = self._record_member_increase(
            increase=increase, platform_id=platform_id, member_info=member_info,
            application_id_hint=application_id_hint, action_source=action_source,
        )
        normalized_platform_id = platform_id or "aiocqhttp"
        lock_key = (normalized_platform_id, increase.group_id, increase.user_id)
        lock = self._member_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            return await self._process_member_increase_locked(
                group_config=group_config, increase=increase, platform_id=platform_id,
                member_info=member_info, member_error=member_error,
                application_id_hint=application_id_hint, action_source=action_source,
                force_card=force_card, notify_error=notify_error, preapplied_card=preapplied_card,
                recorded_join=recorded_join,
            )

    def _record_member_increase(
        self, *, increase, platform_id, member_info, application_id_hint, action_source,
    ) -> tuple[int | None, int | None, bool]:
        """Persist membership observations before any card queue can delay them."""
        normalized_platform_id = platform_id or "aiocqhttp"
        membership_id: int | None = None
        application_id: int | None = application_id_hint
        created = True
        if self.audit_store is not None:
            try:
                membership_id, application_id, created = self.audit_store.record_join(
                    platform_id=normalized_platform_id,
                    event=increase,
                    nickname=member_info.nickname,
                    old_card=member_info.card,
                    application_id_hint=application_id_hint,
                    correlation_hint=action_source,
                )
                if application_id is not None:
                    confirmation_reason = (
                        "通过群名片设置接口确认已入群"
                        if action_source.endswith("_direct")
                        else ""
                    )
                    self.audit_store.record_action(
                        application_id=application_id,
                        kind="platform",
                        action=(
                            "approve" if increase.sub_type == "approve" else "invite"
                        ),
                        actor_qq=increase.operator_id,
                        source=action_source,
                        status="observed",
                        reason=confirmation_reason,
                        occurred_at=increase.occurred_at,
                    )
            except Exception:
                logger.exception("failed to persist group increase event")

        return membership_id, application_id, created

    async def _process_member_increase_locked(
        self,
        *,
        group_config: dict[str, Any],
        increase: GroupMemberIncrease,
        platform_id: str | None,
        member_info: GroupMemberInfo,
        member_error: str = "",
        application_id_hint: int | None = None,
        action_source: str,
        force_card: bool = False,
        notify_error: bool = True,
        preapplied_card: str = "",
        recorded_join: tuple[int | None, int | None, bool] | None = None,
    ) -> str:
        """Record the join/card outcome while holding this member's shared lock."""
        membership_id, application_id, created = recorded_join or self._record_member_increase(
            increase=increase, platform_id=platform_id, member_info=member_info,
            application_id_hint=application_id_hint, action_source=action_source,
        )

        auto_card_enabled = bool(
            group_config.get("enabled", True)
            and group_config.get("auto_set_card", False)
        )
        invited_membership = increase.sub_type == "invite" or (
            self.audit_store is not None
            and application_id is not None
            and self.audit_store.application_is_invite(application_id)
        )
        if invited_membership:
            return "invited"
        if not auto_card_enabled:
            return "disabled"
        if not created and not force_card:
            return "already_seen"
        if (
            self.audit_store is not None
            and membership_id is not None
            and self.audit_store.has_successful_card_operation(membership_id)
        ):
            return "already_done"
        return await self._apply_member_card(
            group_config=group_config,
            increase=increase,
            platform_id=platform_id,
            member_info=member_info,
            member_error=member_error,
            membership_id=membership_id,
            application_id=application_id,
            notify_error=notify_error,
            preapplied_card=preapplied_card,
        )

    def _handle_member_decrease(self, event: Any, decrease: Any) -> None:
        group_config = find_group_policy(self.config, decrease.group_id)
        if group_config is None or not group_config.get("audit_log_enabled", True):
            return
        if self.audit_store is not None:
            try:
                self.audit_store.record_leave(
                    platform_id=_platform_id(event) or "aiocqhttp",
                    event=decrease,
                )
            except Exception:
                logger.exception("failed to persist group decrease event")

    async def _apply_member_card(
        self,
        *,
        group_config: dict[str, Any],
        increase: Any,
        platform_id: str | None,
        member_info: GroupMemberInfo,
        member_error: str,
        membership_id: int | None,
        application_id: int | None,
        notify_error: bool,
        preapplied_card: str = "",
    ) -> str:
        template = str(group_config.get("card_template") or "{nickname}")
        application = None
        if self.audit_store is not None and application_id is not None:
            application = self.audit_store.detail(
                group_id=increase.group_id,
                application_id=application_id,
            )
        question = str((application or {}).get("question") or "")
        stored_answer = str((application or {}).get("answer") or "")
        raw_comment = str((application or {}).get("raw_comment") or stored_answer)
        answer = extract_application_answer(raw_comment)
        nickname = member_info.nickname or str(
            (application or {}).get("nickname") or ""
        )
        if (
            self.audit_store is not None
            and application_id is not None
            and answer != stored_answer
        ):
            self.audit_store.update_application_answer(application_id, answer)
        target_card = ""
        error = member_error
        status = "failed" if error else "pending"
        result = "failed" if error else "pending"
        if not error and member_info.card_changeable is False:
            error = "平台报告该成员的群名片不可修改"
            status = "skipped"
            result = "skipped"
        if not error:
            try:
                target_card = render_card(
                    template,
                    qq=increase.user_id,
                    nickname=nickname,
                    question=question,
                    answer=answer,
                    joined_at=member_info.join_time or increase.occurred_at,
                )
            except CardTemplateError as exc:
                error = str(exc)
                status = "skipped"
                result = "skipped"
        if not error and member_info.card and not preapplied_card:
            status = "skipped"
            result = "existing_card"
        elif not error and preapplied_card:
            if target_card != preapplied_card:
                error = "群名片模板在设置过程中发生变化"
                status = "failed"
                result = "failed"
            else:
                status = "succeeded"
                result = "succeeded"
        elif not error:
            if member_info.card == target_card:
                status = "succeeded"
                result = "already_target"
            else:
                try:
                    fresh = await self._write_card_if_empty(
                        group_id=increase.group_id,
                        user_id=increase.user_id,
                        card=target_card,
                        platform_id=platform_id,
                    )
                    if fresh.card or fresh.card_changeable is False:
                        member_info = fresh
                        status = "skipped"
                        result = "existing_card" if fresh.card else "skipped"
                        target_card = ""
                except ActionDeferred:
                    return "deferred"
                except Exception as exc:
                    error = str(exc)
                    status = "failed"
                    result = "failed"
                else:
                    if result not in {"existing_card", "skipped"}:
                        status = "succeeded"
                        result = "succeeded"
        if self.audit_store is not None and membership_id is not None:
            try:
                self.audit_store.record_card_operation(
                    membership_id=membership_id,
                    template=template,
                    old_card=member_info.card,
                    target_card=target_card,
                    status=status,
                    error=error,
                )
            except Exception:
                logger.exception("failed to persist group card operation")
        if error and notify_error:
            await self._notify_card_error(
                group_config,
                increase.group_id,
                increase.user_id,
                error,
                platform_id,
            )
        return result

    async def _write_card_if_empty(self, *, group_id, user_id, card, platform_id):
        async def write():
            group = find_group_config(self.config, group_id)
            if group is None or not group.get("auto_set_card", False):
                raise self.guard.deferred_error("自动修改名片已关闭")
            member = await get_group_member_info(
                self.context, group_id=group_id, user_id=user_id, platform_id=platform_id,
            )
            if member.card or member.card_changeable is False:
                return member
            desired = card(member) if callable(card) else card
            await set_group_card(self.context, group_id=group_id, user_id=user_id,
                                 card=desired, platform_id=platform_id)
            return member
        return await self._run_guarded(
            platform_id, write, delay=delay_range(self.config["automation_pacing"], "card"),
            priority=1, group=group_id, label="修改群名片",
        )

    async def _set_card_from_application(
        self,
        *,
        group_config: dict[str, Any],
        application: dict[str, Any],
        action_source: str,
        notify_error: bool,
    ) -> str:
        group_id = str(application.get("group_id") or "")
        user_id = str(application.get("applicant_qq") or "")
        platform_id = str(application.get("platform_id") or "aiocqhttp")
        lock_key = (platform_id, group_id, user_id)
        lock = self._member_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            # A live join handler may have completed while this reconciliation
            # waited. Re-read its membership/card outcome before queuing a write.
            if self.audit_store is None:
                return "deferred"
            current = self.audit_store.application_for_reconciliation(int(application["id"]))
            if current is None:
                return "skipped"
            application = {**application, **current}
            return await self._set_card_from_application_locked(
                group_config=group_config,
                application=application,
                action_source=action_source,
                notify_error=notify_error,
            )

    async def _set_card_from_application_locked(
        self,
        *,
        group_config: dict[str, Any],
        application: dict[str, Any],
        action_source: str,
        notify_error: bool,
    ) -> str:
        assert self.audit_store is not None
        group_id = str(application.get("group_id") or "")
        user_id = str(application.get("applicant_qq") or "")
        platform_id = str(application.get("platform_id") or "aiocqhttp")
        application_id = int(application["id"])
        if application.get("request_kind") == "invite":
            return "invited"
        if self.audit_store.has_successful_card_attempt(application_id):
            return "already_done"
        if (
            action_source == "member_reconcile_direct"
            and self.audit_store.card_attempt_count(
                application_id=application_id,
                source=action_source,
            )
            >= _MAX_AUTOMATIC_CARD_ATTEMPTS
        ):
            return "retry_exhausted"
        membership_id = application.get("membership_id")
        if membership_id is not None and self.audit_store.has_successful_card_operation(
            int(membership_id)
        ):
            return "already_done"
        known_card = str(application.get("membership_card_at_join") or "").strip()
        if membership_id is not None and known_card:
            self.audit_store.record_card_operation(
                membership_id=int(membership_id),
                template=str(group_config.get("card_template") or "{nickname}"),
                old_card=known_card,
                target_card="",
                status="skipped",
                error="成员已有群名片，未修改",
            )
            return "existing_card"

        occurred_at = int(
            application.get("membership_joined_at")
            or application.get("approval_at")
            or application.get("requested_at")
            or time.time()
        )
        increase = GroupMemberIncrease(
            group_id=group_id,
            user_id=user_id,
            operator_id=str(application.get("approval_actor_qq") or ""),
            sub_type="approve",
            occurred_at=occurred_at,
            self_id=str(application.get("self_id") or ""),
        )
        member_info = GroupMemberInfo(
            nickname=str(application.get("nickname") or ""),
            card="",
            join_time=occurred_at,
        )
        if not member_info.nickname and "{nickname}" in str(
            group_config.get("card_template") or "{nickname}"
        ):
            try:
                nickname = await get_user_nickname(
                    self.context,
                    user_id=user_id,
                    platform_id=platform_id,
                )
            except PlatformActionError:
                logger.debug("failed to load QQ nickname", exc_info=True)
            else:
                member_info = GroupMemberInfo(
                    nickname=nickname,
                    card="",
                    join_time=occurred_at,
                )
                if nickname:
                    self.audit_store.update_application_nickname(
                        int(application["id"]), nickname
                    )
        try:
            target_card = render_card(
                str(group_config.get("card_template") or "{nickname}"),
                qq=user_id,
                nickname=member_info.nickname,
                question=str(application.get("question") or ""),
                answer=extract_application_answer(
                    str(
                        application.get("raw_comment")
                        or application.get("answer")
                        or ""
                    )
                ),
                joined_at=occurred_at,
            )
        except CardTemplateError as exc:
            if notify_error:
                await self._notify_card_error(
                    group_config,
                    group_id,
                    user_id,
                    str(exc),
                    platform_id,
                )
            return "skipped"

        def render_current(member):
            nonlocal target_card
            target_card = render_card(
                str(group_config.get("card_template") or "{nickname}"),
                qq=user_id, nickname=member.nickname or member_info.nickname,
                question=str(application.get("question") or ""),
                answer=extract_application_answer(str(application.get("raw_comment") or application.get("answer") or "")),
                joined_at=member.join_time or occurred_at,
            )
            return target_card

        try:
            fresh = await self._write_card_if_empty(
                group_id=group_id,
                user_id=user_id,
                card=render_current,
                platform_id=platform_id,
            )
        except ActionDeferred:
            return "deferred"
        except PlatformActionError as exc:
            if _is_missing_group_member_error(exc):
                self._record_application_card_attempt(
                    application_id=application_id,
                    source=action_source,
                    status="not_in_group",
                    error=str(exc),
                )
                logger.debug(
                    "group member is not present yet: group=%s user=%s",
                    group_id,
                    user_id,
                )
                return "not_in_group"
            self._record_application_card_attempt(
                application_id=application_id,
                source=action_source,
                status="failed",
                error=str(exc),
            )
            logger.info(
                "direct group card update failed: group=%s user=%s error=%s",
                group_id,
                user_id,
                exc,
            )
            if notify_error:
                await self._notify_card_error(
                    group_config,
                    group_id,
                    user_id,
                    str(exc),
                    platform_id,
                )
            return "failed"

        if fresh.card_changeable is False:
            return "skipped"
        member_info = replace(fresh, nickname=fresh.nickname or member_info.nickname,
                              join_time=fresh.join_time or occurred_at)
        self._record_application_card_attempt(
            application_id=application_id,
            source=action_source,
            status="succeeded",
        )

        return await self._process_member_increase_locked(
            group_config=group_config,
            increase=increase,
            platform_id=platform_id,
            member_info=member_info,
            application_id_hint=int(application["id"]),
            action_source=action_source,
            force_card=True,
            notify_error=notify_error,
            preapplied_card="" if fresh.card else target_card,
        )

    def _record_application_card_attempt(
        self,
        *,
        application_id: int,
        source: str,
        status: str,
        error: str = "",
    ) -> None:
        if self.audit_store is None:
            return
        try:
            self.audit_store.record_card_attempt(
                application_id=application_id,
                source=source,
                status=status,
                error=error,
            )
        except Exception:
            logger.exception("failed to persist group card attempt")

    async def _notify_card_error(
        self,
        group_config: dict[str, Any],
        group_id: str,
        user_id: str,
        error: str,
        platform_id: str | None,
    ) -> None:
        text = f"自动修改群名片失败\n群号：{group_id}\n成员：{user_id}\n错误：{error}"
        try:
            await self._send_notice(group_config, text, platform_id)
        except Exception:
            logger.warning("failed to send card error notification", exc_info=True)

    async def _reconcile_application_member(
        self,
        application_id: int,
        *,
        application: dict[str, Any] | None = None,
        notify_error: bool = False,
    ) -> str | None:
        if self.audit_store is None:
            return None
        application = application or self.audit_store.application_for_reconciliation(
            application_id
        )
        if application is None:
            return None
        group_id = str(application.get("group_id") or "")
        user_id = str(application.get("applicant_qq") or "")
        group_config = find_group_config(self.config, group_id)
        if (
            group_config is None
            or not group_config.get("auto_set_card", False)
            or not group_id
            or not user_id
            or application.get("request_kind") == "invite"
        ):
            return None
        return await self._set_card_from_application(
            group_config=group_config,
            application=application,
            action_source="member_reconcile_direct",
            notify_error=notify_error,
        )

    def _schedule_application_reconciliation(self, application_id: int) -> None:
        existing = self._application_tasks.get(application_id)
        if existing is not None and not existing.done():
            return

        async def retry() -> None:
            for index, delay in enumerate(_JOIN_CONFIRM_RETRY_DELAYS):
                await asyncio.sleep(delay)
                result = await self._reconcile_application_member(
                    application_id,
                    notify_error=index == len(_JOIN_CONFIRM_RETRY_DELAYS) - 1,
                )
                if result not in {None, "failed", "not_in_group", "deferred"}:
                    return

        task = asyncio.create_task(retry())
        self._application_tasks[application_id] = task

        def completed(done: asyncio.Task[None]) -> None:
            if self._application_tasks.get(application_id) is done:
                self._application_tasks.pop(application_id, None)
            if not done.cancelled() and done.exception() is not None:
                logger.warning("failed to reconcile approved applicant: %s", done.exception())

        task.add_done_callback(completed)

    async def _reconcile_missing_joins(self, platform_id: str, now: int) -> None:
        if self.audit_store is None:
            return
        group_ids = [
            str(item.get("group_id") or "")
            for item in self.config.get("group_audits") or []
            if item.get("enabled", True) and item.get("auto_set_card", False)
        ]
        applications = self.audit_store.pending_join_applications(
            platform_id=platform_id,
            group_ids=group_ids,
            now=now,
            max_card_attempts=_MAX_AUTOMATIC_CARD_ATTEMPTS,
        )
        for application in applications:
            try:
                await self._reconcile_application_member(
                    int(application["id"]),
                    application=application,
                )
            except Exception:
                logger.exception(
                    "failed to reconcile one approved applicant: application=%s",
                    application.get("id"),
                )

    async def _backfill_group_cards(
        self,
        *,
        platform_id: str,
        group_config: dict[str, Any],
    ) -> dict[str, int]:
        assert self.audit_store is not None
        group_id = str(group_config["group_id"])
        recorded_user_ids = self.audit_store.card_candidate_user_ids(
            platform_id=platform_id,
            group_id=group_id,
        )
        applications = self.audit_store.card_backfill_applications(
            platform_id=platform_id,
            group_id=group_id,
        )
        counts = {
            "recorded": len(recorded_user_ids),
            "unmatched": len(recorded_user_ids) - len(applications),
            "succeeded": 0,
            "already_done": 0,
            "existing_card": 0,
            "skipped": 0,
            "not_in_group": 0,
            "failed": 0,
        }
        for application in applications:
            try:
                result = await self._set_card_from_application(
                    group_config=group_config,
                    application=application,
                    action_source="card_backfill_direct",
                    notify_error=False,
                )
            except Exception:
                logger.exception(
                    "failed to backfill one group member card: application=%s",
                    application.get("id"),
                )
                result = "failed"
            counts[result if result in counts else "failed"] += 1
        return counts

    async def _reconcile_loop(self) -> None:
        while True:
            try:
                if self.audit_store is not None:
                    platform_ids = onebot_platform_ids(self.context)
                    for platform_id in platform_ids:
                        await self._poll_platform(platform_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("failed to reconcile external group actions", exc_info=True)
            await asyncio.sleep(_RECONCILE_INTERVAL_SECONDS)

    async def _poll_platform(self, platform_id: str) -> None:
        if (
            self.audit_store is None
            or not self.config["background_sync_enabled"]
            or not any(_tracks_requests(g) for g in self.config["group_audits"])
        ):
            return
        lock = self._sync_locks.setdefault(platform_id, asyncio.Lock())
        if lock.locked():
            return
        async with lock:
            if time.monotonic() < self._sync_next_at.get(platform_id, 0):
                return
            interval = self.config["background_sync_interval_seconds"]
            try:
                if not await is_onebot_online(self.context, platform_id=platform_id):
                    raise PlatformActionError("QQ 未在线或 OneBot 连接已断开")
                await self._reconcile_platform(platform_id)
            except Exception as exc:
                failures = min(self._sync_failures.get(platform_id, 0) + 1, 6)
                self._sync_failures[platform_id] = failures
                delay = min(interval * 2 ** (failures - 1), max(interval, 3600))
                logger.warning(
                    "群审核后台同步暂停：平台=%s，%s 秒后重试；%s: %s",
                    platform_id, delay, type(exc).__name__, str(exc) or "请求超时",
                )
            else:
                self._sync_failures.pop(platform_id, None)
                delay = interval + random.uniform(0, self.config["automation_pacing"]["sync_jitter_seconds"])
            self._sync_next_at[platform_id] = time.monotonic() + delay

    async def _reconcile_platform(self, platform_id: str) -> None:
        if self.audit_store is None:
            return
        now = int(time.time())
        requests = await get_group_system_requests(
            self.context,
            platform_id=platform_id,
            count=100,
        )
        # Persist the entire snapshot before approving anything. Otherwise an
        # oldest-first adapter response lets an obsolete flag be acted on before
        # its replacement (or externally checked status) is even observed.
        persisted_requests = []
        for item in requests:
            try:
                await self._reconcile_system_request(
                    item=item, platform_id=platform_id, now=now, allow_catch_up=False,
                )
            except Exception:
                logger.exception("failed to persist one group system request")
            else:
                persisted_requests.append(item)
        catch_up_reviews = 0
        for item in persisted_requests:
            try:
                attempted = await self._reconcile_system_request(
                    item=item,
                    platform_id=platform_id,
                    now=now,
                    allow_catch_up=(
                        catch_up_reviews < _MAX_CATCH_UP_REVIEWS_PER_CYCLE
                    ),
                )
            except Exception:
                logger.exception("failed to reconcile one group system request")
                continue
            catch_up_reviews += int(attempted)
        await self._reconcile_missing_joins(platform_id, now)
        self.audit_store.infer_external_rejections(
            platform_id=platform_id,
            now=now,
            grace_seconds=_EXTERNAL_REJECTION_GRACE_SECONDS,
        )

    async def _reconcile_system_request(
        self,
        *,
        item: dict[str, Any],
        platform_id: str,
        now: int,
        allow_catch_up: bool,
    ) -> bool:
        assert self.audit_store is not None
        group_id = str(item.get("group_id") or "").strip()
        requester_qq = str(
            item.get("requester_uin") or item.get("user_id") or ""
        ).strip()
        applicant_qq = requester_qq or str(item.get("invitor_uin") or "").strip()
        flag = str(item.get("request_id") or "").strip()
        group_config = find_group_policy(self.config, group_id)
        if not group_id or not applicant_qq or not flag or group_config is None:
            return False
        if not _tracks_requests(group_config):
            return False
        raw_comment = str(item.get("message") or item.get("comment") or "").strip()
        # NapCat omits request_time in get_group_system_msg. Its 16-digit seq
        # contains epoch microseconds; using sync time would make old requests
        # appear newer than live requests. Other adapters retain the fallback.
        fallback_time = now
        if flag.isascii() and flag.isdigit() and len(flag) == 16:
            sequence_time = int(flag) // 1_000_000
            if 946684800 <= sequence_time <= now + 60:
                fallback_time = sequence_time
        request = JoinRequest(
            group_id=group_id,
            applicant_qq=applicant_qq,
            answer=extract_application_answer(raw_comment),
            flag=flag,
            sub_type="add",
            requested_at=_timestamp(
                item.get("request_time") or item.get("time"),
                fallback_time,
            ),
            nickname=str(item.get("requester_nick") or "").strip(),
            raw_comment=raw_comment,
            self_id=str(item.get("self_id") or "").strip(),
        )
        application_id, _ = self.audit_store.record_application(
            platform_id=platform_id,
            request=request,
            question=str(group_config.get("application_question") or ""),
            question_source=(
                "config" if group_config.get("application_question") else "unknown"
            ),
            review_prompt=str(group_config.get("review_prompt") or ""),
            observed_at=now,
        )
        if _system_request_is_checked(item):
            self.audit_store.mark_external_checked(
                application_id=application_id,
                actor_qq=str(item.get("actor") or "").strip(),
                observed_at=now,
            )
            return False
        if self.audit_store.application_is_invite(application_id):
            request = replace(request, request_kind="invite")
        detail = self.audit_store.detail(group_id=group_id, application_id=application_id)
        if detail:
            request = replace(request, answer=detail["answer"], raw_comment=detail["raw_comment"],
                              requested_at=detail["requested_at"])
        enabled_group = find_group_config(self.config, group_id)
        if (
            not allow_catch_up
            or not requester_qq
            or enabled_group is None
            or self.audit_store.has_review_action(application_id)
        ):
            return False
        try:
            await self._review_application(
                group_config=enabled_group,
                request=request,
                application_id=application_id,
                platform_id=platform_id,
                unified_msg_origin=None,
                action_source="plugin_catch_up",
            )
        except Exception:
            logger.exception(
                "failed to catch up one pending group request: application=%s",
                application_id,
            )
        return True

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @qgaudit.command("test")
    async def qgaudit_test(self, event: Any) -> None:
        parsed = parse_test_command(_event_message_text(event))
        if parsed is None:
            yield event.plain_result("用法：/qgaudit test <群号> <申请答案>")
            return

        group_id, answer = parsed
        if not is_group_admin(self.config, group_id, _sender_id(event)):
            yield event.plain_result("无权限")
            return

        group_config = find_group_config(self.config, group_id)
        if group_config is None:
            yield event.plain_result("群未配置或未启用")
            return

        request = JoinRequest(
            group_id=group_id,
            applicant_qq=_sender_id(event),
            answer=answer,
            flag="test",
            sub_type="add",
        )
        try:
            decision = await RuntimeReviewer(
                self.context,
                getattr(event, "unified_msg_origin", None),
            ).review(
                group_config=group_config,
                request=request,
            )
        except LLMReviewError as exc:
            yield event.plain_result(f"LLM审核异常：{exc}")
            return

        yield event.plain_result(f"approve={decision.approve} reason={decision.reason}")

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @qgaudit.command("backfill")
    async def qgaudit_backfill(self, event: Any) -> None:
        group_id = parse_backfill_command(_event_message_text(event))
        if group_id is None:
            yield event.plain_result("用法：/qgaudit backfill <群号>")
            return
        if not is_group_admin(self.config, group_id, _sender_id(event)):
            yield event.plain_result("无权限")
            return
        group_config = find_group_config(self.config, group_id)
        if group_config is None:
            yield event.plain_result("群未配置或未启用")
            return
        if not group_config.get("auto_set_card", False):
            yield event.plain_result("该群未开启自动修改群名片")
            return
        if self.audit_store is None:
            yield event.plain_result("审计数据库不可用")
            return

        platform_id = _platform_id(event) or "aiocqhttp"
        lock_key = f"{platform_id}:{group_id}"
        lock = self._backfill_locks.setdefault(lock_key, asyncio.Lock())
        if lock.locked():
            yield event.plain_result("该群的历史群名片补处理正在执行")
            return
        async with lock:
            try:
                counts = await self._backfill_group_cards(
                    platform_id=platform_id,
                    group_config=group_config,
                )
            except Exception:
                logger.exception("failed to backfill group member cards")
                yield event.plain_result("历史群名片补处理失败，请查看 AstrBot 日志")
                return

        yield event.plain_result(
            "历史群名片补处理完成\n"
            f"有审计记录的QQ：{counts['recorded']}\n"
            f"本次修改成功：{counts['succeeded']}\n"
            f"此前已经处理：{counts['already_done']}\n"
            f"已有群名片，未修改：{counts['existing_card']}\n"
            f"因模板或平台标记不可修改而跳过：{counts['skipped']}\n"
            f"当前不在群内：{counts['not_in_group']}\n"
            f"其他设置失败：{counts['failed']}\n"
            f"无可用的通过记录：{counts['unmatched']}"
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @qgaudit.command("history")
    async def qgaudit_history(self, event: Any) -> None:
        parsed = parse_history_command(_event_message_text(event))
        if parsed is None:
            yield event.plain_result("用法：/qgaudit history <群号> <QQ号> [条数]")
            return
        group_id, applicant_qq, limit = parsed
        if not is_group_admin(self.config, group_id, _sender_id(event)):
            yield event.plain_result("无权限")
            return
        if self.audit_store is None:
            yield event.plain_result("审计数据库不可用")
            return
        platform_id = _platform_id(event)
        if platform_id:
            with contextlib.suppress(Exception):
                await self._poll_platform(platform_id)
        try:
            records = self.audit_store.history(
                group_id=group_id,
                applicant_qq=applicant_qq,
                limit=limit,
            )
        except Exception:
            logger.exception("failed to query audit history")
            yield event.plain_result("查询审计记录失败")
            return
        yield event.plain_result(format_history(records))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @qgaudit.command("detail")
    async def qgaudit_detail(self, event: Any) -> None:
        parsed = parse_detail_command(_event_message_text(event))
        if parsed is None:
            yield event.plain_result("用法：/qgaudit detail <群号> <记录ID>")
            return
        group_id, application_id = parsed
        if not is_group_admin(self.config, group_id, _sender_id(event)):
            yield event.plain_result("无权限")
            return
        if self.audit_store is None:
            yield event.plain_result("审计数据库不可用")
            return
        try:
            record = self.audit_store.detail(
                group_id=group_id,
                application_id=application_id,
            )
        except Exception:
            logger.exception("failed to query audit detail")
            yield event.plain_result("查询审计记录失败")
            return
        yield event.plain_result(format_detail(record))
