# QQ Group Auditor 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 构建一个 AstrBot 插件 `astrbot_plugin_qq_group_auditor`，自动处理 QQ 加群申请：按群配置审核提示词，调用 AstrBot 当前 LLM/provider 严格 JSON 判定，通过则同意，不通过则按配置拒绝或忽略，并支持私聊测试命令。

**架构：** 插件入口 `main.py` 只负责 AstrBot handler 绑定和流程编排；业务逻辑拆到 `qq_group_auditor/` 包内。配置解析、LLM 审核、OneBot/NapCat request 事件适配、通知格式化分别独立，测试用 fake provider/platform/notifier 覆盖主流程，不依赖真实 QQ 或真实 LLM。

**技术栈：** Python 3.10+、AstrBot 插件 SDK、OneBot v11 aiocqhttp/NapCat、uv、pytest、pytest-asyncio。

---

## 参考资料

- 设计规格：`/home/junie/astrbot_plugin_qq_group_auditor/docs/superpowers/specs/2026-06-30-qq-group-auditor-design.md`
- 已验证的本地插件骨架参考：`/home/junie/astrbot_plugin_github_subscriber`
- AstrBot 插件创建文档：https://docs.astrbot.app/dev/star/plugin-new.html
- AstrBot 消息事件文档：https://docs.astrbot.app/dev/star/guides/listen-message-event.html
- AstrBot LLM 调用文档：https://docs.astrbot.app/dev/star/guides/ai.html
- OneBot v11 加群请求 API：`set_group_add_request(flag, sub_type, approve, reason)`

## 文件结构

创建或修改以下文件：

- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/pyproject.toml`
  - uv 项目配置、dev 依赖。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/requirements.txt`
  - AstrBot 插件安装依赖。第一版不需要运行时三方依赖，文件可为空但必须存在。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/pytest.ini`
  - pytest 配置，开启 asyncio auto。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/metadata.yaml`
  - AstrBot 插件元数据。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/_conf_schema.json`
  - WebUI 配置 schema，使用 `template_list` 表达群审核配置。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/README.md`
  - 使用说明、配置示例、安全行为。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/CHANGELOG.md`
  - 从 `v0.1.0` 开始，避免 AstrBot 缺失更新日志警告。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/main.py`
  - AstrBot Star 插件入口、事件 handler、私聊测试命令。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/__init__.py`
  - 包标记和版本常量。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/models.py`
  - `GroupAuditConfig`、`JoinRequest`、`ReviewDecision`、`ActionResult` 等 dataclass。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/config.py`
  - 默认配置、WebUI `template_list` 归一化、群配置查找、管理员判断。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/text.py`
  - 文本摘要、QQ ID 归一化、群号归一化。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/reviewer.py`
  - 构造 LLM prompt、调用 provider wrapper、解析严格 JSON。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/platform.py`
  - 从 AstrBot event/raw OneBot event 提取加群申请；通过 aiocqhttp bot 调用 `set_group_add_request`。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/notifier.py`
  - 管理员私聊通知消息格式化和发送。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/service.py`
  - 审核主流程：过滤、空答案分支、LLM 分支、动作执行、通知触发。
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/tests/`
  - 单元测试和 AstrBot stub。

## 任务 1：项目骨架和 AstrBot 元数据

**文件：**
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/pyproject.toml`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/requirements.txt`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/pytest.ini`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/metadata.yaml`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/__init__.py`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/tests/test_metadata.py`

- [ ] **步骤 1：编写失败的元数据测试**

创建 `tests/test_metadata.py`：

```python
from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_metadata_has_required_astrbot_fields():
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))

    assert metadata["name"] == "astrbot_plugin_qq_group_auditor"
    assert metadata["version"] == "v0.1.0"
    assert metadata["support_platforms"] == ["aiocqhttp"]
    assert metadata["astrbot_version"] == ">=4.16,<5"
    assert metadata["help"]
    assert metadata["display_name"]


def test_runtime_files_exist_for_astrbot_plugin_install():
    assert (ROOT / "requirements.txt").exists()
    assert (ROOT / "CHANGELOG.md").exists()
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
uv init --bare --python 3.10
uv add --dev pytest pytest-asyncio pyyaml
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_metadata.py -q
```

预期：FAIL，至少报 `metadata.yaml` 或 `CHANGELOG.md` 不存在。

- [ ] **步骤 3：创建项目骨架文件**

写入 `pyproject.toml`：

```toml
[project]
name = "astrbot-plugin-qq-group-auditor"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pyyaml>=6",
]
```

写入 `pytest.ini`：

```ini
[pytest]
asyncio_mode = auto
pythonpath = .
```

写入 `metadata.yaml`：

```yaml
name: astrbot_plugin_qq_group_auditor
desc: QQ 加群申请 LLM 自动审核插件，按群配置审核提示词并通过 OneBot/NapCat 处理加群请求。
help: 私聊使用 /qgaudit test <群号> <申请答案> 测试某个群的审核规则。
version: v0.1.0
author: JunieXD
repo: ""
display_name: QQ 加群审核
short_desc: 使用 AstrBot 当前 LLM 自动审核 QQ 加群申请。
support_platforms:
  - aiocqhttp
astrbot_version: ">=4.16,<5"
```

写入 `requirements.txt`：

```text
```

写入 `qq_group_auditor/__init__.py`：

```python
from __future__ import annotations

__version__ = "0.1.0"
```

写入 `CHANGELOG.md`：

```markdown
# Changelog

## v0.1.0 - 2026-06-30

- 初始版本：支持 QQ 加群申请 LLM 自动审核、按群配置、异常私聊通知和私聊测试命令。
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_metadata.py -q
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
git add pyproject.toml uv.lock pytest.ini metadata.yaml requirements.txt CHANGELOG.md qq_group_auditor/__init__.py tests/test_metadata.py
git commit -m "chore: scaffold astrbot qq group auditor plugin"
```

## 任务 2：WebUI 配置 schema 和配置归一化

**文件：**
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/_conf_schema.json`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/models.py`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/config.py`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/tests/test_conf_schema.py`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/tests/test_config.py`

- [ ] **步骤 1：编写 schema 和配置测试**

创建 `tests/test_conf_schema.py`：

