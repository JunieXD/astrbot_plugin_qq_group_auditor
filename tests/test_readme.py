from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_required_user_flows():
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "NapCat" in text
    assert "Bot 必须是群管理员" in text
    assert "template_list" in text
    assert "/qgaudit test" in text
    assert "approve" in text
    assert "reason" in text
    assert "空答案" in text
    assert "ignore" in text
    assert "reject" in text
    assert "response_format" in text
    assert "重试一次" in text
    assert "500 个字符" in text
    assert "auto_set_card" in text
    assert "{nickname}" in text
    assert "{answer}" in text
    assert "/qgaudit history" in text
    assert "/qgaudit detail" in text
    assert "external_inferred" in text
