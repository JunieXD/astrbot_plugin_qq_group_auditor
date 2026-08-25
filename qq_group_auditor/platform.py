from __future__ import annotations

from typing import Any

import time

from .models import (
    GroupMemberDecrease,
    GroupMemberIncrease,
    GroupMemberInfo,
    JoinRequest,
)


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
    sub_type = str(_raw_get(raw, "sub_type") or "").strip()
    answer = str(_raw_get(raw, "comment") or "").strip()
    requested_at = int(_raw_get(raw, "time") or time.time())
    self_id = str(_raw_get(raw, "self_id") or "").strip()

    if not group_id or not user_id or not flag or not sub_type:
        raise ValueError("missing required group request fields")

    if sub_type != "add":
        return None

    return JoinRequest(
        group_id=group_id,
        applicant_qq=user_id,
        answer=answer,
        flag=flag,
        sub_type=sub_type,
        requested_at=requested_at,
        self_id=self_id,
        raw_comment=answer,
    )


def extract_group_member_increase(event: Any) -> GroupMemberIncrease | None:
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if raw is None:
        return None
    if _raw_get(raw, "post_type") != "notice" or _raw_get(raw, "notice_type") != "group_increase":
        return None

    group_id = str(_raw_get(raw, "group_id") or "").strip()
    user_id = str(_raw_get(raw, "user_id") or "").strip()
    operator_id = str(_raw_get(raw, "operator_id") or "").strip()
    sub_type = str(_raw_get(raw, "sub_type") or "").strip()
    if not group_id or not user_id or not sub_type:
        raise ValueError("missing required group increase fields")
    return GroupMemberIncrease(
        group_id=group_id,
        user_id=user_id,
        operator_id=operator_id,
        sub_type=sub_type,
        occurred_at=int(_raw_get(raw, "time") or time.time()),
        self_id=str(_raw_get(raw, "self_id") or "").strip(),
    )


def extract_group_member_decrease(event: Any) -> GroupMemberDecrease | None:
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if raw is None:
        return None
    if _raw_get(raw, "post_type") != "notice" or _raw_get(raw, "notice_type") != "group_decrease":
        return None

    group_id = str(_raw_get(raw, "group_id") or "").strip()
    user_id = str(_raw_get(raw, "user_id") or "").strip()
    operator_id = str(_raw_get(raw, "operator_id") or "").strip()
    sub_type = str(_raw_get(raw, "sub_type") or "").strip()
    if not group_id or not user_id or not sub_type:
        raise ValueError("missing required group decrease fields")
    return GroupMemberDecrease(
        group_id=group_id,
        user_id=user_id,
        operator_id=operator_id,
        sub_type=sub_type,
        occurred_at=int(_raw_get(raw, "time") or time.time()),
        self_id=str(_raw_get(raw, "self_id") or "").strip(),
    )


def _iter_platforms(context: Any):
    manager = getattr(context, "platform_manager", None)
    if manager is None:
        return []
    if hasattr(manager, "get_insts"):
        return manager.get_insts()
    return getattr(manager, "platform_insts", []) or []


def _platform_meta_id(platform: Any) -> str | None:
    meta = getattr(platform, "meta", None)
    if not callable(meta):
        return None
    try:
        metadata = meta()
    except Exception:
        return None
    value = getattr(metadata, "id", None)
    return str(value) if value is not None else None


def _platform_metadata_id(platform: Any) -> str | None:
    metadata = getattr(platform, "metadata", None)
    value = getattr(metadata, "id", None)
    return str(value) if value is not None else None


def _platform_id(platform: Any) -> str | None:
    value = getattr(platform, "id", None)
    return str(value) if value is not None else None


def _platform_config_id(platform: Any) -> str | None:
    config = getattr(platform, "config", None)
    if not isinstance(config, dict):
        return None
    value = config.get("id")
    return str(value) if value is not None else None


def _platform_matches_id(platform: Any, platform_id: str) -> bool:
    return platform_id in {
        value
        for value in (
            _platform_meta_id(platform),
            _platform_metadata_id(platform),
            _platform_id(platform),
            _platform_config_id(platform),
        )
        if value is not None
    }


def find_onebot_bot(context: Any, platform_id: str | None = None) -> Any:
    for platform in _iter_platforms(context):
        if platform_id is not None and not _platform_matches_id(platform, platform_id):
            continue
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
    platform_id: str | None = None,
) -> None:
    bot = find_onebot_bot(context, platform_id=platform_id)
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


async def get_group_member_info(
    context: Any,
    *,
    group_id: str,
    user_id: str,
    platform_id: str | None = None,
) -> GroupMemberInfo:
    bot = find_onebot_bot(context, platform_id=platform_id)
    try:
        data = await bot.call_action(
            action="get_group_member_info",
            group_id=group_id,
            user_id=user_id,
            no_cache=True,
        )
    except Exception as exc:
        raise PlatformActionError(f"get_group_member_info failed: {exc}") from exc
    if not isinstance(data, dict):
        raise PlatformActionError("get_group_member_info returned invalid data")
    card_changeable = data.get("card_changeable")
    return GroupMemberInfo(
        nickname=str(data.get("nickname") or "").strip(),
        card=str(data.get("card") or "").strip(),
        join_time=int(data.get("join_time") or 0),
        card_changeable=card_changeable if isinstance(card_changeable, bool) else None,
    )


async def set_group_card(
    context: Any,
    *,
    group_id: str,
    user_id: str,
    card: str,
    platform_id: str | None = None,
) -> None:
    bot = find_onebot_bot(context, platform_id=platform_id)
    try:
        await bot.call_action(
            action="set_group_card",
            group_id=group_id,
            user_id=user_id,
            card=card,
        )
    except Exception as exc:
        raise PlatformActionError(f"set_group_card failed: {exc}") from exc


async def get_group_question(
    context: Any,
    *,
    group_id: str,
    platform_id: str | None = None,
) -> str:
    bot = find_onebot_bot(context, platform_id=platform_id)
    try:
        data = await bot.call_action(action="get_group_detail_info", group_id=group_id)
    except Exception as exc:
        raise PlatformActionError(f"get_group_detail_info failed: {exc}") from exc
    if not isinstance(data, dict):
        return ""
    return str(data.get("groupQuestion") or data.get("group_question") or "").strip()


async def get_group_system_requests(
    context: Any,
    *,
    platform_id: str | None = None,
    count: int = 100,
) -> list[dict[str, Any]]:
    bot = find_onebot_bot(context, platform_id=platform_id)
    try:
        data = await bot.call_action(action="get_group_system_msg", count=count)
    except Exception as exc:
        raise PlatformActionError(f"get_group_system_msg failed: {exc}") from exc
    if not isinstance(data, dict):
        raise PlatformActionError("get_group_system_msg returned invalid data")
    requests = data.get("join_requests") or []
    return [item for item in requests if isinstance(item, dict)]