```python
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_conf_schema_is_valid_json_and_uses_supported_types():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

    assert schema["group_audits"]["type"] == "template_list"
    template = schema["group_audits"]["templates"]["group_audit"]
    items = template["items"]
    assert items["group_id"]["type"] == "string"
    assert items["review_prompt"]["type"] == "text"
    assert items["failure_action"]["type"] == "string"
    assert items["admin_qq_ids"]["type"] == "list"
    assert items["notify_on_approve"]["type"] == "bool"
    assert items["notify_on_reject"]["type"] == "bool"
    assert items["notify_on_ignore"]["type"] == "bool"
```

创建 `tests/test_config.py`：

```python
from __future__ import annotations

from qq_group_auditor.config import (
    DEFAULT_REJECT_REASON,
    find_group_config,
    is_group_admin,
    normalize_config,
)


def test_normalize_config_adds_defaults():
    config = normalize_config({})

    assert config["group_audits"] == []


def test_normalize_config_preserves_template_list_and_coerces_scalars():
    config = normalize_config(
        {
            "group_audits": [
                {
                    "__template_key": "group_audit",
                    "enabled": True,
                    "group_id": 123456,
                    "review_prompt": "答案必须包含 AutoEmailSender",
                    "failure_action": "reject",
                    "reject_reason": "",
                    "admin_qq_ids": [10001, "10002", ""],
                    "notify_on_approve": True,
                    "notify_on_reject": False,
                    "notify_on_ignore": True,
                }
            ]
        }
    )

    item = config["group_audits"][0]
    assert item["__template_key"] == "group_audit"
    assert item["group_id"] == "123456"
    assert item["reject_reason"] == DEFAULT_REJECT_REASON
    assert item["admin_qq_ids"] == ["10001", "10002"]
    assert item["failure_action"] == "reject"


def test_invalid_failure_action_defaults_to_ignore():
    config = normalize_config(
        {
            "group_audits": [
                {
                    "group_id": "123456",
                    "review_prompt": "规则",
                    "failure_action": "delete",
                }
            ]
        }
    )

    assert config["group_audits"][0]["failure_action"] == "ignore"


def test_find_group_config_ignores_disabled_groups():
    config = normalize_config(
        {
            "group_audits": [
                {"group_id": "1", "enabled": False, "review_prompt": "A"},
                {"group_id": "2", "enabled": True, "review_prompt": "B"},
            ]
        }
    )

    assert find_group_config(config, "1") is None
    assert find_group_config(config, 2)["review_prompt"] == "B"


def test_is_group_admin_uses_group_specific_admin_list():
    config = normalize_config(
        {
            "group_audits": [
                {
                    "group_id": "123456",
                    "review_prompt": "规则",
                    "admin_qq_ids": ["10001"],
                }
            ]
        }
    )

    assert is_group_admin(config, "123456", "10001") is True
    assert is_group_admin(config, "123456", "10002") is False
    assert is_group_admin(config, "999999", "10001") is False
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_conf_schema.py tests/test_config.py -q
```

预期：FAIL，缺少 `_conf_schema.json` 和 `qq_group_auditor.config`。

- [ ] **步骤 3：实现配置模型和归一化**

写入 `qq_group_auditor/models.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FailureAction = Literal["ignore", "reject"]


@dataclass(frozen=True)
class GroupAuditConfig:
    group_id: str
    enabled: bool
    review_prompt: str
    failure_action: FailureAction
    reject_reason: str
    admin_qq_ids: tuple[str, ...]
    notify_on_approve: bool
    notify_on_reject: bool
    notify_on_ignore: bool
```

写入 `qq_group_auditor/config.py`：

```python
from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_REJECT_REASON = "加群答案不符合要求，请重新申请并按提示填写。"
DEFAULT_CONFIG = {"group_audits": []}
VALID_FAILURE_ACTIONS = {"ignore", "reject"}


def normalize_id(value: Any) -> str:
    return str(value or "").strip()


def normalize_admin_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value:
        normalized = normalize_id(item)
        if normalized:
            ids.append(normalized)
    return ids


def normalize_group_item(item: dict[str, Any]) -> dict[str, Any] | None:
    group_id = normalize_id(item.get("group_id"))
    if not group_id:
        return None

    failure_action = str(item.get("failure_action") or "ignore").strip().lower()
    if failure_action not in VALID_FAILURE_ACTIONS:
        failure_action = "ignore"

    reject_reason = str(item.get("reject_reason") or "").strip() or DEFAULT_REJECT_REASON

    return {
        "__template_key": item.get("__template_key") or "group_audit",
        "enabled": bool(item.get("enabled", True)),
        "group_id": group_id,
        "review_prompt": str(item.get("review_prompt") or "").strip(),
        "failure_action": failure_action,
        "reject_reason": reject_reason,
        "admin_qq_ids": normalize_admin_ids(item.get("admin_qq_ids")),
        "notify_on_approve": bool(item.get("notify_on_approve", False)),
        "notify_on_reject": bool(item.get("notify_on_reject", False)),
        "notify_on_ignore": bool(item.get("notify_on_ignore", False)),
    }


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    raw = raw or {}
    for key, value in raw.items():
        config[key] = deepcopy(value)

    group_audits: list[dict[str, Any]] = []
    for item in config.get("group_audits") or []:
        if not isinstance(item, dict):
            continue
        normalized = normalize_group_item(item)
        if normalized is not None:
            group_audits.append(normalized)
    config["group_audits"] = group_audits
    return config


def find_group_config(config: dict[str, Any], group_id: Any) -> dict[str, Any] | None:
    normalized_group_id = normalize_id(group_id)
    for item in config.get("group_audits") or []:
        if item.get("group_id") == normalized_group_id and item.get("enabled", True):
            return item
    return None


def is_group_admin(config: dict[str, Any], group_id: Any, qq_id: Any) -> bool:
    group_config = find_group_config(config, group_id)
    if group_config is None:
        return False
    return normalize_id(qq_id) in set(group_config.get("admin_qq_ids") or [])
```

写入 `_conf_schema.json`：

