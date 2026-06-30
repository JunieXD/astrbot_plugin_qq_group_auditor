from __future__ import annotations

import pytest

from qq_group_auditor.reviewer import LLMReviewError, ReviewLLMClient, review_answer


class FakeLLMClient(ReviewLLMClient):
    def __init__(self, response_text: str | Exception) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, str]] = []

    async def generate(self, *, system_prompt: str, prompt: str) -> str:
        self.calls.append({"system_prompt": system_prompt, "prompt": prompt})
        if isinstance(self.response_text, Exception):
            raise self.response_text
        return self.response_text


@pytest.mark.asyncio
async def test_review_answer_accepts_strict_json_true():
    client = FakeLLMClient('{"approve": true, "reason": "答案正确"}')

    decision = await review_answer(
        client,
        group_id="123",
        applicant_qq="10001",
        answer="AutoEmailSender",
        review_prompt="必须知道项目名",
    )

    assert decision.approve is True
    assert decision.reason == "答案正确"
    assert "必须知道项目名" in client.calls[0]["prompt"]
    assert "AutoEmailSender" in client.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_review_answer_accepts_strict_json_false():
    client = FakeLLMClient('{"approve": false, "reason": "答案不相关"}')

    decision = await review_answer(
        client,
        group_id="123",
        applicant_qq="10001",
        answer="随便",
        review_prompt="必须知道项目名",
    )

    assert decision.approve is False
    assert decision.reason == "答案不相关"


@pytest.mark.asyncio
async def test_review_answer_rejects_invalid_json():
    client = FakeLLMClient("通过")

    with pytest.raises(LLMReviewError, match="invalid json"):
        await review_answer(
            client,
            group_id="123",
            applicant_qq="10001",
            answer="abc",
            review_prompt="规则",
        )


@pytest.mark.asyncio
async def test_review_answer_rejects_malformed_shape():
    client = FakeLLMClient('{"approve": "yes", "reason": 1}')

    with pytest.raises(LLMReviewError, match="malformed"):
        await review_answer(
            client,
            group_id="123",
            applicant_qq="10001",
            answer="abc",
            review_prompt="规则",
        )


@pytest.mark.asyncio
async def test_review_answer_wraps_provider_errors():
    client = FakeLLMClient(RuntimeError("provider down"))

    with pytest.raises(LLMReviewError, match="provider failed"):
        await review_answer(
            client,
            group_id="123",
            applicant_qq="10001",
            answer="abc",
            review_prompt="规则",
        )
