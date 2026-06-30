from __future__ import annotations

import json
from typing import Protocol

from .models import ReviewDecision


SYSTEM_PROMPT = (
    "你是QQ群加群申请审核器。你只能返回 JSON，不能返回 Markdown、解释文字或代码块。"
    "JSON 必须包含 approve(boolean) 和 reason(string)。只有申请答案明确符合管理员规则时 approve 才能为 true。"
)


class LLMReviewError(Exception):
    pass


class ReviewLLMClient(Protocol):
    async def generate(self, *, system_prompt: str, prompt: str) -> str:
        ...


def build_review_prompt(
    *,
    group_id: str,
    applicant_qq: str,
    answer: str,
    review_prompt: str,
) -> str:
    return (
        "管理员审核规则：\n"
        f"{review_prompt}\n\n"
        "申请信息：\n"
        f"- QQ群号：{group_id}\n"
        f"- 申请人QQ：{applicant_qq}\n"
        f"- 申请答案：{answer}\n\n"
        "请只返回 JSON，例如：{\"approve\": true, \"reason\": \"符合条件\"}"
    )


def parse_review_response(response_text: str) -> ReviewDecision:
    if not isinstance(response_text, str):
        raise LLMReviewError("invalid json response")

    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise LLMReviewError("invalid json response") from exc

    if not isinstance(payload, dict):
        raise LLMReviewError("malformed review response")

    approve = payload.get("approve")
    reason = payload.get("reason")
    if not isinstance(approve, bool) or not isinstance(reason, str):
        raise LLMReviewError("malformed review response")

    return ReviewDecision(approve=approve, reason=reason.strip())


async def review_answer(
    client: ReviewLLMClient,
    *,
    group_id: str,
    applicant_qq: str,
    answer: str,
    review_prompt: str,
) -> ReviewDecision:
    prompt = build_review_prompt(
        group_id=group_id,
        applicant_qq=applicant_qq,
        answer=answer,
        review_prompt=review_prompt,
    )
    try:
        response_text = await client.generate(system_prompt=SYSTEM_PROMPT, prompt=prompt)
    except Exception as exc:
        raise LLMReviewError(f"provider failed: {exc}") from exc
    return parse_review_response(response_text)
