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
| `invite_action` | 受邀入群动作，可填写 `approve`、`ignore` 或 `reject`，默认 `ignore`。 |
| `reject_reason` | 固定拒绝理由，在 `failure_action=reject` 或 `invite_action=reject` 时用于平台拒绝消息。 |
| `admin_qq_ids` | 管理员 QQ 号列表，用于接收异常通知，也用于私聊测试命令权限校验。 |
| `notify_on_approve` | 正常自动通过时是否通知管理员。 |
| `notify_on_reject` | 正常自动拒绝时是否通知管理员。 |
| `notify_on_ignore` | 正常忽略时是否通知管理员。 |
| `audit_log_enabled` | 是否保存每次申请、审批、入群和退群记录，默认开启。 |
| `auto_set_card` | 新成员实际入群后是否自动修改群名片，默认关闭。 |
| `card_template` | 群名片模板，默认 `{nickname}`。 |
| `application_question` | 平台无法读取加群问题时使用的备用问题文本。 |

## 入群审计记录

插件使用 SQLite 为每一次申请保存独立记录。同一个 QQ 退群、被踢或重新申请时不会覆盖旧记录。审计内容包括：

- QQ号、申请时间、申请时昵称、问题快照、答案和原始验证信息。
- 插件审核决定、平台审批动作、操作者 QQ、执行结果和错误信息。
- 实际入群时间、批准或邀请人、主动退群或被踢时间及操作者。
- 群名片修改前后的内容和执行结果。

数据库位于 AstrBot 的 `data/plugin_data/astrbot_plugin_qq_group_auditor/audit.sqlite3`，默认长期保留。

管理员可以私聊 Bot 查询：

```text
/qgaudit history <群号> <QQ号> [条数]
/qgaudit detail <群号> <记录ID>
```

没有对应申请的直接邀请入群会使用 `J<编号>` 作为记录ID，同样可以传给 `detail` 查询。

只有该群 `admin_qq_ids` 中的 QQ 可以查询。申请答案可能包含个人信息，不支持在群聊中查询。

NapCat 当前群系统消息接口只能说明申请是否已处理以及操作者，不能区分其他管理员执行的拒绝和忽略。插件按照统一规则，将“已处理但未观察到实际入群”的外部操作记录为 `reject`，并标记来源为 `external_inferred`。

受邀入群请求会保存为独立的 `invite` 类型申请，策略处理流水来源标记为 `invite_policy`。即使 `auto_set_card` 已开启，受邀成员也不会被实时补偿、定时补偿或 `/qgaudit backfill` 修改群名片。

插件启动、更新或重新启用后，会通过群系统消息同步补审停机期间遗漏且平台仍明确标记为“未处理”的申请。已经被任何管理员处理、已有插件审核流水、所属群当前停用或属于邀请类的记录不会补审；实时事件与补审使用同一申请锁，避免重复调用 LLM 或审批接口。每轮最多补审 10 条，每次实际调用 QQ 审批接口前随机等待 2 至 5 秒，单条异常不会中断其他申请。补审产生的流水来源标记为 `plugin_catch_up`。

## 自动群名片

`auto_set_card` 默认关闭。开启后，插件在自动批准申请后会主动确认成员是否已经入群，并在短时重试后继续通过定时任务核对。实时成员入群通知仍会正常处理，但即使 NapCat 偶尔没有发送 `group_increase`，名片设置也不会只依赖该通知。其他管理员手工通过的申请会在群系统消息同步后按同样流程处理。

NapCat 的申请验证消息可能是 `问题：毕业年份\n答案：2028`。插件会把完整文本保存在 `raw_comment` 供审计，同时把纯答案 `2028` 保存并用于审核和 `{answer}`，避免把问题标签写进群名片。

`card_template` 支持以下占位符：

| 占位符 | 内容 |
| --- | --- |
| `{qq}` | 成员 QQ 号。 |
| `{nickname}` | 成员当前 QQ 昵称，获取失败时使用 QQ 号。 |
| `{question}` | 申请时保存的问题快照。 |
| `{answer}` | 本次申请答案。 |
| `{join_date}` | 北京时间入群日期。 |
| `{join_time}` | 北京时间完整入群时间。 |

示例：

```text
{nickname}-{answer}
```

如果模板引用的申请答案不存在，例如成员由管理员直接邀请入群，插件会跳过修改并记录错误，不会生成残缺名片或清空现有名片。答案内容会移除换行和控制空白，最终名片最多保留 60 个字符。

