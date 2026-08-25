from __future__ import annotations

import re


_ANSWER_LABEL_RE = re.compile(
    r"(?:^|[\r\n])\s*(?:答案|回答)\s*[:：]\s*",
    re.IGNORECASE,
)


def summarize_text(value: str, limit: int = 120) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def extract_application_answer(comment: str) -> str:
    """Return the answer portion of a labeled QQ group application comment."""
    raw_comment = str(comment or "").strip()
    matches = list(_ANSWER_LABEL_RE.finditer(raw_comment))
    if not matches:
        return raw_comment
    return raw_comment[matches[-1].end() :].strip()
