import asyncio
from copy import deepcopy

import pytest

from qq_group_auditor.llm_options import (
    REVIEW_RESPONSE_FORMAT, ecnu_request_provider, normalize_llm_review,
)
from test_main import FakeContext, collect, FakeEvent, import_main, plugin_config
from test_llm_stats import response


class SDKClient:
    def __init__(self):
        self.api_key = "original"
        self.pool = object()

    def with_options(self):
        client = SDKClient()
        client.pool = self.pool
        return client


class ECNUProvider:
    def __init__(self, model="ecnu-plus"):
        self.model = model
        self.client = SDKClient()
        self.provider_config = {
            "type": "openai_chat_completion",
            "custom_extra_body": {
                "thinking": {"type": "disabled"}, "reasoning_effort": "xhigh",
                "response_format": {"type": "text"}, "max_tokens": 4000,
            },
        }
        self.calls = []

    def get_model(self):
        return self.model

    async def text_chat(self, **kwargs):
        self.client.api_key = "rotated"
        await asyncio.sleep(0)
        self.calls.append(deepcopy(self.provider_config["custom_extra_body"]))
        return response()


def opts(**kwargs):
    return normalize_llm_review({"ecnu_thinking": "enabled", **kwargs})


@pytest.mark.asyncio
async def test_concurrent_overrides_do_not_leak_to_other_calls_or_sdk():
    provider = ECNUProvider()
    before = deepcopy(provider.provider_config)
    enabled = ecnu_request_provider(provider, opts())
    disabled = ecnu_request_provider(provider, opts(ecnu_thinking="disabled", ecnu_structured_output=False))
    await asyncio.gather(enabled.text_chat(), disabled.text_chat())
    assert provider.provider_config == before
    assert provider.client.api_key == "original"
    assert enabled.client.pool is provider.client.pool
    assert enabled.client is not disabled.client
    assert enabled.provider_config["custom_extra_body"] == {
        "thinking": {"type": "enabled"}, "reasoning_effort": "low",
        "response_format": REVIEW_RESPONSE_FORMAT, "max_tokens": 4000,
    }
    assert disabled.provider_config["custom_extra_body"] == {
        "thinking": {"type": "disabled"}, "max_tokens": 4000,
    }
    await provider.text_chat()
    assert provider.calls[-1] == before["custom_extra_body"]


def test_inherit_and_other_models():
    provider = ECNUProvider()
    local = ecnu_request_provider(provider, opts(ecnu_thinking="inherit"))
    assert local.provider_config["custom_extra_body"]["thinking"] == {"type": "disabled"}
    assert local.provider_config["custom_extra_body"]["reasoning_effort"] == "xhigh"
    assert ecnu_request_provider(ECNUProvider("other"), opts()) is None
    assert ecnu_request_provider(None, opts()) is None


@pytest.mark.parametrize("model,effort,valid", [
    ("ecnu-plus", "low", True), ("ecnu-plus", "medium", True),
    ("ecnu-plus", "xhigh", True), ("ecnu-plus", "high", False),
    ("ecnu-max", "low", True), ("ecnu-max", "high", True),
    ("ecnu-max", "max", True), ("ecnu-max", "medium", False),
])
def test_model_specific_efforts(model, effort, valid):
    provider = ECNUProvider(model)
    if valid:
        ecnu_request_provider(provider, opts(ecnu_reasoning_effort=effort))
    else:
        with pytest.raises(ValueError, match="不支持思考程度"):
            ecnu_request_provider(provider, opts(ecnu_reasoning_effort=effort))


def test_unsupported_adapter_fails_without_mutating_provider():
    provider = ECNUProvider()
    provider.provider_config["type"] = "unknown"
    before = deepcopy(provider.provider_config)
    with pytest.raises(RuntimeError, match="适配器"):
        ecnu_request_provider(provider, opts())
    assert provider.provider_config == before


def test_normalize_bad_values():
    assert normalize_llm_review(None) == normalize_llm_review([])
    got = normalize_llm_review({"provider_id": " ecnu/ecnu-plus ", "ecnu_thinking": "bad",
                                "ecnu_reasoning_effort": "bad", "ecnu_structured_output": "false"})
    assert got == {"provider_id": "ecnu/ecnu-plus", "ecnu_thinking": "inherit",
                   "ecnu_reasoning_effort": "low", "ecnu_structured_output": False}


@pytest.mark.asyncio
async def test_plugin_settings_reach_test_entry_and_keep_usage(monkeypatch):
    module, _ = import_main(monkeypatch)
    config = plugin_config()
    config["llm_review"] = opts(provider_id="ecnu/ecnu-plus")
    context = FakeContext()
    provider = ECNUProvider()
    def get_provider(provider_id):
        assert provider_id == "ecnu/ecnu-plus"
        return provider
    context.get_provider_by_id = get_provider
    plugin = module.QQGroupAuditorPlugin(context, config)
    result = await collect(plugin.qgaudit_test(FakeEvent(message="/qgaudit test 123 2028")))
    assert "approve=True" in result[0]
    assert context.llm_calls == []
    assert provider.calls[0]["response_format"] == REVIEW_RESPONSE_FORMAT
    assert provider.calls[0]["thinking"] == {"type": "enabled"}
    rows = plugin.usage_store.query(group_ids=["123"])
    assert len(rows) == 1 and rows[0]["status"] == "success"
    assert rows[0]["cached_tokens"] == 80
    assert rows[0]["provider_id"] == "ecnu/ecnu-plus"
    assert provider.client.api_key == "original"
    await plugin.terminate()


@pytest.mark.asyncio
async def test_limiter_cancellation_and_failure_release_slots(monkeypatch):
    module, _ = import_main(monkeypatch)
    context = FakeContext()
    limiter = asyncio.Semaphore(3)
    tasks = set()
    active = peak = 0
    gate = asyncio.Event()
    started = asyncio.Event()
    async def llm(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 3:
            started.set()
        try:
            await gate.wait()
            raise TimeoutError("failed")
        finally:
            active -= 1
    context.llm_generate = llm
    clients = [module.AstrBotLLMClient(context, limiter=limiter, tasks=tasks) for _ in range(5)]
    jobs = [asyncio.create_task(c.generate(system_prompt="json", prompt="x")) for c in clients]
    await asyncio.wait_for(started.wait(), 2)
    assert peak == 3 and len(tasks) == 5
    jobs[-1].cancel()
    await asyncio.gather(jobs[-1], return_exceptions=True)
    gate.set()
    await asyncio.gather(*jobs, return_exceptions=True)
    assert not tasks and limiter._value == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,refusal", [("length", None), ("content_filter", None),
                                         ("tool_calls", None), ("stop", "refused")])
async def test_truncated_or_refused_valid_json_does_not_approve(monkeypatch, reason, refusal):
    module, _ = import_main(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(FakeContext(), plugin_config())
    async def incomplete(**kwargs):
        out = response()
        out.raw_completion["choices"][0].update(finish_reason=reason, message={"refusal": refusal})
        return out
    plugin.context.llm_generate = incomplete
    result = await collect(plugin.qgaudit_test(FakeEvent(message="/qgaudit test 123 2028")))
    assert "LLM审核异常" in result[0]
    rows = plugin.usage_store.query(group_ids=["123"], content=True)
    assert len(rows) == 2 and all(r["status"] == "invalid_json" for r in rows)
    assert all(r["cached_tokens"] == 80 and r["finish_reason"] == reason for r in rows)
    await plugin.terminate()
