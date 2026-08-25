from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from dataclasses import replace
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
    )
    from .qq_group_auditor.models import (
        GroupMemberIncrease,
        GroupMemberInfo,
        JoinRequest,
        ReviewDecision,
    )
    from .qq_group_auditor.notifier import format_notice, send_admin_notice
    from .qq_group_auditor.platform import (
        extract_group_member_decrease,
        extract_group_member_increase,
        extract_join_request,
        get_group_member_info,
        get_group_member_list,
        get_group_question,
        get_group_system_requests,
        set_group_card,
        set_group_request,
    )
    from .qq_group_auditor.reviewer import LLMReviewError, review_answer
    from .qq_group_auditor.service import AuditService
    from .qq_group_auditor.text import extract_application_answer
except ImportError:  # pragma: no cover - supports direct local imports in tests/dev.
    from qq_group_auditor.audit_store import AuditStore
    from qq_group_auditor.audit_text import format_detail, format_history
    from qq_group_auditor.card import CardTemplateError, render_card
    from qq_group_auditor.config import (
        find_group_config,
        find_group_policy,
        is_group_admin,
        normalize_config,
    )
    from qq_group_auditor.models import (
        GroupMemberIncrease,
        GroupMemberInfo,
        JoinRequest,
        ReviewDecision,
    )
    from qq_group_auditor.notifier import format_notice, send_admin_notice
    from qq_group_auditor.platform import (
        extract_group_member_decrease,
        extract_group_member_increase,
        extract_join_request,
        get_group_member_info,
        get_group_member_list,
        get_group_question,
        get_group_system_requests,
        set_group_card,
        set_group_request,
    )
    from qq_group_auditor.reviewer import LLMReviewError, review_answer
    from qq_group_auditor.service import AuditService
    from qq_group_auditor.text import extract_application_answer


logger = logging.getLogger(__name__)

_DEEPSEEK_JSON_MAX_TOKENS = 512
_EXTERNAL_REJECTION_GRACE_SECONDS = 120
_RECONCILE_INTERVAL_SECONDS = 60
_JOIN_CONFIRM_RETRY_DELAYS = (1, 3, 8, 20)
_PLUGIN_NAME = "astrbot_plugin_qq_group_auditor"


def _is_deepseek_provider_id(provider_id: Any) -> bool:
    source_id = str(provider_id).strip().lower().partition("/")[0]
    return source_id == "deepseek" or source_id.startswith("deepseek-")


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
    def __init__(self, context: Context, platform_id: str | None = None) -> None:
        self.context = context
        self.platform_id = platform_id

    async def set_group_request(
        self,
        request: JoinRequest,
        *,
        approve: bool,
        reason: str,
    ) -> None:
        await set_group_request(
            self.context,
            flag=request.flag,
            sub_type=request.sub_type,
            approve=approve,
            reason=reason,
            platform_id=self.platform_id,
        )


class RuntimeNotifier:
    def __init__(self, context: Context, platform_id: str | None = None) -> None:
        self.context = context
        self.platform_id = platform_id

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
            await send_admin_notice(
                self.context,
                list(group_config.get("admin_qq_ids") or []),
                text,
                platform_name=self.platform_id or "aiocqhttp",
            )
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
        group_config.get("audit_log_enabled", True)
        or group_config.get("auto_set_card", False)
    )


