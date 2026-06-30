from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FailureAction = Literal["ignore", "reject"]


@dataclass(frozen=True)
class GroupAuditConfig:
    group_id: str
    enabled: bool
    review_prompt: str
    failure_action: FailureAction
    reject_reason: str
    admin_qq_ids: tuple[str, ...]
    notify_on_approve: bool
    notify_on_reject: bool
    notify_on_ignore: bool
