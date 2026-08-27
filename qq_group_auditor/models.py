from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FailureAction = Literal["ignore", "reject"]
InviteAction = Literal["approve", "ignore", "reject"]
RequestKind = Literal["application", "invite"]


@dataclass(frozen=True)
class GroupAuditConfig:
    group_id: str
    enabled: bool
    review_prompt: str
    failure_action: FailureAction
    invite_action: InviteAction
    reject_reason: str
    admin_qq_ids: tuple[str, ...]
    notify_on_approve: bool
    notify_on_reject: bool
    notify_on_ignore: bool
    audit_log_enabled: bool = True
    auto_set_card: bool = False
    card_template: str = "{nickname}"
    application_question: str = ""


@dataclass(frozen=True)
class ReviewDecision:
    approve: bool
    reason: str


@dataclass(frozen=True)
class JoinRequest:
    group_id: str
    applicant_qq: str
    answer: str
    flag: str
    sub_type: str
    requested_at: int = 0
    self_id: str = ""
    question: str = ""
    nickname: str = ""
    raw_comment: str = ""
    request_kind: RequestKind = "application"


@dataclass(frozen=True)
class GroupMemberIncrease:
    group_id: str
    user_id: str
    operator_id: str
    sub_type: str
    occurred_at: int
    self_id: str = ""


@dataclass(frozen=True)
class GroupMemberDecrease:
    group_id: str
    user_id: str
    operator_id: str
    sub_type: str
    occurred_at: int
    self_id: str = ""


@dataclass(frozen=True)
class GroupMemberInfo:
    nickname: str
    card: str
    join_time: int = 0
    card_changeable: bool | None = None


@dataclass(frozen=True)
class GroupMemberSnapshot:
    user_id: str
    nickname: str
    card: str
    join_time: int = 0
    card_changeable: bool | None = None

    def info(self) -> GroupMemberInfo:
        return GroupMemberInfo(
            nickname=self.nickname,
            card=self.card,
            join_time=self.join_time,
            card_changeable=self.card_changeable,
        )


@dataclass(frozen=True)
class ActionResult:
    action: str
    reason: str = ""
    review_action: str = ""
    platform_action: str = ""
    platform_status: str = "none"