```json
{
  "group_audits": {
    "description": "QQ群加群审核配置",
    "type": "template_list",
    "default": [],
    "hint": "每条配置对应一个QQ群。Bot 必须是群管理员，自动审核才会生效。",
    "templates": {
      "group_audit": {
        "name": "群审核配置",
        "display_item": "group_id",
        "items": {
          "enabled": {
            "description": "启用此群审核",
            "type": "bool",
            "default": true
          },
          "group_id": {
            "description": "QQ群号",
            "type": "string",
            "default": "",
            "hint": "填写要开启自动加群审核的 QQ 群号。"
          },
          "review_prompt": {
            "description": "审核提示词",
            "type": "text",
            "default": "请判断申请人的加群答案是否符合本群要求。只有答案明确符合要求时才 approve=true。",
            "hint": "描述什么样的答案可以通过。插件会额外要求 LLM 只返回 JSON。"
          },
          "failure_action": {
            "description": "不符合时动作",
            "type": "string",
            "default": "ignore",
            "hint": "填写 ignore 或 reject。ignore 表示不处理，reject 表示自动拒绝。"
          },
          "reject_reason": {
            "description": "固定拒绝理由",
            "type": "text",
            "default": "加群答案不符合要求，请重新申请并按提示填写。"
          },
          "admin_qq_ids": {
            "description": "管理员QQ号列表",
            "type": "list",
            "default": [],
            "hint": "这些 QQ 会收到私聊通知，也可以使用 /qgaudit test。"
          },
          "notify_on_approve": {
            "description": "正常通过时通知管理员",
            "type": "bool",
            "default": false
          },
          "notify_on_reject": {
            "description": "正常拒绝时通知管理员",
            "type": "bool",
            "default": false
          },
          "notify_on_ignore": {
            "description": "正常忽略时通知管理员",
            "type": "bool",
            "default": false
          }
        }
      }
    }
  }
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_conf_schema.py tests/test_config.py -q
```

预期：PASS。

- [ ] **步骤 5：验证 schema JSON**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run python -m json.tool _conf_schema.json >/tmp/qq_group_auditor_schema.json
```

预期：命令退出码 0。

- [ ] **步骤 6：Commit**

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
git add _conf_schema.json qq_group_auditor/models.py qq_group_auditor/config.py tests/test_conf_schema.py tests/test_config.py
git commit -m "feat: add group audit configuration"
```

## 任务 3：文本工具和严格 JSON 审核器

**文件：**
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/text.py`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/reviewer.py`
- 修改：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/models.py`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/tests/test_text.py`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/tests/test_reviewer.py`

- [ ] **步骤 1：编写失败的文本和审核器测试**

创建 `tests/test_text.py`：

```python
from qq_group_auditor.text import summarize_text


def test_summarize_text_trims_whitespace_and_adds_ellipsis():
    assert summarize_text("  abcdef  ", limit=3) == "abc..."


def test_summarize_text_keeps_short_text():
    assert summarize_text("abc", limit=10) == "abc"
```

创建 `tests/test_reviewer.py`：

```python
from __future__ import annotations

import pytest

from qq_group_auditor.reviewer import LLMReviewError, ReviewLLMClient, review_answer


class FakeLLMClient(ReviewLLMClient):
    def __init__(self, response_text: str | Exception) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, str]] = []

    async def generate(self, *, system_prompt: str, prompt: str) -> str:
        self.calls.append({"system_prompt": system_prompt, "prompt": prompt})
        if isinstance(self.response_text, Exception):
            raise self.response_text
        return self.response_text


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
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_text.py tests/test_reviewer.py -q
```

预期：FAIL，缺少 `text.py`、`reviewer.py` 或模型字段。

- [ ] **步骤 3：扩展模型并实现文本工具**

在 `qq_group_auditor/models.py` 增加：

```python
@dataclass(frozen=True)
class ReviewDecision:
    approve: bool
    reason: str
```

写入 `qq_group_auditor/text.py`：

```python
from __future__ import annotations


def summarize_text(value: str, limit: int = 120) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."
```

- [ ] **步骤 4：实现审核器**

写入 `qq_group_auditor/reviewer.py`：

```python
from __future__ import annotations

import json
from typing import Protocol

from .models import ReviewDecision


SYSTEM_PROMPT = (
    "你是QQ群加群申请审核器。你只能返回 JSON，不能返回 Markdown、解释文字或代码块。"
    "JSON 必须包含 approve(boolean) 和 reason(string)。只有申请答案明确符合管理员规则时 approve 才能为 true。"
)


class LLMReviewError(Exception):
    pass


class ReviewLLMClient(Protocol):
    async def generate(self, *, system_prompt: str, prompt: str) -> str:
        ...


def build_review_prompt(
    *,
    group_id: str,
    applicant_qq: str,
    answer: str,
    review_prompt: str,
) -> str:
    return (
        "管理员审核规则：\n"
        f"{review_prompt}\n\n"
        "申请信息：\n"
        f"- QQ群号：{group_id}\n"
        f"- 申请人QQ：{applicant_qq}\n"
        f"- 申请答案：{answer}\n\n"
        "请只返回 JSON，例如：{\"approve\": true, \"reason\": \"符合条件\"}"
    )


def parse_review_response(response_text: str) -> ReviewDecision:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise LLMReviewError("invalid json response") from exc

    if not isinstance(payload, dict):
        raise LLMReviewError("malformed review response")

    approve = payload.get("approve")
    reason = payload.get("reason")
    if not isinstance(approve, bool) or not isinstance(reason, str):
        raise LLMReviewError("malformed review response")

    return ReviewDecision(approve=approve, reason=reason.strip())


async def review_answer(
    client: ReviewLLMClient,
    *,
    group_id: str,
    applicant_qq: str,
    answer: str,
    review_prompt: str,
) -> ReviewDecision:
    prompt = build_review_prompt(
        group_id=group_id,
        applicant_qq=applicant_qq,
        answer=answer,
        review_prompt=review_prompt,
    )
    try:
        response_text = await client.generate(system_prompt=SYSTEM_PROMPT, prompt=prompt)
    except Exception as exc:
        raise LLMReviewError(f"provider failed: {exc}") from exc
    return parse_review_response(response_text)
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_text.py tests/test_reviewer.py -q
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
git add qq_group_auditor/models.py qq_group_auditor/text.py qq_group_auditor/reviewer.py tests/test_text.py tests/test_reviewer.py
git commit -m "feat: add strict llm review parser"
```

## 任务 4：OneBot/NapCat 平台 request 适配

**文件：**
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/platform.py`
- 修改：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/models.py`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/tests/test_platform.py`

- [ ] **步骤 1：编写失败的平台适配测试**

创建 `tests/test_platform.py`：

