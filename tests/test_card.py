from __future__ import annotations

import pytest

from qq_group_auditor.card import CardTemplateError, render_card


def test_render_card_supports_answer_nickname_qq_and_join_time():
    result = render_card(
        "{nickname}-{answer}-{qq}-{join_date}",
        qq="10001",
        nickname=" Alice\n ",
        question="来源？",
        answer=" GitHub\n推荐 ",
        joined_at=0,
    )

    assert result == "Alice-GitHub 推荐-10001-1970-01-01"


def test_render_card_falls_back_to_qq_for_missing_nickname():
    assert render_card(
        "{nickname}",
        qq="10001",
        nickname="",
        question="",
        answer="",
        joined_at=1,
    ) == "10001"


def test_render_card_skips_missing_answer_instead_of_rendering_partial_card():
    with pytest.raises(CardTemplateError, match=r"missing value for \{answer\}"):
        render_card(
            "{nickname}-{answer}",
            qq="10001",
            nickname="Alice",
            question="",
            answer="",
            joined_at=1,
        )


def test_render_card_rejects_unknown_placeholder_and_truncates():
    with pytest.raises(CardTemplateError, match="unsupported placeholder"):
        render_card(
            "{user}",
            qq="10001",
            nickname="Alice",
            question="",
            answer="",
            joined_at=1,
        )

    assert render_card(
        "{answer}",
        qq="10001",
        nickname="Alice",
        question="",
        answer="x" * 100,
        joined_at=1,
    ) == "x" * 60
