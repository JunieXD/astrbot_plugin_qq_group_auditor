# astrbot_plugin_qq_group_auditor

`astrbot_plugin_qq_group_auditor` 是 AstrBot 插件，用于 QQ 群加群申请审核。插件在收到加群申请时，会调用当前 AstrBot LLM/provider 判断申请答案是否符合群规则，并根据每个群的配置自动通过、忽略或拒绝。

## 适用场景

- 支持 QQ OneBot v11 / aiocqhttp / NapCat 适配器。
- Bot 必须是群管理员，否则 OneBot 审核接口会失败，插件无法完成自动通过或拒绝。
- 适合需要按申请答案自动初筛加群请求，并把异常情况通知管理员的 QQ 群。

## WebUI 配置

安装插件后，在 AstrBot WebUI 的插件配置中编辑 `group_audits`。该字段使用 `template_list`，可以为多个群分别添加独立配置。

每个群可配置：

| 字段 | 说明 |
| --- | --- |
| `enabled` | 是否启用此群审核。 |
| `group_id` | QQ 群号。 |
| `review_prompt` | 审核提示词，用来描述什么样的申请答案可以通过。 |
| `failure_action` | 不通过动作，只能填写 `ignore` 或 `reject`。 |
| `reject_reason` | 固定拒绝理由，仅在 `failure_action=reject` 时用于平台拒绝消息。 |
| `admin_qq_ids` | 管理员 QQ 号列表，用于接收异常通知，也用于私聊测试命令权限校验。 |
| `notify_on_approve` | 正常自动通过时是否通知管理员。 |
| `notify_on_reject` | 正常自动拒绝时是否通知管理员。 |
| `notify_on_ignore` | 正常忽略时是否通知管理员。 |

## 审核规则

插件只在 LLM 返回严格 JSON，且 `approve=true` 时自动通过申请。

LLM 输出格式必须是 JSON：

```json
{"approve": true, "reason": "符合条件"}
```

其中：

- `approve` 必须是布尔值。
- `reason` 必须是字符串。
- `approve=false`、JSON 解析失败、字段类型不正确，都不会自动通过。

## 不通过动作

当申请答案未通过审核时，插件按该群的 `failure_action` 处理：

- `ignore`：不调用平台审核接口，留给管理员手动处理。
- `reject`：自动拒绝，并使用该群配置的固定 `reject_reason`。

出于安全和可控性考虑，插件不会把 LLM 返回的 `reason` 发给申请人。`reason` 只用于管理员理解审核结果或排查问题。

## 空答案处理

空答案不会调用 LLM。插件会直接按该群配置的 `ignore` 或 `reject` 处理，避免把无意义内容发送给模型。

## 管理员通知

以下情况会私聊通知该群配置的管理员：

- LLM 调用异常。
- LLM 返回内容不是合法 JSON，或不符合 `approve` / `reason` 字段要求。
- OneBot 平台审核接口失败，例如 Bot 不是群管理员、请求已过期或适配器返回错误。

正常通过、拒绝、忽略是否通知管理员，由 `notify_on_approve`、`notify_on_reject`、`notify_on_ignore` 控制。

## 私聊测试命令

群管理员可以私聊 Bot 测试某个群的审核提示词：

```text
/qgaudit test <群号> <申请答案>
```

示例：

```text
/qgaudit test 123456789 我已经阅读群规，会遵守讨论主题
```

命令会调用当前 LLM/provider，并返回类似结果：

```text
approve=True reason=符合条件
```

只有该群 `admin_qq_ids` 中的 QQ 可以使用测试命令。真实 AstrBot 命令解析可能剥离开头的 `/`，插件同时兼容 `qgaudit test ...`，但用户文档中仍推荐使用 `/qgaudit test ...`。