```python
from __future__ import annotations

import pytest

from qq_group_auditor.platform import (
    PlatformActionError,
    extract_join_request,
    set_group_request,
)


class RawEvent(dict):
    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class FakeMessageObj:
    def __init__(self, raw_message):
        self.raw_message = raw_message


class FakeEvent:
    def __init__(self, raw_message):
        self.message_obj = FakeMessageObj(raw_message)


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail = False

    async def call_action(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("action failed")
        return {"status": "ok"}


class FakePlatform:
    def __init__(self, bot):
        self.bot = bot


class FakeContext:
    def __init__(self, bot):
        self.platform_manager = type("PM", (), {"platform_insts": [FakePlatform(bot)]})()


def test_extract_join_request_from_onebot_add_request():
    event = FakeEvent(
        RawEvent(
            {
                "post_type": "request",
                "request_type": "group",
                "sub_type": "add",
                "group_id": 123456,
                "user_id": 10001,
                "comment": "AutoEmailSender",
                "flag": "flag-1",
            }
        )
    )

    request = extract_join_request(event)

    assert request.group_id == "123456"
    assert request.applicant_qq == "10001"
    assert request.answer == "AutoEmailSender"
    assert request.flag == "flag-1"
    assert request.sub_type == "add"


def test_extract_join_request_ignores_non_group_request():
    event = FakeEvent(RawEvent({"post_type": "message"}))

    assert extract_join_request(event) is None


def test_extract_join_request_raises_for_missing_required_fields():
    event = FakeEvent(
        RawEvent(
            {
                "post_type": "request",
                "request_type": "group",
                "sub_type": "add",
                "group_id": 123456,
            }
        )
    )

    with pytest.raises(ValueError, match="missing"):
        extract_join_request(event)


@pytest.mark.asyncio
async def test_set_group_request_calls_onebot_action():
    bot = FakeBot()
    context = FakeContext(bot)

    await set_group_request(
        context,
        flag="flag-1",
        sub_type="add",
        approve=False,
        reason="答案不符合",
    )

    assert bot.calls == [
        {
            "action": "set_group_add_request",
            "flag": "flag-1",
            "sub_type": "add",
            "approve": False,
            "reason": "答案不符合",
        }
    ]


@pytest.mark.asyncio
async def test_set_group_request_wraps_action_failure():
    bot = FakeBot()
    bot.fail = True
    context = FakeContext(bot)

    with pytest.raises(PlatformActionError, match="set_group_add_request failed"):
        await set_group_request(
            context,
            flag="flag-1",
            sub_type="add",
            approve=True,
            reason="",
        )
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_platform.py -q
```

预期：FAIL，缺少平台适配代码。

- [ ] **步骤 3：扩展模型**

在 `qq_group_auditor/models.py` 增加：

```python
@dataclass(frozen=True)
class JoinRequest:
    group_id: str
    applicant_qq: str
    answer: str
    flag: str
    sub_type: str


@dataclass(frozen=True)
class ActionResult:
    action: str
    reason: str = ""
```

- [ ] **步骤 4：实现平台适配**

写入 `qq_group_auditor/platform.py`：

```python
from __future__ import annotations

from typing import Any

from .models import JoinRequest


class PlatformActionError(Exception):
    pass


def _raw_get(raw: Any, key: str, default: Any = None) -> Any:
    if isinstance(raw, dict):
        return raw.get(key, default)
    return getattr(raw, key, default)


def extract_join_request(event: Any) -> JoinRequest | None:
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if raw is None:
        return None

    if _raw_get(raw, "post_type") != "request" or _raw_get(raw, "request_type") != "group":
        return None

    group_id = str(_raw_get(raw, "group_id") or "").strip()
    user_id = str(_raw_get(raw, "user_id") or "").strip()
    flag = str(_raw_get(raw, "flag") or "").strip()
    sub_type = str(_raw_get(raw, "sub_type") or "add").strip() or "add"
    answer = str(_raw_get(raw, "comment") or "").strip()

    if not group_id or not user_id or not flag:
        raise ValueError("missing required group request fields")

    return JoinRequest(
        group_id=group_id,
        applicant_qq=user_id,
        answer=answer,
        flag=flag,
        sub_type=sub_type,
    )


def _iter_platforms(context: Any):
    manager = getattr(context, "platform_manager", None)
    if manager is None:
        return []
    if hasattr(manager, "get_insts"):
        return manager.get_insts()
    return getattr(manager, "platform_insts", []) or []


def find_onebot_bot(context: Any) -> Any:
    for platform in _iter_platforms(context):
        bot = getattr(platform, "bot", None)
        if bot is not None and hasattr(bot, "call_action"):
            return bot
    raise PlatformActionError("onebot bot api not found")


async def set_group_request(
    context: Any,
    *,
    flag: str,
    sub_type: str,
    approve: bool,
    reason: str,
) -> None:
    bot = find_onebot_bot(context)
    try:
        await bot.call_action(
            action="set_group_add_request",
            flag=flag,
            sub_type=sub_type,
            approve=approve,
            reason=reason,
        )
    except Exception as exc:
        raise PlatformActionError(f"set_group_add_request failed: {exc}") from exc
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_platform.py -q
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
git add qq_group_auditor/models.py qq_group_auditor/platform.py tests/test_platform.py
git commit -m "feat: add onebot group request adapter"
```

## 任务 5：通知格式化和管理员私聊发送

**文件：**
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/notifier.py`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/tests/test_notifier.py`

- [ ] **步骤 1：编写失败的通知测试**

创建 `tests/test_notifier.py`：

```python
from __future__ import annotations

import pytest

from qq_group_auditor.models import JoinRequest
from qq_group_auditor.notifier import format_notice, send_admin_notice


class FakeContext:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    async def send_message(self, session: str, message_chain: object) -> bool:
        self.sent.append((session, message_chain))
        return True


def chain_text(chain: object) -> str:
    return chain.get_plain_text() if hasattr(chain, "get_plain_text") else str(chain.chain[0])


def test_format_notice_contains_group_applicant_action_and_reason():
    request = JoinRequest(
        group_id="123456",
        applicant_qq="10001",
        answer="AutoEmailSender 是邮件工具",
        flag="flag",
        sub_type="add",
    )

    text = format_notice(
        title="加群审核通过",
        request=request,
        action="approve",
        reason="符合条件",
    )

    assert "123456" in text
    assert "10001" in text
    assert "approve" in text
    assert "符合条件" in text
    assert "AutoEmailSender" in text


@pytest.mark.asyncio
async def test_send_admin_notice_sends_private_messages_to_all_admins():
    context = FakeContext()

    await send_admin_notice(
        context,
        admin_qq_ids=["10001", "10002"],
        text="通知内容",
        platform_name="aiocqhttp",
    )

    assert [target for target, _ in context.sent] == [
        "aiocqhttp:FriendMessage:10001",
        "aiocqhttp:FriendMessage:10002",
    ]
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_notifier.py -q
```

