from __future__ import annotations

import json

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .text import summarize_text


def format_time(value: Any) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return "未知"
    if timestamp <= 0:
        return "未知"
    return datetime.fromtimestamp(timestamp, tz=ZoneInfo("Asia/Shanghai")).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _action_summary(actions: list[dict[str, Any]]) -> str:
    if not actions:
        return "尚无处理记录"
    parts = []
    for action in actions:
        actor = str(action.get("actor_qq") or "未知")
        source = str(action.get("source") or "unknown")
        status = str(action.get("status") or "")
        parts.append(f"{action.get('action')}({actor}, {source}, {status})")
    return "; ".join(parts)


def _request_type(record: dict[str, Any]) -> str:
    memberships = record.get("memberships") or []
    if record.get("request_kind") == "invite" or any(
        membership.get("join_sub_type") == "invite" for membership in memberships
    ):
        return "受邀入群"
    if record.get("record_type") == "join_only":
        return "仅观察到入群"
    return "主动申请"


def format_history(records: list[dict[str, Any]]) -> str:
    if not records:
        return "没有找到申请记录"
    blocks = []
    for record in records:
        memberships = record.get("memberships") or []
        latest_membership = memberships[-1] if memberships else None
        is_join_only = record.get("record_type") == "join_only"
        lines = [
            f"{'入群' if is_join_only else '申请'}记录 #{record['id']}  "
            f"{'入群' if is_join_only else '申请'}时间：{format_time(record.get('requested_at'))}",
            f"QQ：{record.get('applicant_qq')}",
            f"类型：{_request_type(record)}",
            f"问题：{summarize_text(record.get('question') or '', 120) or '(未知)'}",
            f"答案：{summarize_text(record.get('answer') or '', 240) or '(空)'}",
            f"处理：{_action_summary(record.get('actions') or [])}",
        ]
        if latest_membership:
            lines.append(f"入群：{format_time(latest_membership.get('joined_at'))}")
            if latest_membership.get("left_at"):
                lines.append(
                    f"离群：{format_time(latest_membership.get('left_at'))} "
                    f"({latest_membership.get('leave_sub_type')}, "
                    f"操作者 {latest_membership.get('leave_operator_qq') or '未知'})"
                )
        else:
            lines.append("入群：未观察到")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def format_detail(record: dict[str, Any] | None) -> str:
    if record is None:
        return "没有找到申请记录"
    is_join_only = record.get("record_type") == "join_only"
    lines = [
        f"{'入群' if is_join_only else '申请'}记录 #{record['id']}",
        f"群号：{record.get('group_id')}",
        f"QQ：{record.get('applicant_qq')}",
        f"类型：{_request_type(record)}",
        f"申请昵称：{record.get('nickname') or '(未知)'}",
        f"申请时间：{format_time(record.get('requested_at'))}",
        f"问题：{record.get('question') or '(未知)'}",
        f"答案：{record.get('answer') or '(空)'}",
        "处理流水：",
    ]
    actions = record.get("actions") or []
    if not actions:
        lines.append("- 尚无处理记录")
    for action in actions:
        lines.append(
            f"- {format_time(action.get('occurred_at'))} {action.get('action')} "
            f"操作者={action.get('actor_qq') or '未知'} "
            f"来源={action.get('source')} 状态={action.get('status')}"
        )
        if action.get("reason"):
            lines.append(f"  原因：{action.get('reason')}")
    phases = record.get("phases") or []
    labels = {
        "review_start": "开始审核", "llm_queued": "等待模型并发槽", "llm_start": "模型开始",
        "llm_end": "模型返回/结束", "llm_parsed": "输出校验", "action_queued": "审批入队",
        "action_wait": "审批等待", "online_check_start": "在线检查开始", "online_check_end": "在线检查结束",
        "platform_start": "平台操作开始", "platform_end": "平台操作结束", "platform_skipped": "平台操作跳过",
        "action_deduplicated": "已完成操作去重", "action_stopped": "操作暂缓/中断", "review_end": "本次审核结束",
    }
    if phases:
        lines.append("阶段记录（最近 100 条，单次耗时不可重复相加）：")
    for phase in phases:
        details = json.loads(phase["details"])
        parts = [str(details[key]) for key in ("reason", "status", "error_type") if details.get(key)]
        for key, label in (("duration_ms", "耗时"), ("queue_ms", "排队")):
            if isinstance(details.get(key), (int, float)):
                parts.append(f"{label}={details[key]/1000:.2f}s")
        if "delay_seconds" in details:
            parts.append(f"随机等待={details['delay_seconds']:.2f}s")
        if "connected" in details:
            parts.append(f"在线={details['connected']}")
        lines.append(f"- {format_time(phase['occurred_at'])} {labels.get(phase['phase'], phase['phase'])} " + "；".join(parts))
    card_attempts = record.get("card_attempts") or []
    if card_attempts:
        lines.append("群名片尝试：")
    for attempt in card_attempts:
        lines.append(
            f"- {format_time(attempt.get('attempted_at'))} "
            f"来源={attempt.get('source')} 状态={attempt.get('status')}"
        )
        if attempt.get("error"):
            lines.append(f"  错误：{attempt.get('error')}")
    memberships = record.get("memberships") or []
    for index, membership in enumerate(memberships, start=1):
        lines.append(f"成员会话 {index}：")
        lines.append(
            f"- 入群：{format_time(membership.get('joined_at'))} "
            f"方式={membership.get('join_sub_type') or '未知'} "
            f"操作者={membership.get('join_operator_qq') or '未知'}"
        )
        if membership.get("left_at"):
            lines.append(
                f"- 离群：{format_time(membership.get('left_at'))} "
                f"方式={membership.get('leave_sub_type')} "
                f"操作者={membership.get('leave_operator_qq') or '未知'}"
            )
        for card in membership.get("card_operations") or []:
            lines.append(
                f"- 名片：{card.get('status')} "
                f"{card.get('old_card') or '(空)'} -> {card.get('target_card') or '(未生成)'}"
            )
            if card.get("error"):
                lines.append(f"  错误：{card.get('error')}")
    return "\n".join(lines)
