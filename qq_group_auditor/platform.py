from __future__ import annotations

from typing import Any

from .models import JoinRequest


class PlatformActionError(Exception):
    pass


def _raw_get(raw: Any, key: str, default: Any = None) -> Any:
    if isinstance(raw, dict):
        return raw.get(key, default)
    return getattr(raw, key, default)


def extract_join_request(event: Any) -> JoinRequest | None:
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if raw is None:
        return None

    if _raw_get(raw, "post_type") != "request" or _raw_get(raw, "request_type") != "group":
        return None

    group_id = str(_raw_get(raw, "group_id") or "").strip()
    user_id = str(_raw_get(raw, "user_id") or "").strip()
    flag = str(_raw_get(raw, "flag") or "").strip()
    sub_type = str(_raw_get(raw, "sub_type") or "add").strip() or "add"
    answer = str(_raw_get(raw, "comment") or "").strip()

    if not group_id or not user_id or not flag:
        raise ValueError("missing required group request fields")

    return JoinRequest(
        group_id=group_id,
        applicant_qq=user_id,
        answer=answer,
        flag=flag,
        sub_type=sub_type,
    )


def _iter_platforms(context: Any):
    manager = getattr(context, "platform_manager", None)
    if manager is None:
        return []
    if hasattr(manager, "get_insts"):
        return manager.get_insts()
    return getattr(manager, "platform_insts", []) or []


def find_onebot_bot(context: Any) -> Any:
    for platform in _iter_platforms(context):
        bot = getattr(platform, "bot", None)
        if bot is not None and hasattr(bot, "call_action"):
            return bot
    raise PlatformActionError("onebot bot api not found")


async def set_group_request(
    context: Any,
    *,
    flag: str,
    sub_type: str,
    approve: bool,
    reason: str,
) -> None:
    bot = find_onebot_bot(context)
    try:
        await bot.call_action(
            action="set_group_add_request",
            flag=flag,
            sub_type=sub_type,
            approve=approve,
            reason=reason,
        )
    except Exception as exc:
        raise PlatformActionError(f"set_group_add_request failed: {exc}") from exc