预期：FAIL，缺少 notifier。

- [ ] **步骤 3：实现通知模块**

写入 `qq_group_auditor/notifier.py`：

```python
from __future__ import annotations

from typing import Any

try:
    from astrbot.api.event import MessageChain
except ImportError:  # pragma: no cover - only used in local tests without AstrBot.
    class MessageChain:
        def __init__(self, chain: list[Any] | None = None) -> None:
            self.chain = list(chain or [])

        def message(self, text: str):
            self.chain.append(text)
            return self

        def get_plain_text(self) -> str:
            return "".join(str(item) for item in self.chain)

from .models import JoinRequest
from .text import summarize_text


def private_umo(platform_name: str, qq_id: str) -> str:
    return f"{platform_name}:FriendMessage:{qq_id}"


def format_notice(
    *,
    title: str,
    request: JoinRequest,
    action: str,
    reason: str = "",
    error: str = "",
) -> str:
    lines = [
        title,
        f"群号：{request.group_id}",
        f"申请人：{request.applicant_qq}",
        f"动作：{action}",
        f"答案：{summarize_text(request.answer, 160) or '(空)'}",
    ]
    if reason:
        lines.append(f"LLM理由：{reason}")
    if error:
        lines.append(f"错误：{error}")
    return "\n".join(lines)


async def send_admin_notice(
    context: Any,
    *,
    admin_qq_ids: list[str],
    text: str,
    platform_name: str = "aiocqhttp",
) -> None:
    for qq_id in admin_qq_ids:
        target = private_umo(platform_name, str(qq_id))
        await context.send_message(target, MessageChain().message(text))
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_notifier.py -q
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
git add qq_group_auditor/notifier.py tests/test_notifier.py
git commit -m "feat: add admin private notifications"
```

## 任务 6：审核服务主流程

**文件：**
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/qq_group_auditor/service.py`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/tests/test_service.py`

- [ ] **步骤 1：编写失败的服务流程测试**

创建 `tests/test_service.py`：

```python
from __future__ import annotations

import pytest

from qq_group_auditor.models import JoinRequest, ReviewDecision
from qq_group_auditor.reviewer import LLMReviewError
from qq_group_auditor.service import AuditService


class FakeReviewer:
    def __init__(self, decision: ReviewDecision | Exception) -> None:
        self.decision = decision
        self.calls = 0

    async def review(self, *, group_config, request):
        self.calls += 1
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


class FakePlatform:
    def __init__(self) -> None:
        self.actions: list[tuple[JoinRequest, bool, str]] = []
        self.fail = False

    async def set_group_request(self, request: JoinRequest, *, approve: bool, reason: str):
        if self.fail:
            raise RuntimeError("platform failed")
        self.actions.append((request, approve, reason))


class FakeNotifier:
    def __init__(self) -> None:
        self.notices: list[tuple[list[str], str]] = []

    async def notify(self, *, group_config, request, title, action, reason="", error=""):
        self.notices.append((group_config["admin_qq_ids"], f"{title}|{action}|{reason}|{error}"))


def group_config(**overrides):
    config = {
        "group_id": "123",
        "enabled": True,
        "review_prompt": "规则",
        "failure_action": "ignore",
        "reject_reason": "请重新申请",
        "admin_qq_ids": ["10001"],
        "notify_on_approve": False,
        "notify_on_reject": False,
        "notify_on_ignore": False,
    }
    config.update(overrides)
    return config


def request(answer: str = "答案") -> JoinRequest:
    return JoinRequest(
        group_id="123",
        applicant_qq="20001",
        answer=answer,
        flag="flag",
        sub_type="add",
    )


@pytest.mark.asyncio
async def test_approve_calls_platform_approve_and_optional_notice():
    platform = FakePlatform()
    notifier = FakeNotifier()
    service = AuditService(FakeReviewer(ReviewDecision(True, "符合")), platform, notifier)

    result = await service.handle_request(group_config(notify_on_approve=True), request())

    assert result.action == "approve"
    assert platform.actions == [(request(), True, "")]
    assert notifier.notices[0][1].startswith("加群审核通过|approve|符合|")


@pytest.mark.asyncio
async def test_reject_false_decision_uses_fixed_reason():
    platform = FakePlatform()
    notifier = FakeNotifier()
    service = AuditService(FakeReviewer(ReviewDecision(False, "不符合")), platform, notifier)

    result = await service.handle_request(
        group_config(failure_action="reject", notify_on_reject=True),
        request(),
    )

    assert result.action == "reject"
    assert platform.actions == [(request(), False, "请重新申请")]
    assert notifier.notices[0][1].startswith("加群审核拒绝|reject|不符合|")


@pytest.mark.asyncio
async def test_ignore_false_decision_does_not_call_platform():
    platform = FakePlatform()
    notifier = FakeNotifier()
    service = AuditService(FakeReviewer(ReviewDecision(False, "不符合")), platform, notifier)

    result = await service.handle_request(group_config(notify_on_ignore=True), request())

    assert result.action == "ignore"
    assert platform.actions == []
    assert notifier.notices[0][1].startswith("加群审核忽略|ignore|不符合|")


@pytest.mark.asyncio
async def test_empty_answer_skips_reviewer_and_follows_failure_action():
    reviewer = FakeReviewer(ReviewDecision(True, "不会调用"))
    platform = FakePlatform()
    service = AuditService(reviewer, platform, FakeNotifier())

    result = await service.handle_request(group_config(failure_action="reject"), request("   "))

    assert result.action == "reject"
    assert reviewer.calls == 0
    assert platform.actions == [(request("   "), False, "请重新申请")]


@pytest.mark.asyncio
async def test_llm_error_notifies_admin_and_leaves_request_untouched():
    platform = FakePlatform()
    notifier = FakeNotifier()
    service = AuditService(FakeReviewer(LLMReviewError("invalid json")), platform, notifier)

    result = await service.handle_request(group_config(), request())

    assert result.action == "error"
    assert platform.actions == []
    assert "LLM审核异常" in notifier.notices[0][1]
    assert "invalid json" in notifier.notices[0][1]
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_service.py -q
```

