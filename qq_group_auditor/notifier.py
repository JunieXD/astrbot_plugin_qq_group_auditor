from __future__ import annotations

from typing import Any

try:
    from astrbot.api.event import MessageChain
except ImportError:  # pragma: no cover - only used in local tests without AstrBot.

    class MessageChain:
        def __init__(self, chain: list[Any] | None = None) -> None:
            self.chain = list(chain or [])

        def message(self, text: str):
            self.chain.append(text)
            return self

        def get_plain_text(self) -> str:
            return "".join(str(item) for item in self.chain)


from .models import JoinRequest
from .text import summarize_text


def private_umo(platform_name: str, qq_id: str) -> str:
    return f"{platform_name}:FriendMessage:{qq_id}"


def format_notice(
    title: str,
    request: JoinRequest,
    action: str,
    reason: str = "",
    error: str = "",
) -> str:
    lines = [
        title,
        f"群号：{request.group_id}",
        f"申请人：{request.applicant_qq}",
        f"类型：{'受邀入群' if request.request_kind == 'invite' else '主动申请'}",
        f"动作：{action}",
        f"答案：{summarize_text(request.answer, 160) or '(空)'}",
    ]
    if reason:
        lines.append(f"处理理由：{reason}")
    if error:
        lines.append(f"错误：{error}")
    return "\n".join(lines)


async def send_admin_notice(
    context: Any,
    admin_qq_ids: list[str],
    text: str,
    platform_name: str = "aiocqhttp",
) -> None:
    for qq_id in admin_qq_ids:
        target = private_umo(platform_name, str(qq_id))
        await context.send_message(target, MessageChain().message(text))
