from qq_group_auditor.text import summarize_text


def test_summarize_text_trims_whitespace_and_adds_ellipsis():
    assert summarize_text("  abcdef  ", limit=3) == "abc..."


def test_summarize_text_keeps_short_text():
    assert summarize_text("abc", limit=10) == "abc"
