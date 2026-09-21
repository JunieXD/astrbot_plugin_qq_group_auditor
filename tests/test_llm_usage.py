from decimal import Decimal
from types import SimpleNamespace as Obj

import pytest

from qq_group_auditor.llm_usage import calculate_cost, extract_usage, normalize_statistics


def response(raw_usage, **kwargs):
    return Obj(raw_completion={"choices": [{}], "usage": raw_usage}, **kwargs)


@pytest.mark.parametrize("as_object", [False, True])
def test_deepseek_counters_override_framework_zero_cache(as_object):
    usage = {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 800,
             "prompt_cache_miss_tokens": 200, "completion_tokens": 150,
             "completion_tokens_details": {"reasoning_tokens": 120}}
    if as_object:
        usage = Obj(**usage)
    data = extract_usage(response(usage, usage=Obj(input_other=1000, input_cached=0, output=150)))
    assert [data[k] for k in ("input_tokens", "cached_tokens", "uncached_tokens", "output_tokens", "reasoning_tokens")] == [1000, 800, 200, 150, 120]
    assert data["usage_source"] == "provider"


def test_openai_cache_and_zero_are_known_when_provider_reports_them():
    data = extract_usage(response({"prompt_tokens": 100, "completion_tokens": 0,
                                  "prompt_tokens_details": Obj(cached_tokens=0)}))
    assert data["cached_tokens"] == 0
    assert data["uncached_tokens"] == 100
    assert data["output_tokens"] == 0
    assert data["reasoning_tokens"] is None


def test_absent_cache_remains_unknown_instead_of_framework_default_zero():
    data = extract_usage(response({"prompt_tokens": 100, "completion_tokens": 20},
                                 usage=Obj(input_other=100, input_cached=0, output=20)))
    assert data["input_tokens"] == 100
    assert data["cached_tokens"] is data["uncached_tokens"] is None


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": -1, "completion_tokens": True},
                                  {"prompt_tokens": 2**64, "completion_tokens": float("nan")}])
def test_absent_and_invalid_raw_usage_never_become_free_calls(usage):
    data = extract_usage(response(usage, usage=Obj(input_other=0, input_cached=0, output=0)))
    assert data["input_tokens"] is data["output_tokens"] is None
    assert data["usage_source"] == "unknown"


@pytest.mark.parametrize("counters", [
    {"prompt_cache_hit_tokens": 101},
    {"prompt_cache_miss_tokens": 101},
    {"prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 30},
    {"prompt_cache_hit_tokens": 80, "prompt_tokens_details": {"cached_tokens": 70}},
])
def test_inconsistent_cache_is_not_used_for_cost(counters):
    data = extract_usage(response({"prompt_tokens": 100, "completion_tokens": 20, **counters}))
    assert data["cached_tokens"] is data["uncached_tokens"] is None
    assert data["usage_notes"]


def test_invalid_reasoning_does_not_corrupt_output_total():
    data = extract_usage(response({"completion_tokens": 20, "completion_tokens_details": {"reasoning_tokens": 30}}))
    assert data["output_tokens"] == 20
    assert data["reasoning_tokens"] is None


def test_can_derive_total_and_cache_split_from_provider_counters():
    data = extract_usage(response({"prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20}))
    assert data["input_tokens"] == 100
    data = extract_usage(response({"prompt_tokens": 100, "prompt_cache_miss_tokens": 20}))
    assert data["cached_tokens"] == 80


def test_fallback_for_other_astrbot_adapters():
    data = extract_usage(Obj(usage=Obj(input_other=20, input_cached=80, output=10)))
    assert data["input_tokens"] == 100 and data["usage_source"] == "astrbot"
    assert extract_usage(Obj(usage=Obj(input_other=0, input_cached=0, output=0)))["input_tokens"] is None


def prices(**overrides):
    return normalize_statistics({"prices": [{"model": "test-model", "cached_input_per_million": "1",
                  "uncached_input_per_million": "4", "output_per_million": "8", **overrides}]})["prices"]


def test_cost_counts_reasoning_only_once_and_preserves_price_snapshot():
    data = dict(input_tokens=1000, cached_tokens=800, uncached_tokens=200, output_tokens=150, reasoning_tokens=120)
    cost = calculate_cost(data, prices(), "provider", "test-model")
    assert Decimal(cost["estimated_cost"]) == Decimal("0.0028")
    assert cost["currency"] == "CNY"
    assert cost["price_snapshot"]["output_per_million"] == "8"
    assert calculate_cost(data, prices(), "provider", "other-model")["estimated_cost"] is None


def test_provider_specific_price_precedes_global_price():
    configured = prices(output_per_million="1") + prices(provider_id="special", output_per_million="8")
    data = dict(input_tokens=0, cached_tokens=0, uncached_tokens=0, output_tokens=1000000)
    assert calculate_cost(data, configured, "special", "test-model")["estimated_cost"] == "8"
    assert calculate_cost(data, configured, "other", "test-model")["estimated_cost"] == "1"


def test_missing_split_can_only_be_priced_when_input_rates_match():
    data = dict(input_tokens=100, cached_tokens=None, uncached_tokens=None, output_tokens=20)
    assert calculate_cost(data, prices(), "p", "test-model")["estimated_cost"] is None
    same = prices(cached_input_per_million="4")
    assert Decimal(calculate_cost(data, same, "p", "test-model")["estimated_cost"]) == Decimal("0.00056")
    data["output_tokens"] = None
    assert calculate_cost(data, same, "p", "test-model")["estimated_cost"] is None


def test_free_prices_are_distinct_from_missing_prices():
    data = dict(cached_tokens=80, uncached_tokens=20, output_tokens=10)
    assert calculate_cost(data, prices(cached_input_per_million="", output_per_million=""), "p", "test-model")["estimated_cost"] is None
    free = prices(cached_input_per_million="0", uncached_input_per_million="0", output_per_million="0")
    assert calculate_cost(data, free, "p", "test-model")["estimated_cost"] == "0"


def test_statistics_config_handles_invalid_values_and_does_not_modify_input():
    raw = {"enabled": "false", "store_content": "true", "retention_days": "nan", "content_max_chars": -100,
           "prices": [None, {}, {"model": "m", "currency": "CNY", "cached_input_per_million": "NaN",
                                  "uncached_input_per_million": "-1", "output_per_million": "Infinity"}]}
    config = normalize_statistics(raw)
    assert config["enabled"] is False and config["store_content"] is True
    assert config["retention_days"] == 90 and config["content_max_chars"] == 256
    assert config["prices"][0]["cached_input_per_million"] is None
    assert raw["prices"][2]["output_per_million"] == "Infinity"
    assert normalize_statistics({"prices": "bad"})["prices"] == []