预期：FAIL，缺少 `service.py`。

- [ ] **步骤 3：实现服务主流程**

写入 `qq_group_auditor/service.py`：

```python
from __future__ import annotations

from typing import Any, Protocol

from .models import ActionResult, JoinRequest, ReviewDecision
from .reviewer import LLMReviewError


class ReviewerPort(Protocol):
    async def review(self, *, group_config: dict[str, Any], request: JoinRequest) -> ReviewDecision:
        ...


class PlatformPort(Protocol):
    async def set_group_request(self, request: JoinRequest, *, approve: bool, reason: str) -> None:
        ...


class NotifierPort(Protocol):
    async def notify(
        self,
        *,
        group_config: dict[str, Any],
        request: JoinRequest,
        title: str,
        action: str,
        reason: str = "",
        error: str = "",
    ) -> None:
        ...


class AuditService:
    def __init__(
        self,
        reviewer: ReviewerPort,
        platform: PlatformPort,
        notifier: NotifierPort,
    ) -> None:
        self.reviewer = reviewer
        self.platform = platform
        self.notifier = notifier

    async def handle_request(
        self,
        group_config: dict[str, Any],
        request: JoinRequest,
    ) -> ActionResult:
        try:
            decision = await self._decision_for_request(group_config, request)
        except LLMReviewError as exc:
            await self.notifier.notify(
                group_config=group_config,
                request=request,
                title="LLM审核异常",
                action="error",
                error=str(exc),
            )
            return ActionResult(action="error", reason=str(exc))

        if decision.approve:
            try:
                await self.platform.set_group_request(request, approve=True, reason="")
            except Exception as exc:
                await self._notify_platform_error(group_config, request, "approve", exc)
                return ActionResult(action="error", reason=str(exc))
            if group_config.get("notify_on_approve", False):
                await self.notifier.notify(
                    group_config=group_config,
                    request=request,
                    title="加群审核通过",
                    action="approve",
                    reason=decision.reason,
                )
            return ActionResult(action="approve", reason=decision.reason)

        if group_config.get("failure_action") == "reject":
            reject_reason = str(group_config.get("reject_reason") or "")
            try:
                await self.platform.set_group_request(
                    request,
                    approve=False,
                    reason=reject_reason,
                )
            except Exception as exc:
                await self._notify_platform_error(group_config, request, "reject", exc)
                return ActionResult(action="error", reason=str(exc))
            if group_config.get("notify_on_reject", False):
                await self.notifier.notify(
                    group_config=group_config,
                    request=request,
                    title="加群审核拒绝",
                    action="reject",
                    reason=decision.reason,
                )
            return ActionResult(action="reject", reason=decision.reason)

        if group_config.get("notify_on_ignore", False):
            await self.notifier.notify(
                group_config=group_config,
                request=request,
                title="加群审核忽略",
                action="ignore",
                reason=decision.reason,
            )
        return ActionResult(action="ignore", reason=decision.reason)

    async def _decision_for_request(
        self,
        group_config: dict[str, Any],
        request: JoinRequest,
    ) -> ReviewDecision:
        if not request.answer.strip():
            return ReviewDecision(False, "申请答案为空")
        return await self.reviewer.review(group_config=group_config, request=request)

    async def _notify_platform_error(
        self,
        group_config: dict[str, Any],
        request: JoinRequest,
        action: str,
        exc: Exception,
    ) -> None:
        await self.notifier.notify(
            group_config=group_config,
            request=request,
            title="平台审核接口异常",
            action=action,
            error=str(exc),
        )
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_service.py -q
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
git add qq_group_auditor/service.py tests/test_service.py
git commit -m "feat: add audit service flow"
```

## 任务 7：AstrBot 插件入口和私聊测试命令

**文件：**
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/main.py`
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/tests/test_main.py`

- [ ] **步骤 1：编写失败的 AstrBot stub 和入口测试**

创建 `tests/test_main.py`：

```python
from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import pytest


def install_astrbot_stubs(monkeypatch):
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event_module = types.ModuleType("astrbot.api.event")
    star_module = types.ModuleType("astrbot.api.star")

    class AstrBotConfig(dict):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.save_count = 0

        def save_config(self) -> None:
            self.save_count += 1

    class Logger:
        def __init__(self) -> None:
            self.warnings: list[Any] = []
            self.exceptions: list[Any] = []

        def warning(self, *args: Any) -> None:
            self.warnings.append(args)

        def exception(self, *args: Any) -> None:
            self.exceptions.append(args)

        def info(self, *args: Any) -> None:
            pass

    class MessageChain:
        def __init__(self, chain: list[Any] | None = None) -> None:
            self.chain = list(chain or [])

        def message(self, text: str):
            self.chain.append(text)
            return self

    class Filter:
        class EventMessageType:
            PRIVATE_MESSAGE = "private"
            OTHER_MESSAGE = "other"

        class PlatformAdapterType:
            AIOCQHTTP = "aiocqhttp"

        def command_group(self, name: str):
            class CommandGroup:
                def __init__(self, name: str) -> None:
                    self.name = name

                def __call__(self, func):
                    func.__command_group__ = self.name
                    return self

                def command(self, command_name: str):
                    def decorator(func):
                        func.__command_name__ = command_name
                        return func

                    return decorator

            return CommandGroup(name)

        def event_message_type(self, event_type):
            def decorator(func):
                func.__event_message_type__ = event_type
                return func

            return decorator

        def platform_adapter_type(self, platform_type):
            def decorator(func):
                func.__platform_adapter_type__ = platform_type
                return func

            return decorator

    class Context:
        async def get_current_chat_provider_id(self, umo: str) -> str:
            return "provider-id"

        async def llm_generate(self, **kwargs):
            return type("Resp", (), {"completion_text": '{"approve": true, "reason": "ok"}'})()

    class Star:
        def __init__(self, context: Context) -> None:
            self.context = context

    api.AstrBotConfig = AstrBotConfig
    api.logger = Logger()
    event_module.filter = Filter()
    event_module.AstrMessageEvent = object
    event_module.MessageChain = MessageChain
    star_module.Context = Context
    star_module.Star = Star

    monkeypatch.setitem(sys.modules, "astrbot", astrbot)
    monkeypatch.setitem(sys.modules, "astrbot.api", api)
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.star", star_module)
    sys.modules.pop("main", None)
    return importlib.import_module("main")


class FakePrivateEvent:
    def __init__(
        self,
        sender_id: str = "10001",
        message_str: str = "/qgaudit test 123 答案 带 空格",
        umo: str = "aiocqhttp:FriendMessage:10001",
    ) -> None:
        self.unified_msg_origin = umo
        self.message_str = message_str
        self._sender_id = sender_id
        self.replies: list[str] = []

    def is_private_chat(self) -> bool:
        return True

    def get_sender_id(self) -> str:
        return self._sender_id

    def plain_result(self, message: str) -> str:
        self.replies.append(message)
        return message


async def collect(async_iterable):
    return [item async for item in async_iterable]


def test_imports_and_registers_qgaudit_command(monkeypatch):
    module = install_astrbot_stubs(monkeypatch)

    assert module.qgaudit.name == "qgaudit"
    assert hasattr(module, "QQGroupAuditorPlugin")


@pytest.mark.asyncio
async def test_test_command_requires_group_admin(monkeypatch):
    module = install_astrbot_stubs(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(
        module.Context(),
        module.AstrBotConfig(
            {
                "group_audits": [
                    {
                        "group_id": "123",
                        "review_prompt": "规则",
                        "admin_qq_ids": ["10001"],
                    }
                ]
            }
        ),
    )

    denied = await collect(plugin.qgaudit_test(FakePrivateEvent("99999")))
    allowed = await collect(plugin.qgaudit_test(FakePrivateEvent("10001")))

    assert "无权限" in denied[0]
    assert "approve=True" in allowed[0]
    assert "reason=ok" in allowed[0]


@pytest.mark.asyncio
async def test_test_command_preserves_answer_spaces(monkeypatch):
    module = install_astrbot_stubs(monkeypatch)
    plugin = module.QQGroupAuditorPlugin(
        module.Context(),
        module.AstrBotConfig(
            {
                "group_audits": [
                    {
                        "group_id": "123",
                        "review_prompt": "规则",
                        "admin_qq_ids": ["10001"],
                    }
                ]
            }
        ),
    )

    event = FakePrivateEvent("10001", "/qgaudit test 123 答案 带 空格")
    replies = await collect(plugin.qgaudit_test(event))

    assert "approve=True" in replies[0]
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_main.py -q
```

