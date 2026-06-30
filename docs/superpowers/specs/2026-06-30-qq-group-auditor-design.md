# QQ Group Auditor Plugin Design

Date: 2026-06-30

## Summary

Build a new AstrBot plugin named `astrbot_plugin_qq_group_auditor`.

The first version only handles QQ group join requests. For configured groups, the plugin reads the applicant answer, asks AstrBot's currently configured LLM provider to make a strict JSON decision, and then approves, rejects, or leaves the request untouched according to the group configuration.

This version is intentionally small. It does not handle friend requests, group invitations, welcome messages, blacklists, audit history queries, or general group moderation.

## Goals

- Let the owner configure QQ groups that should use automatic join review.
- Use AstrBot's existing LLM/provider configuration instead of storing a separate model API key.
- Keep each group isolated: prompt, fallback action, rejection reason, notification recipients, and normal-result notification switches are configured per group.
- Be conservative: only an explicit, valid LLM JSON response with `approve: true` can approve a request.
- Provide a private admin test command for tuning a group's review prompt without actually approving or rejecting anyone.

## Non-Goals

- No independent OpenAI-compatible API configuration in this version.
- No group-chat commands.
- No persistent audit record database or recent-review query.
- No automatic handling for friend requests, group invitations, or post-join welcome flows.
- No rule engine beyond one LLM prompt per group.

## Platform Assumptions

- The plugin targets QQ through OneBot-compatible adapters such as NapCat.
- AstrBot receives a group join request event with enough data to identify:
  - QQ group ID.
  - Applicant QQ user ID.
  - Applicant answer or request comment.
  - A request `flag` or equivalent token needed by the platform approve/reject API.
- The bot account must be an administrator in the target QQ group. If it is not, the platform review API may fail; that failure is reported to configured admins by QQ private message.

Because AstrBot's ordinary plugin documentation focuses more on message handlers than request events, the implementation should isolate adapter-specific request parsing and approve/reject calls behind a small platform adapter module. This keeps the rest of the plugin testable and lets implementation adjust to AstrBot/NapCat event object details without changing review logic.

## WebUI Configuration

Configuration uses AstrBot-supported schema types only. The group list should be represented as `template_list`, not a raw `dict`.

Each group review item contains:

- `enabled`: whether automatic review is enabled for this group.
- `group_id`: QQ group number as a string.
- `review_prompt`: the group-specific instruction describing what answers should pass.
- `failure_action`: `ignore` or `reject`.
- `reject_reason`: fixed text sent to the applicant when `failure_action` is `reject`.
- `admin_qq_ids`: QQ user IDs that can receive private notifications and run the test command for this group.
- `notify_on_approve`: whether normal approvals should be privately reported to admins.
- `notify_on_reject`: whether normal rejections should be privately reported to admins.
- `notify_on_ignore`: whether normal ignored requests should be privately reported to admins.

Defaults:

- `enabled`: `true`.
- `failure_action`: `ignore`.
- `reject_reason`: `加群答案不符合要求，请重新申请并按提示填写。`
- `notify_on_approve`: `false`.
- `notify_on_reject`: `false`.
- `notify_on_ignore`: `false`.

## Review Flow

1. Receive a QQ group join request event.
2. Extract group ID, applicant QQ ID, answer text, and platform request token.
3. If the group is not configured or its configuration is disabled, silently ignore the event.
4. If the answer text is empty or only whitespace:
   - Do not call the LLM.
   - Treat the request as not approved.
   - Apply the configured failure action.
5. If the answer text is non-empty:
   - Build an LLM prompt from the group's `review_prompt`, the group ID, the applicant QQ ID, the answer text, and strict JSON output instructions.
   - Call AstrBot's currently configured LLM/provider.
   - Parse the response as JSON.
6. Approve only when the parsed JSON has a boolean `approve` field set to `true`.
7. If `approve` is `false`, apply the configured failure action.
8. If the LLM call fails, times out, returns invalid JSON, or returns a malformed JSON shape, leave the request untouched and privately notify the configured admins.

## LLM Contract

The plugin asks the LLM to return only JSON with this shape:

```json
{"approve": true, "reason": "符合条件"}
```

Required fields:

- `approve`: boolean.
- `reason`: short string explaining the decision.

Parsing rules:

- `approve: true` means the request may be approved.
- `approve: false` means the request does not meet the configured condition.
- Missing fields, non-boolean `approve`, non-string `reason`, extra prose, Markdown code fences that cannot be safely parsed, or invalid JSON are treated as an LLM output error.
- LLM output errors never approve or reject automatically. They leave the request untouched and notify admins.

The LLM receives only:

- Group ID.
- Applicant QQ ID.
- Applicant answer text.
- The configured group review prompt.

