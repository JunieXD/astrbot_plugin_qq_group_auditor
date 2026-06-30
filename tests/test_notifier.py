from __future__ import annotations

import pytest

from qq_group_auditor.models import JoinRequest
from qq_group_auditor.notifier import format_notice, send_admin_notice


class FakeContext:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    async def send_message(self, session: str, message_chain: object) -> bool:
        self.sent.append((session, message_chain))
        return True


def chain_text(chain: object) -> str:
    return chain.get_plain_text() if hasattr(chain, "get_plain_text") else str(chain.chain[0])


def test_format_notice_contains_group_applicant_action_and_reason():
    request = JoinRequest(
        group_id="123456",
        applicant_qq="10001",
        answer="AutoEmailSender 是邮件工具",
        flag="flag",
        sub_type="add",
    )

    text = format_notice(
        title="加群审核通过",
        request=request,
        action="approve",
        reason="符合条件",
    )

    assert "123456" in text
    assert "10001" in text
    assert "approve" in text
    assert "符合条件" in text
    assert "AutoEmailSender" in text


@pytest.mark.asyncio
async def test_send_admin_notice_sends_private_messages_to_all_admins():
    context = FakeContext()

    await send_admin_notice(
        context,
        admin_qq_ids=["10001", "10002"],
        text="通知内容",
        platform_name="aiocqhttp",
    )

    assert [target for target, _ in context.sent] == [
        "aiocqhttp:FriendMessage:10001",
        "aiocqhttp:FriendMessage:10002",
    ]
