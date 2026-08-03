from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from .models import ReviewDecision


logger = logging.getLogger(__name__)

MAX_REVIEW_ATTEMPTS = 2
RESPONSE_LOG_LIMIT = 500
_JSON_CODE_FENCE = re.compile(
    r"\A\s*```(?:json)?[ \t]*(?:\r?\n)?(?P<payload>.*?)(?:\r?\n)?```\s*\Z",
    re.IGNORECASE | re.DOTALL,
)
_RETRY_INSTRUCTION = (
    "\n\n上一次输出无法解析。请重新审核，并且只返回一个完整的 json 对象："
    '{"approve": true, "reason": "简短理由"}。'
    "approve 必须是 boolean，reason 必须是 string；不要返回空内容、Markdown 或解释文字。"
)

SYSTEM_PROMPT = (
    "你是QQ群加群申请审核器。你只能返回一个 JSON（json）对象，不能返回 Markdown、解释文字或代码块。"
    "JSON 必须包含 approve(boolean) 和 reason(string)。只有申请答案明确符合管理员规则时 approve 才能为 true。"
)


class LLMReviewError(Exception):
    pass


class ReviewLLMClient(Protocol):
    async def generate(self, *, system_prompt: str, prompt: str) -> str:
        ...


def _unwrap_json_code_fence(response_text: str) -> str:
    match = _JSON_CODE_FENCE.fullmatch(response_text)
    if match is None:
        return response_text
    return match.group("payload").strip()


def _response_log_excerpt(response_text: object) -> str:
    text = response_text if isinstance(response_text, str) else repr(response_text)
    if len(text) <= RESPONSE_LOG_LIMIT:
        return text
    omitted = len(text) - RESPONSE_LOG_LIMIT
    return f"{text[:RESPONSE_LOG_LIMIT]}... <truncated {omitted} chars>"


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
        payload = json.loads(_unwrap_json_code_fence(response_text))
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
    for attempt in range(1, MAX_REVIEW_ATTEMPTS + 1):
        request_prompt = prompt if attempt == 1 else f"{prompt}{_RETRY_INSTRUCTION}"
        try:
            response_text = await client.generate(
                system_prompt=SYSTEM_PROMPT,
                prompt=request_prompt,
            )
        except Exception as exc:
            raise LLMReviewError(f"provider failed: {exc}") from exc

        try:
            return parse_review_response(response_text)
        except LLMReviewError as exc:
            logger.warning(
                "invalid LLM review response: group_id=%s applicant_qq=%s "
                "attempt=%d/%d error=%s response=%r",
                group_id,
                applicant_qq,
                attempt,
                MAX_REVIEW_ATTEMPTS,
                exc,
                _response_log_excerpt(response_text),
            )
            if attempt == MAX_REVIEW_ATTEMPTS:
                raise

    raise AssertionError("review attempt loop exited unexpectedly")
