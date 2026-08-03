from __future__ import annotations

import pytest

from qq_group_auditor.reviewer import LLMReviewError, ReviewLLMClient, review_answer


class FakeLLMClient(ReviewLLMClient):
    def __init__(self, response_text: object | Exception) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, str]] = []

    async def generate(self, *, system_prompt: str, prompt: str) -> str:
        self.calls.append({"system_prompt": system_prompt, "prompt": prompt})
        if isinstance(self.response_text, Exception):
            raise self.response_text
        return self.response_text


class SequenceLLMClient(ReviewLLMClient):
    def __init__(self, responses: list[object | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, str]] = []

    async def generate(self, *, system_prompt: str, prompt: str) -> str:
        response = self.responses[len(self.calls)]
        self.calls.append({"system_prompt": system_prompt, "prompt": prompt})
        if isinstance(response, Exception):
            raise response
        return response


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
    assert "json" in client.calls[0]["system_prompt"].lower()
    assert '"approve": true' in client.calls[0]["prompt"]


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
async def test_review_answer_accepts_complete_json_markdown_code_fence():
    client = FakeLLMClient(
        '```json\n{"approve": true, "reason": "代码块内是有效 JSON"}\n```'
    )

    decision = await review_answer(
        client,
        group_id="123",
        applicant_qq="10001",
        answer="github",
        review_prompt="合理来源即可",
    )

    assert decision.approve is True
    assert decision.reason == "代码块内是有效 JSON"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_review_answer_retries_invalid_response_once():
    client = SequenceLLMClient(
        [
            "",
            '{"approve": true, "reason": "重试成功"}',
        ]
    )

    decision = await review_answer(
        client,
        group_id="123",
        applicant_qq="10001",
        answer="github",
        review_prompt="合理来源即可",
    )

    assert decision.approve is True
    assert decision.reason == "重试成功"
    assert len(client.calls) == 2
    assert "上一次输出无法解析" not in client.calls[0]["prompt"]
    assert "上一次输出无法解析" in client.calls[1]["prompt"]


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

    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_review_answer_rejects_json_mixed_with_explanation_text():
    client = FakeLLMClient(
        '审核结果如下：\n{"approve": true, "reason": "不应从说明文字中提取"}'
    )

    with pytest.raises(LLMReviewError, match="invalid json"):
        await review_answer(
            client,
            group_id="123",
            applicant_qq="10001",
            answer="abc",
            review_prompt="规则",
        )

    assert len(client.calls) == 2


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

    assert len(client.calls) == 2


@pytest.mark.parametrize("response_text", ["[]", '"approved"'])
@pytest.mark.asyncio
async def test_review_answer_rejects_non_object_json(response_text: str):
    client = FakeLLMClient(response_text)

    with pytest.raises(LLMReviewError, match="malformed"):
        await review_answer(
            client,
            group_id="123",
            applicant_qq="10001",
            answer="abc",
            review_prompt="规则",
        )


@pytest.mark.asyncio
async def test_review_answer_rejects_non_string_response():
    client = FakeLLMClient(None)

    with pytest.raises(LLMReviewError, match="invalid json"):
        await review_answer(
            client,
            group_id="123",
            applicant_qq="10001",
            answer="abc",
            review_prompt="规则",
        )

    assert len(client.calls) == 2


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

    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_review_answer_logs_only_truncated_invalid_response(caplog):
    response_text = "x" * 700
    client = FakeLLMClient(response_text)

    with caplog.at_level("WARNING", logger="qq_group_auditor.reviewer"):
        with pytest.raises(LLMReviewError, match="invalid json"):
            await review_answer(
                client,
                group_id="123",
                applicant_qq="10001",
                answer="abc",
                review_prompt="规则",
            )

    message = caplog.records[0].getMessage()
    assert "group_id=123" in message
    assert "applicant_qq=10001" in message
    assert "x" * 500 in message
    assert "x" * 501 not in message
    assert "<truncated 200 chars>" in message
