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

    sub_type = str(_raw_get(raw, "sub_type") or "add").strip() or "add"
    if sub_type != "add":
        return None

    group_id = str(_raw_get(raw, "group_id") or "").strip()
    user_id = str(_raw_get(raw, "user_id") or "").strip()
    flag = str(_raw_get(raw, "flag") or "").strip()
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
