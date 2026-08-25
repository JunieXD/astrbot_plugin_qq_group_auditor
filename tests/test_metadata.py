from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_metadata_has_required_astrbot_fields():
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))

    assert metadata["name"] == "astrbot_plugin_qq_group_auditor"
    assert metadata["version"] == "v0.2.0"
    assert metadata["support_platforms"] == ["aiocqhttp"]
    assert metadata["astrbot_version"] == ">=4.16,<5"
    assert metadata["help"]
    assert metadata["display_name"]


def test_runtime_files_exist_for_astrbot_plugin_install():
    assert (ROOT / "requirements.txt").exists()
    assert (ROOT / "CHANGELOG.md").exists()