@register("qq_group_auditor", "Junie", "QQ group join request auditor", "0.2.1")
class QQGroupAuditorPlugin(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context=context, config=config)
        self.context = context
        self.config = normalize_config(dict(config or {}))
        self._reconcile_task: asyncio.Task[None] | None = None
        self._application_tasks: dict[int, asyncio.Task[None]] = {}
        self._member_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self._backfill_locks: dict[str, asyncio.Lock] = {}
        try:
            self.audit_store: AuditStore | None = AuditStore(_audit_database_path())
        except Exception:
            logger.exception("failed to initialize audit database")
            self.audit_store = None

    async def initialize(self) -> None:
        if self.audit_store is not None and self._reconcile_task is None:
            self._reconcile_task = asyncio.create_task(self._reconcile_loop())

    async def terminate(self) -> None:
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
            else:
                if already_reviewed:
                    return

        group_config = find_group_config(self.config, request.group_id)
        if group_config is None:
            return

        service = AuditService(
            RuntimeReviewer(self.context, getattr(event, "unified_msg_origin", None)),
            RuntimePlatform(self.context, platform_id=platform_id),
            RuntimeNotifier(self.context, platform_id=platform_id),
            logger=logger,
        )
        result = await service.handle_request(group_config, request)
        if application_id is not None:
            try:
                self._record_service_result(application_id, request, result)
            except Exception:
                logger.exception("failed to persist group review result")
            else:
                if (
                    result.platform_action == "approve"
                    and result.platform_status == "succeeded"
                    and group_config.get("auto_set_card", False)
                ):
                    confirmed = await self._reconcile_application_member(application_id)
                    if confirmed in {None, "failed"}:
                        self._schedule_application_reconciliation(application_id)

    async def _persist_request(
        self,
        request: JoinRequest,
        group_config: dict[str, Any],
        platform_id: str | None,
    ) -> tuple[JoinRequest, int, bool]:
        question = str(group_config.get("application_question") or "").strip()
        question_source = "config" if question else "unknown"
        try:
            platform_question = await get_group_question(
                self.context,
                group_id=request.group_id,
                platform_id=platform_id,
            )
        except Exception:
            logger.debug("failed to fetch group application question", exc_info=True)
        else:
            if platform_question:
                question = platform_question
                question_source = "platform"
        raw_comment = request.raw_comment or request.answer
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
        return request, application_id, self.audit_store.has_review_action(application_id)

    def _record_service_result(self, application_id: int, request: JoinRequest, result: Any) -> None:
        if self.audit_store is None:
            return
        if result.review_action:
            self.audit_store.record_action(
                application_id=application_id,
                kind="review",
                action=result.review_action,
                actor_qq=request.self_id,
                source="plugin",
                status="failed" if result.review_action == "error" else "completed",
                reason=result.reason,
            )
        if result.platform_action:
            self.audit_store.record_action(
                application_id=application_id,
                kind="platform",
                action=result.platform_action,
                actor_qq=request.self_id,
                source="plugin",
                status=result.platform_status,
                reason=result.reason,
            )
        elif result.review_action == "ignore":
            self.audit_store.record_action(
                application_id=application_id,
                kind="platform",
                action="no_action",
                actor_qq=request.self_id,
                source="plugin",
                status="observed",
                reason="配置为 ignore，未调用平台审批接口",
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
        member_info = GroupMemberInfo(nickname="", card="")
        member_error = ""
        try:
            member_info = await self._load_member_info(
                increase.group_id,
                increase.user_id,
                platform_id,
                retry=bool(group_config.get("auto_set_card", False)),
            )
        except Exception as exc:
            member_error = str(exc)
            logger.warning("failed to load new group member info", exc_info=True)
        await self._process_member_increase(
            group_config=group_config,
            increase=increase,
            platform_id=platform_id,
            member_info=member_info,
            member_error=member_error,
            action_source="group_increase",
        )

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
    ) -> str:
        normalized_platform_id = platform_id or "aiocqhttp"
        lock_key = (normalized_platform_id, increase.group_id, increase.user_id)
        lock = self._member_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
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
                        self.audit_store.record_action(
                            application_id=application_id,
                            kind="platform",
                            action=(
                                "approve" if increase.sub_type == "approve" else "invite"
                            ),
                            actor_qq=increase.operator_id,
                            source=action_source,
                            status="observed",
                            reason=(
                                "通过成员信息接口确认已入群"
                                if action_source != "group_increase"
                                else ""
                            ),
                            occurred_at=increase.occurred_at,
                        )
                except Exception:
                    logger.exception("failed to persist group increase event")

            auto_card_enabled = bool(
                group_config.get("enabled", True)
                and group_config.get("auto_set_card", False)
            )
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

    async def _load_member_info(
        self,
        group_id: str,
        user_id: str,
        platform_id: str | None,
        *,
        retry: bool,
    ) -> GroupMemberInfo:
        delays = (0, 1, 3) if retry else (0,)
        last_error: Exception | None = None
        for delay in delays:
            if delay:
                await asyncio.sleep(delay)
            try:
                return await get_group_member_info(
                    self.context,
                    group_id=group_id,
                    user_id=user_id,
                    platform_id=platform_id,
                )
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

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
                    nickname=member_info.nickname,
                    question=question,
                    answer=answer,
                    joined_at=member_info.join_time or increase.occurred_at,
                )
            except CardTemplateError as exc:
                error = str(exc)
                status = "skipped"
                result = "skipped"
        if not error:
            if member_info.card == target_card:
                status = "succeeded"
                result = "already_target"
            else:
                try:
                    await set_group_card(
                        self.context,
                        group_id=increase.group_id,
                        user_id=increase.user_id,
                        card=target_card,
                        platform_id=platform_id,
                    )
                except Exception as exc:
                    error = str(exc)
                    status = "failed"
                    result = "failed"
                else:
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
            await send_admin_notice(
                self.context,
                list(group_config.get("admin_qq_ids") or []),
                text,
                platform_name=platform_id or "aiocqhttp",
            )
        except Exception:
            logger.warning("failed to send card error notification", exc_info=True)

    @staticmethod
    def _application_matches_member(
        application: dict[str, Any],
        member_info: GroupMemberInfo,
    ) -> bool:
        if member_info.join_time <= 0:
            return bool(application.get("explicitly_approved"))
        requested_at = int(application.get("requested_at") or 0)
        return (
            member_info.join_time - 30 * 24 * 60 * 60
            <= requested_at
            <= member_info.join_time + 60
        )

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
        platform_id = str(application.get("platform_id") or "aiocqhttp")
        group_config = find_group_config(self.config, group_id)
        if (
            group_config is None
            or not group_config.get("auto_set_card", False)
            or not group_id
            or not user_id
        ):
            return None
        try:
            member_info = await get_group_member_info(
                self.context,
                group_id=group_id,
                user_id=user_id,
                platform_id=platform_id,
            )
        except Exception:
            logger.debug(
                "approved applicant is not a confirmed group member yet: group=%s user=%s",
                group_id,
                user_id,
                exc_info=True,
            )
            return None
        if not self._application_matches_member(application, member_info):
            return None

        occurred_at = (
            member_info.join_time
            or int(application.get("approval_at") or 0)
            or int(application.get("requested_at") or time.time())
        )
        increase = GroupMemberIncrease(
            group_id=group_id,
            user_id=user_id,
            operator_id=str(application.get("approval_actor_qq") or ""),
            sub_type="approve",
            occurred_at=occurred_at,
            self_id=str(application.get("self_id") or ""),
        )
        return await self._process_member_increase(
            group_config=group_config,
            increase=increase,
            platform_id=platform_id,
            member_info=member_info,
            application_id_hint=application_id,
            action_source="member_reconcile",
            force_card=True,
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
                if result not in {None, "failed"}:
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
        )
        for application in applications:
            application_id = int(application["id"])
            result = await self._reconcile_application_member(
                application_id,
                application=application,
            )
            if result == "failed":
                self._schedule_application_reconciliation(application_id)

    async def _backfill_group_cards(
        self,
        *,
        platform_id: str,
        group_config: dict[str, Any],
    ) -> dict[str, int]:
        assert self.audit_store is not None
        group_id = str(group_config["group_id"])
        members = await get_group_member_list(
            self.context,
            group_id=group_id,
            platform_id=platform_id,
        )
        current_members = {member.user_id: member for member in members}
        candidate_ids = self.audit_store.card_candidate_user_ids(
            platform_id=platform_id,
            group_id=group_id,
        )
        counts = {
            "recorded": len(candidate_ids),
            "not_in_group": 0,
            "unmatched": 0,
            "succeeded": 0,
            "already_target": 0,
            "already_done": 0,
            "skipped": 0,
            "failed": 0,
        }
        now = int(time.time())
        for user_id in candidate_ids:
            member = current_members.get(user_id)
            if member is None:
                counts["not_in_group"] += 1
                continue
            application = self.audit_store.find_application_for_member(
                platform_id=platform_id,
                group_id=group_id,
                user_id=user_id,
                joined_at=member.join_time,
                now=now,
            )
            if application is None:
                counts["unmatched"] += 1
                continue
            occurred_at = (
                member.join_time
                or int(application.get("approval_at") or 0)
                or int(application.get("requested_at") or now)
            )
            result = await self._process_member_increase(
                group_config=group_config,
                increase=GroupMemberIncrease(
                    group_id=group_id,
                    user_id=user_id,
                    operator_id=str(application.get("approval_actor_qq") or ""),
                    sub_type="approve",
                    occurred_at=occurred_at,
                    self_id=str(application.get("self_id") or ""),
                ),
                platform_id=platform_id,
                member_info=member.info(),
                application_id_hint=int(application["id"]),
                action_source="card_backfill",
                force_card=True,
                notify_error=False,
            )
            counts[result if result in counts else "failed"] += 1
        return counts

    async def _reconcile_loop(self) -> None:
        while True:
            try:
                if self.audit_store is not None:
                    for platform_id in self.audit_store.platform_ids():
                        await self._reconcile_platform(platform_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("failed to reconcile external group actions", exc_info=True)
            await asyncio.sleep(_RECONCILE_INTERVAL_SECONDS)

    async def _reconcile_platform(self, platform_id: str) -> None:
        if self.audit_store is None:
            return
        now = int(time.time())
        try:
            requests = await get_group_system_requests(
                self.context,
                platform_id=platform_id,
                count=100,
            )
        except Exception:
            logger.warning("failed to load group system requests", exc_info=True)
            requests = []
        for item in requests:
            group_id = str(item.get("group_id") or "").strip()
            applicant_qq = str(
                item.get("requester_uin")
                or item.get("user_id")
                or item.get("invitor_uin")
                or ""
            ).strip()
            flag = str(item.get("request_id") or "").strip()
            group_config = find_group_policy(self.config, group_id)
            if not group_id or not applicant_qq or not flag or group_config is None:
                continue
            if not _tracks_requests(group_config):
                continue
            raw_comment = str(item.get("message") or item.get("comment") or "").strip()
            request = JoinRequest(
                group_id=group_id,
                applicant_qq=applicant_qq,
                answer=extract_application_answer(raw_comment),
                flag=flag,
                sub_type="add",
                requested_at=_timestamp(
                    item.get("request_time") or item.get("time"),
                    now,
                ),
                nickname=str(item.get("requester_nick") or "").strip(),
                raw_comment=raw_comment,
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
            if item.get("checked"):
                self.audit_store.mark_external_checked(
                    application_id=application_id,
                    actor_qq=str(item.get("actor") or "").strip(),
                    observed_at=now,
                )
        await self._reconcile_missing_joins(platform_id, now)
        self.audit_store.infer_external_rejections(
            platform_id=platform_id,
            now=now,
            grace_seconds=_EXTERNAL_REJECTION_GRACE_SECONDS,
        )

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
            f"当前已是目标名片：{counts['already_target']}\n"
            f"此前已经处理：{counts['already_done']}\n"
            f"因模板或权限跳过：{counts['skipped']}\n"
            f"修改失败：{counts['failed']}\n"
            f"当前不在群内：{counts['not_in_group']}\n"
            f"入群时间无法匹配申请：{counts['unmatched']}"
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
                await self._reconcile_platform(platform_id)
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