预期：FAIL，缺少 `main.py`。

- [ ] **步骤 3：实现 AstrBot LLM wrapper 和插件入口**

写入 `main.py`：

```python
from __future__ import annotations

from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:
    from .qq_group_auditor.config import find_group_config, is_group_admin, normalize_config
    from .qq_group_auditor.models import JoinRequest
    from .qq_group_auditor.notifier import format_notice, send_admin_notice
    from .qq_group_auditor.platform import extract_join_request, set_group_request
    from .qq_group_auditor.reviewer import review_answer
    from .qq_group_auditor.service import AuditService
except ImportError:  # pragma: no cover - local test/dev import support.
    from qq_group_auditor.config import find_group_config, is_group_admin, normalize_config
    from qq_group_auditor.models import JoinRequest
    from qq_group_auditor.notifier import format_notice, send_admin_notice
    from qq_group_auditor.platform import extract_join_request, set_group_request
    from qq_group_auditor.reviewer import review_answer
    from qq_group_auditor.service import AuditService


@filter.command_group("qgaudit")
def qgaudit():
    pass


class AstrBotLLMClient:
    def __init__(self, context: Context, umo: str | None) -> None:
        self.context = context
        self.umo = umo

    async def generate(self, *, system_prompt: str, prompt: str) -> str:
        if self.umo:
            provider_id = await self.context.get_current_chat_provider_id(self.umo)
        else:
            provider = self.context.get_using_provider(None)
            if provider is None:
                raise RuntimeError("Provider not found")
            provider_id = provider.meta().id
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            system_prompt=system_prompt,
            prompt=prompt,
        )
        return response.completion_text


class RuntimeReviewer:
    def __init__(self, context: Context, umo: str | None) -> None:
        self.client = AstrBotLLMClient(context, umo)

    async def review(self, *, group_config: dict[str, Any], request: JoinRequest):
        return await review_answer(
            self.client,
            group_id=request.group_id,
            applicant_qq=request.applicant_qq,
            answer=request.answer,
            review_prompt=group_config.get("review_prompt", ""),
        )


class RuntimePlatform:
    def __init__(self, context: Context) -> None:
        self.context = context

    async def set_group_request(self, request: JoinRequest, *, approve: bool, reason: str) -> None:
        await set_group_request(
            self.context,
            flag=request.flag,
            sub_type=request.sub_type,
            approve=approve,
            reason=reason,
        )


class RuntimeNotifier:
    def __init__(self, context: Context, platform_name: str = "aiocqhttp") -> None:
        self.context = context
        self.platform_name = platform_name

    async def notify(
        self,
        *,
        group_config: dict[str, Any],
        request: JoinRequest,
        title: str,
        action: str,
        reason: str = "",
        error: str = "",
    ) -> None:
        text = format_notice(
            title=title,
            request=request,
            action=action,
            reason=reason,
            error=error,
        )
        try:
            await send_admin_notice(
                self.context,
                admin_qq_ids=group_config.get("admin_qq_ids") or [],
                text=text,
                platform_name=self.platform_name,
            )
        except Exception as exc:
            logger.warning("QQ group auditor failed to send admin notice: %s", exc)


class QQGroupAuditorPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.normalized_config = normalize_config(dict(config))

    @filter.event_message_type(filter.EventMessageType.OTHER_MESSAGE)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def on_other_message(self, event: AstrMessageEvent):
        try:
            request = extract_join_request(event)
        except Exception as exc:
            logger.warning("QQ group auditor failed to extract request: %s", exc)
            return
        if request is None:
            return

        group_config = find_group_config(self.normalized_config, request.group_id)
        if group_config is None:
            return

        service = AuditService(
            RuntimeReviewer(self.context, getattr(event, "unified_msg_origin", None)),
            RuntimePlatform(self.context),
            RuntimeNotifier(self.context, event.get_platform_name()),
        )
        await service.handle_request(group_config, request)

    @qgaudit.command("test")
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def qgaudit_test(self, event: AstrMessageEvent):
        parsed = parse_test_command(event.message_str)
        if parsed is None:
            yield event.plain_result("用法：/qgaudit test <群号> <申请答案>")
            return
        group_id, answer = parsed
        sender_id = event.get_sender_id()
        if not is_group_admin(self.normalized_config, group_id, sender_id):
            yield event.plain_result("无权限：你不在该群审核配置的管理员QQ列表中。")
            return

        group_config = find_group_config(self.normalized_config, group_id)
        if group_config is None:
            yield event.plain_result(f"未找到已启用的群审核配置：{group_id}")
            return

        request = JoinRequest(
            group_id=str(group_id),
            applicant_qq=sender_id,
            answer=answer,
            flag="test",
            sub_type="add",
        )
        reviewer = RuntimeReviewer(self.context, event.unified_msg_origin)
        try:
            decision = await reviewer.review(group_config=group_config, request=request)
        except Exception as exc:
            yield event.plain_result(f"LLM审核异常：{exc}")
            return
        yield event.plain_result(f"approve={decision.approve}\nreason={decision.reason}")


def parse_test_command(message_str: str) -> tuple[str, str] | None:
    parts = str(message_str or "").strip().split(maxsplit=3)
    if len(parts) < 4:
        return None
    command, subcommand, group_id, answer = parts
    if command != "/qgaudit" or subcommand != "test" or not group_id.strip() or not answer.strip():
        return None
    return group_id.strip(), answer.strip()
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_main.py -q
```