The raw platform event is not sent to the LLM.

## Actions

### Approve

When the LLM decision is valid and `approve` is `true`, the plugin calls the platform approve API for the request token.

If the platform call fails, the plugin privately notifies configured admins.

### Reject

When a request is not approved and `failure_action` is `reject`, the plugin calls the platform reject API and sends the configured fixed `reject_reason`.

The plugin never sends the LLM `reason` to the applicant.

If the platform call fails, the plugin privately notifies configured admins.

### Ignore

When a request is not approved and `failure_action` is `ignore`, the plugin does not call the platform review API. The request remains available for other administrators to handle.

## Notifications

Notifications are QQ private messages to the `admin_qq_ids` configured for the affected group.

Exception notifications are always sent for:

- LLM call failure or timeout.
- Invalid or malformed LLM response.
- Failure to extract necessary event fields.
- Platform approve/reject API failure.

Normal result notifications are per group and opt-in:

- `notify_on_approve`: send private notice after a successful approval.
- `notify_on_reject`: send private notice after a successful rejection.
- `notify_on_ignore`: send private notice after a normal ignore action.

Normal notices include:

- Group ID.
- Applicant QQ ID.
- Short answer summary.
- Action taken.
- LLM reason when available.

Notification send failures should be logged, but they must not change the review action result.

## Private Test Command

The first version provides one QQ private-chat command:

```text
/qgaudit test <group_id> <answer text>
```

Behavior:

- The command is ignored in group chats.
- The sender must be listed in `admin_qq_ids` for the specified group.
- The command does not approve, reject, or touch any real join request.
- It runs the same non-empty-answer LLM review path used by real events.
- It replies privately with the parsed `approve` and `reason`, or with a clear error if the LLM result is invalid.

For permission failures, the command should reply with a short no-permission message in private chat to make configuration mistakes easy to diagnose.

## Error Handling Principles

- Fail closed for approval: no valid `approve: true`, no automatic approval.
- Do not reject on infrastructure errors. LLM/provider failures and malformed LLM output leave the request untouched.
- Empty applicant answers are not infrastructure errors. They are treated as not approved and follow `failure_action`.
- Missing group configuration or disabled group configuration is a normal no-op.
- Platform API failures and field extraction failures are reported to admins.
- All exceptional paths should be logged with enough context to debug, without logging secrets.

## Architecture

Recommended modules:

- `main.py`: AstrBot plugin entry point, lifecycle, event handlers, and command handler wiring.
- `qq_group_auditor/config.py`: schema normalization and group config lookup.
- `qq_group_auditor/models.py`: dataclasses or typed models for group config, join request, review result, and action result.
- `qq_group_auditor/reviewer.py`: builds prompts, calls AstrBot provider, parses strict JSON, and returns review decisions.
- `qq_group_auditor/platform.py`: extracts join request fields and calls OneBot/NapCat approve/reject APIs.
- `qq_group_auditor/notifier.py`: formats and sends private admin notices.
- `qq_group_auditor/text.py`: answer summarization and small formatting helpers.

Boundaries:

- Review logic must not depend directly on raw AstrBot/NapCat event shapes.
- Platform adapter must not know prompt details.
- Notification formatting must not decide approval behavior.
- Config normalization should absorb WebUI schema quirks so the rest of the code works with typed values.

## Testing Strategy

Use unit tests with mocked provider, mocked platform adapter, and mocked notifier. No test should require real QQ, NapCat, AstrBot runtime network calls, or a live LLM.

Coverage should include:

- Config parsing and defaults.
- Group lookup by group ID.
- Disabled and unconfigured group no-op behavior.
- Empty-answer branch for both `ignore` and `reject`.
- Valid LLM approval.
- Valid LLM rejection with `ignore`.
- Valid LLM rejection with `reject`.
- Invalid JSON response.
- Malformed JSON shape.
- Provider exception or timeout.
- Platform approve/reject failure.
- Notification switch behavior for approve/reject/ignore.
- Exception notification behavior.
- Private test command permission checks.
- Private test command group-chat ignore behavior.

## Documentation

README should document:

- Supported platform expectation: QQ through OneBot/NapCat.
- Requirement that the bot account be a group administrator.
- WebUI configuration fields and examples.
- Strict LLM JSON contract.
- Private test command usage.
- Safety behavior for errors and empty answers.

CHANGELOG should exist from the first release to avoid AstrBot warning about missing changelog.

## Implementation Notes

- During implementation, inspect the installed AstrBot/NapCat event object and platform API access path to select the exact hook and API call names.
- If AstrBot exposes multiple provider invocation helpers, prefer the official high-level provider API used by current plugin conventions.
- Keep the first release versioned as `v0.1.0`.
