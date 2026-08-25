from __future__ import annotations

from datetime import datetime
from string import Formatter
from typing import Any
from zoneinfo import ZoneInfo


ALLOWED_CARD_FIELDS = {"qq", "nickname", "question", "answer", "join_date", "join_time"}
DEFAULT_CARD_MAX_LENGTH = 60


class CardTemplateError(ValueError):
    pass


def normalize_card_value(value: Any) -> str:
    return " ".join(str(value or "").split())


def template_fields(template: str) -> set[str]:
    fields: set[str] = set()
    try:
        parsed = Formatter().parse(template)
        for _, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            if field_name not in ALLOWED_CARD_FIELDS:
                raise CardTemplateError(f"unsupported placeholder: {{{field_name}}}")
            if format_spec or conversion:
                raise CardTemplateError("format specs and conversions are not supported")
            fields.add(field_name)
    except ValueError as exc:
        if isinstance(exc, CardTemplateError):
            raise
        raise CardTemplateError(f"invalid card template: {exc}") from exc
    return fields


def render_card(
    template: str,
    *,
    qq: str,
    nickname: str,
    question: str,
    answer: str,
    joined_at: int,
    max_length: int = DEFAULT_CARD_MAX_LENGTH,
) -> str:
    fields = template_fields(template)
    local_time = datetime.fromtimestamp(joined_at, tz=ZoneInfo("Asia/Shanghai"))
    values = {
        "qq": normalize_card_value(qq),
        "nickname": normalize_card_value(nickname) or normalize_card_value(qq),
        "question": normalize_card_value(question),
        "answer": normalize_card_value(answer),
        "join_date": local_time.strftime("%Y-%m-%d"),
        "join_time": local_time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    missing = sorted(field for field in fields if not values[field])
    if missing:
        raise CardTemplateError(f"missing value for {{{missing[0]}}}")

    try:
        rendered = normalize_card_value(template.format_map(values))
    except (KeyError, ValueError) as exc:
        raise CardTemplateError(f"invalid card template: {exc}") from exc
    if not rendered:
        raise CardTemplateError("rendered card is empty")
    return rendered[:max_length]