### 补处理历史群名片

开启该群的 `auto_set_card` 后，配置中的管理员可以私聊 Bot 执行：

```text
/qgaudit backfill <群号>
```

命令不依赖 NapCat 的群成员列表或群成员缓存，而是按每个 QQ 最新的有效通过记录逐个调用群名片设置接口。明确拒绝、只有已结束成员会话或没有通过证据的申请不会处理；存在尚未结束的成员会话时优先使用该会话关联的申请。只有平台确认设置成功后才会补记成员会话，因此已退群成员的失败尝试不会被误记为仍在群内。

已经有成功名片操作记录的成员不会重复处理，命令可安全重复执行。NapCat 明确返回“成员不存在”时会单独计入“当前不在群内”，不会作为自动修改名片故障通知管理员；权限不足等其他错误计入“其他设置失败”。历史记录没有真实入群通知时，`{join_date}` 和 `{join_time}` 使用审批时间作为近似值；昵称优先使用申请快照，缺失时通过独立的 `get_stranger_info` 获取，仍然失败才回退到 QQ 号。

每次真正修改群名片前会随机等待 0.8 至 2.2 秒，批量补处理按成员串行执行。单个成员失败只会计入本次结果，不会中断后续成员；后台自动补偿对每次申请最多调用 5 次，次数持久化到数据库，重启插件也不会重新无限尝试。管理员以后主动再次执行 `backfill` 时，每个候选成员在该次命令中仍只尝试一次。

插件不会覆盖审计记录中已知的非空群名片，也不会重复修改已有成功操作记录的成员。由于当前 NapCat 版本的群成员信息和列表接口不可用，而 `set_group_card` 本身不会返回修改前名片，因此对于数据库中从未记录过名片的历史成员，插件无法在不调用故障接口的前提下可靠判断其当前是否已有名片；这类成员执行 `backfill` 前请先确认范围。升级 NapCat 前，插件不会主动调用这两个故障接口。

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

### DeepSeek JSON Output

当前聊天 provider 来自 DeepSeek 官方 API 时，插件会按照 [DeepSeek JSON Output 文档](https://api-docs.deepseek.com/guides/json_mode) 传入：

```json
{"response_format": {"type": "json_object"}, "max_tokens": 512}
```

审核提示词同时包含 `json` 关键字和目标格式示例。若模型仍返回空内容、无效 JSON 或错误字段结构，插件会追加格式纠正指令并重试一次。完整包裹返回内容的 `json` Markdown 代码块也可以解析；任意解释文字与 JSON 混合的响应仍会拒绝解析。

每次无法解析的原始返回都会以转义形式写入 warning 日志，最多保留前 500 个字符，便于定位空响应、代码块或额外说明文字，同时避免超长模型输出刷满日志。

## 不通过动作

当申请答案未通过审核时，插件按该群的 `failure_action` 处理：

- `ignore`：不调用平台审核接口，留给管理员手动处理。
- `reject`：自动拒绝，并使用该群配置的固定 `reject_reason`。

出于安全和可控性考虑，插件不会把 LLM 返回的 `reason` 发给申请人。`reason` 只用于管理员理解审核结果或排查问题。

## 空答案处理

普通申请中有问题文本但答案为空时不会调用 LLM，插件会直接按该群配置的 `failure_action` 处理，避免把无意义内容发送给模型。

## 受邀入群处理

`invite_action` 为每个群单独控制受邀入群请求：

- `approve`：直接通过，不调用 LLM，也不自动修改该成员的群名片。
- `ignore`：不调用平台审批接口，保留给管理员手动处理；这是默认值。
- `reject`：直接拒绝，不调用 LLM，拒绝消息使用 `reject_reason`。

三种动作分别复用 `notify_on_approve`、`notify_on_ignore`、`notify_on_reject` 决定是否私聊通知管理员。

NapCat 当前把“群成员邀请他人且需要管理员确认”的实时事件发送成普通 `sub_type=add`，不提供邀请者或请求类型字段，同时也不在 `get_group_system_msg` 中返回这类通知。插件因此仅在“原始申请附言完全为空”时将事件识别为受邀入群。`问题：年级\n答案：` 这类仍属于普通空答案；如果群允许主动申请时把整段验证信息留空，平台数据本身无法与受邀入群区分，也会按 `invite_action` 处理。

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
