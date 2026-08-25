from qq_group_auditor.text import extract_application_answer, summarize_text


def test_summarize_text_trims_whitespace_and_adds_ellipsis():
    assert summarize_text("  abcdef  ", limit=3) == "abc..."


def test_summarize_text_keeps_short_text():
    assert summarize_text("abc", limit=10) == "abc"


def test_extract_application_answer_returns_only_a_labeled_answer():
    assert extract_application_answer("问题：毕业年份\n答案：2028") == "2028"
    assert extract_application_answer("问题: 年级\r\n回答: 24级") == "24级"
    assert extract_application_answer("普通答案") == "普通答案"
    assert extract_application_answer("我的答案：不应误切") == "我的答案：不应误切"
