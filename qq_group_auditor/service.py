from __future__ import annotations

from typing import Any, Protocol

from .models import ActionResult, JoinRequest, ReviewDecision
from .reviewer import LLMReviewError


class ReviewerPort(Protocol):
    async def review(self, *, group_config: dict[str, Any], request: JoinRequest) -> ReviewDecision:
        ...


class PlatformPort(Protocol):
    async def set_group_request(self, request: JoinRequest, *, approve: bool, reason: str) -> None:
        ...


class NotifierPort(Protocol):
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
        ...


class AuditService:
    def __init__(
        self,
        reviewer: ReviewerPort,
        platform: PlatformPort,
        notifier: NotifierPort,
    ) -> None:
        self.reviewer = reviewer
        self.platform = platform
        self.notifier = notifier

    async def handle_request(
        self,
        group_config: dict[str, Any],
        request: JoinRequest,
    ) -> ActionResult:
        try:
            decision = await self._decision_for_request(group_config, request)
        except LLMReviewError as exc:
            await self.notifier.notify(
                group_config=group_config,
                request=request,
                title="LLM审核异常",
                action="error",
                error=str(exc),
            )
            return ActionResult(action="error", reason=str(exc))

        if decision.approve:
            try:
                await self.platform.set_group_request(request, approve=True, reason="")
            except Exception as exc:
                await self._notify_platform_error(group_config, request, "approve", exc)
                return ActionResult(action="error", reason=str(exc))
            if group_config.get("notify_on_approve", False):
                await self.notifier.notify(
                    group_config=group_config,
                    request=request,
                    title="加群审核通过",
                    action="approve",
                    reason=decision.reason,
                )
            return ActionResult(action="approve", reason=decision.reason)

        if group_config.get("failure_action") == "reject":
            reject_reason = str(group_config.get("reject_reason") or "")
            try:
                await self.platform.set_group_request(
                    request,
                    approve=False,
                    reason=reject_reason,
                )
            except Exception as exc:
                await self._notify_platform_error(group_config, request, "reject", exc)
                return ActionResult(action="error", reason=str(exc))
            if group_config.get("notify_on_reject", False):
                await self.notifier.notify(
                    group_config=group_config,
                    request=request,
                    title="加群审核拒绝",
                    action="reject",
                    reason=decision.reason,
                )
            return ActionResult(action="reject", reason=decision.reason)

        if group_config.get("notify_on_ignore", False):
            await self.notifier.notify(
                group_config=group_config,
                request=request,
                title="加群审核忽略",
                action="ignore",
                reason=decision.reason,
            )
        return ActionResult(action="ignore", reason=decision.reason)

    async def _decision_for_request(
        self,
        group_config: dict[str, Any],
        request: JoinRequest,
    ) -> ReviewDecision:
        if not request.answer.strip():
            return ReviewDecision(False, "申请答案为空")
        return await self.reviewer.review(group_config=group_config, request=request)

    async def _notify_platform_error(
        self,
        group_config: dict[str, Any],
        request: JoinRequest,
        action: str,
        exc: Exception,
    ) -> None:
        await self.notifier.notify(
            group_config=group_config,
            request=request,
            title="平台审核接口异常",
            action=action,
            error=str(exc),
        )
