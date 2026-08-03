# Changelog

## v0.1.2 - 2026-08-03

- DeepSeek provider 按官方 JSON Output 接口传入 `response_format={"type":"json_object"}`，并为审核结果预留 512 tokens。
- LLM 返回空内容、无效 JSON 或字段结构错误时自动重试一次。
- 兼容完整包裹 JSON 的 Markdown 代码块；异常响应以转义形式记录，最多保留前 500 个字符。

## v0.1.1 - 2026-07-03

- 修复管理员通知私聊会话默认使用 `aiocqhttp`，导致自定义平台名下通知发送失败的问题。

## v0.1.0 - 2026-06-30

- 初始版本：支持 QQ 加群申请 LLM 自动审核、按群配置、异常私聊通知和私聊测试命令。