预期：PASS。

- [ ] **步骤 5：检查真实 AstrBot API 兼容性**

运行以下只读命令，确认 `filter.EventMessageType.OTHER_MESSAGE`、`filter.PlatformAdapterType.AIOCQHTTP`、`context.get_using_provider(None)` 在当前 AstrBot master 仍存在：

```bash
gh api 'repos/AstrBotDevs/AstrBot/contents/astrbot/api/event/filter/__init__.py?ref=master' --jq .content | base64 -d | rg 'EventMessageType|PlatformAdapterType'
gh api 'repos/AstrBotDevs/AstrBot/contents/astrbot/core/star/context.py?ref=master' --jq .content | base64 -d | rg 'def get_using_provider|def llm_generate|def get_current_chat_provider_id'
gh api 'repos/AstrBotDevs/AstrBot/contents/astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py?ref=master' --jq .content | base64 -d | rg 'on_request|raw_message|set_group_add_request|call_action'
```

预期：前两条能找到对应 API；第三条至少能找到 `on_request`、`raw_message`、`call_action`。如果找不到 `set_group_add_request` 字符串是可接受的，因为 AstrBot adapter 不一定硬编码这个 action。

- [ ] **步骤 6：Commit**

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
git add main.py tests/test_main.py
git commit -m "feat: wire astrbot qq group auditor plugin"
```

## 任务 8：README 和用户文档

**文件：**
- 创建：`/home/junie/astrbot_plugin_qq_group_auditor/README.md`

- [ ] **步骤 1：编写失败的 README 测试**

创建 `tests/test_readme.py`：

```python
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
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_readme.py -q
```

预期：FAIL，缺少 README。

- [ ] **步骤 3：编写 README**

写入 `README.md`：

```markdown
# QQ 加群审核

`astrbot_plugin_qq_group_auditor` 是一个 AstrBot 插件，用于在 QQ 群收到加群申请时调用 AstrBot 当前配置的 LLM/provider 自动判断是否通过。

## 支持平台

- QQ OneBot v11 / aiocqhttp / NapCat。
- Bot 必须是群管理员，否则 OneBot 审核接口会失败。

## 功能

- 在 WebUI 中用 `template_list` 配置多个群。
- 每个群独立配置审核提示词、不通过动作、拒绝理由、管理员 QQ、正常结果通知开关。
- 只有 LLM 返回严格 JSON 且 `approve=true` 时才会自动通过。
- 空答案不调用 LLM，直接按该群的 `ignore` 或 `reject` 处理。
- LLM 异常、JSON 解析失败、平台审核接口失败会私聊通知该群管理员。
- 私聊测试命令：`/qgaudit test <群号> <申请答案>`。

## LLM 输出格式

LLM 必须只返回 JSON：

```json
{"approve": true, "reason": "符合条件"}
```

`approve` 必须是布尔值，`reason` 必须是字符串。任何自然语言解释、无法解析的 JSON 或字段类型错误都会被视为异常，不会自动通过或拒绝申请。

## 不通过动作

- `ignore`：不处理申请，留给其他管理员处理。
- `reject`：自动拒绝申请，并使用配置里的固定拒绝理由。插件不会把 LLM 的 `reason` 发给申请人。
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_readme.py -q
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
git add README.md tests/test_readme.py
git commit -m "docs: add qq group auditor usage guide"
```

## 任务 9：全量验证和收尾

**文件：**
- 修改：按验证结果修正前面任务中的文件。

- [ ] **步骤 1：运行全量测试**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest -q
```

预期：全部 PASS。

- [ ] **步骤 2：运行 schema 校验**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run python -m json.tool _conf_schema.json >/tmp/qq_group_auditor_schema.json
```

预期：退出码 0。

- [ ] **步骤 3：检查插件安装必要文件**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
test -f metadata.yaml
test -f _conf_schema.json
test -f requirements.txt
test -f main.py
test -f README.md
test -f CHANGELOG.md
```

预期：全部退出码 0。

- [ ] **步骤 4：检查 git 状态**

运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
git status --short
```

预期：无输出。

- [ ] **步骤 5：如果有验证修复则 commit**

如果步骤 1-3 中有修复，运行：

```bash
cd /home/junie/astrbot_plugin_qq_group_auditor
git add .
git commit -m "fix: stabilize qq group auditor plugin"
```

如果没有修复，不创建空 commit。

## 规格覆盖自检

- WebUI 按群配置：任务 2 覆盖。
- `template_list` 避免不支持 `dict`：任务 2 覆盖。
- 使用 AstrBot 当前 provider：任务 7 覆盖。
- 严格 JSON 判定：任务 3 覆盖。
- 空答案不调用 LLM：任务 6 覆盖。
- `ignore/reject` 每群配置：任务 2 和任务 6 覆盖。
- 固定拒绝理由：任务 2 和任务 6 覆盖。
- 异常私聊管理员：任务 5 和任务 6 覆盖。
- 正常通过/拒绝/忽略可选通知：任务 6 覆盖。
- 只做私聊测试命令：任务 7 覆盖。
- 不保存审核记录：所有任务均未引入持久状态。
- README/CHANGELOG：任务 1 和任务 8 覆盖。
