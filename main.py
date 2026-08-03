from __future__ import annotations

import logging
import re
from typing import Any

from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register

try:
    from .qq_group_auditor.config import find_group_config, is_group_admin, normalize_config
    from .qq_group_auditor.models import JoinRequest, ReviewDecision
    from .qq_group_auditor.notifier import format_notice, send_admin_notice
    from .qq_group_auditor.platform import extract_join_request, set_group_request
    from .qq_group_auditor.reviewer import LLMReviewError, review_answer
    from .qq_group_auditor.service import AuditService
except ImportError:  # pragma: no cover - supports direct local imports in tests/dev.
    from qq_group_auditor.config import find_group_config, is_group_admin, normalize_config
    from qq_group_auditor.models import JoinRequest, ReviewDecision
    from qq_group_auditor.notifier import format_notice, send_admin_notice
    from qq_group_auditor.platform import extract_join_request, set_group_request
    from qq_group_auditor.reviewer import LLMReviewError, review_answer
    from qq_group_auditor.service import AuditService


logger = logging.getLogger(__name__)

_DEEPSEEK_JSON_MAX_TOKENS = 512


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


@register("qq_group_auditor", "Junie", "QQ group join request auditor", "0.1.2")
class QQGroupAuditorPlugin(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context=context, config=config)
        self.context = context
        self.config = normalize_config(dict(config or {}))

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

        group_config = find_group_config(self.config, request.group_id)
        if group_config is None:
            return

        platform_id = _platform_id(event)
        service = AuditService(
            RuntimeReviewer(self.context, getattr(event, "unified_msg_origin", None)),
            RuntimePlatform(self.context, platform_id=platform_id),
            RuntimeNotifier(self.context, platform_id=platform_id),
            logger=logger,
        )
        await service.handle_request(group_config, request)

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
