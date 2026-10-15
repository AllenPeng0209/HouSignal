# 楼管家 Pi Agent Runtime

This is a native scheduling sidecar using the actual MIT-licensed
[`@earendil-works/pi-agent-core`](https://github.com/earendil-works/pi/tree/main/packages/agent)
and [`@earendil-works/pi-ai`](https://github.com/earendil-works/pi/tree/main/packages/ai)
packages, pinned to **1.2.0**. It does not reimplement the Pi loop. Python remains
responsible for project membership, authorization, PII policy, tool allowlists,
transactions, production-job dependencies, auditing and final acceptance.

## Run and verify

Node **22.19.0 or newer** is required.

```sh
cd apps/agent-runtime
npm ci
npm test
node bridge.mjs
```

The API/worker launches `node bridge.mjs` as a subprocess. The model credential is
provided only through the private child environment variable
`HOUSIGNAL_PI_API_KEY`; do not put it in arguments, configuration, tool results,
logs or persisted sessions. The Python gateway must validate the provider URL
against its configured model endpoint. The sidecar deliberately supports local
HTTP endpoints for the offline HTTP-stream integration tests.

Tests use the real Pi Agent, plus a controlled model stream or a local
OpenAI-compatible SSE server. They do not call a paid model or external platform.

## NDJSON protocol

Each command and event is one UTF-8 JSON line. Stdout contains protocol messages;
controlled failures do not print SDK stacks or payloads to stderr. One process
handles one run and exits after `done` (exit 0) or a terminal `error` (exit 1).

The first event is:

```json
{"type":"ready","engine":"pi-agent-core","version":"1.2.0"}
```

Start with either top-level configuration fields or a nested `config`:

```json
{"type":"start","config":{"systemPrompt":"Current authorized scope","model":{"id":"qwen-plus","baseUrl":"https://dashscope.aliyuncs.com/compatible-mode/v1","contextWindow":32768,"maxTokens":4096},"messages":[{"role":"user","content":"Current sanitized user request"}],"tools":[{"type":"function","function":{"name":"crm_update","description":"Update verified client details","parameters":{"type":"object","properties":{"note":{"type":"string"}},"required":["note"]}}}],"maxTurns":null,"maxToolCalls":null,"maxOutputTokens":null,"maxTotalTokens":null}}
```

`messages` accepts OpenAI user/assistant/tool messages, including historical
`tool_calls`, or normalized Pi user/assistant/toolResult messages. If
`state.messages` is supplied, those messages are history and `messages` contains
only new input. Restoring state reinstalls the current authorized system prompt
and current tools. Existing system messages, thinking blocks and raw provider
error details are excluded from restored business history. The in-process `engine.run()` API returns restorable `state`; it is never
written to disk by this process. The executable NDJSON bridge excludes `state`
from its terminal `done` event: Python already owns the business transcript and
does not consume that snapshot. Growing run history therefore cannot inflate
the terminal protocol frame past the gateway's existing 2 MiB frame boundary;
that protocol-format boundary remains in place.

The current input normally ends in a user or tool result. A first command with
`"type":"continue"` (or `start` with `"continue":true`) calls Pi `continue()`
against the transcript. Pi requires a non-assistant tail or queued input when
continuing; an active process accepts `steer` or `follow_up` instead of a second
`continue`.

When business tools are registered, the first model request requires a tool call;
later requests use automatic tool selection. A provider that still returns an
ungrounded completion receives one bounded system correction through Pi
`finishTurn` and its queue. The correction carries no human control ID and
grants no additional write authority.

Python may supply `requiredToolGroups`, an array of groups of alternative
operation names. Every group must have a real successful native write receipt.
Only a tool result with `_execution: {applied: true, name: "exact_tool_name"}`
from Python satisfies a group; reads, tool invocations and rejected operations
do not. Missing groups trigger a Pi system correction and continued required
tool selection until completion or cancellation, subject to any explicitly configured run limits. `done.incompleteRequiredGroups`
contains any unmet groups for independent server-side validation. A model
claiming a save without its receipt cannot satisfy the requirement.

The real Pi loop emits lifecycle events and text deltas:

```json
{"type":"event","event":"turn_start","turns":1}
{"type":"text_delta","delta":"Progress text"}
{"type":"tool_call","id":"call-id","name":"crm_update","arguments":{"note":"Sanitized note"}}
```

Python executes the permitted business operation inside its transaction, then
responds with the same call id:

```json
{"type":"tool_result","id":"call-id","result":{"updated":true}}
```

A tool failure uses `"error":"safe error"` instead of `result`; the model
receives a generic business-tool failure. The sidecar waits at most 120 seconds
for each tool result. Global `toolExecution` and every tool's `executionMode`
are `sequential`; the sidecar never executes concurrent database writes.

Controls accepted while running:

```json
{"type":"steer","id":"human-control-id","text":"Sanitized correction"}
{"type":"follow_up","id":"next-human-control-id","text":"Sanitized next request"}
{"type":"clear_steering_queue"}
{"type":"clear_follow_up_queue"}
{"type":"clear_all_queues"}
{"type":"queue_status"}
{"type":"abort"}
```

Control acknowledgments contain only their action and acceptance. Queue status
contains only `hasQueuedMessages`, `steering` and `followUp` counts. Enqueuing a
control does **not** grant it business-write authority. When Pi actually consumes
a queued user message, an awaited event identifies its human control:

```json
{"type":"input_consumed","id":"human-control-id","kind":"steer"}
```

Controls can additionally carry `requiredToolGroups` and a new OpenAI `tools`
loadout. They are applied only when that exact queued user message is consumed,
at which point old satisfied-operation names are reset. Pi `prepareRequest`
synchronizes its active tool context and transcript declarations with the new
loadout.

Python must switch the current tool-authorization source only on that event.
A cleared control never emits `input_consumed`. No control text is echoed in the
event. The `id` is optional for low-level callers, but the production Python
gateway must provide it for authorization tracking.

Pi 1.2 processes a tool batch before polling steering. This bridge additionally
uses Pi `beforeToolCall` to skip unstarted tools in that batch when a newer
steering message is waiting. The next Pi turn consumes the correction.
Follow-ups run only after the current request naturally stops.

A terminal result contains:

```json
{"type":"done","text":"Final answer","model":"qwen-plus","turns":2,"toolCalls":1,"aborted":false,"limitReached":null,"usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"totalTokens":0,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}}}
```

Usage is aggregated from actual assistant messages in this run. Custom-model
cost rates are not configured here, so the zero-valued cost fields are not a
billing estimate. By default there is **no application run limit** on model turns,
tool executions or accumulated tokens. `maxTurns`, `maxToolCalls` and
`maxTotalTokens` each accept an omitted field, `null` or `0` for unlimited;
a positive safe integer enables that optional limit at its exact value. There
is no hidden maximum or fallback cap. Negative, fractional, string, boolean or
unsafe integer values are rejected as `INVALID_CONFIG`.

`maxOutputTokens` omitted, `null` or `0` uses the configured model's full
`maxTokens` capability for each provider call, rather than an application-level
1,200-token cap. An explicit positive value is respected up to that model
capability. The provider's actual context and per-response capabilities still
apply. Unlimited scheduling does not disable abort, authorization, required
operation receipts or tool/schema validation.

For an explicitly limited run, `limitReached` can be `max_turns`,
`max_tool_calls` or `max_total_tokens`. A natural final answer on the last
allowed turn succeeds; a further required request is stopped. Python decides
whether to accept or roll back an incomplete operation. An explicit accumulated
token limit is enforced at request/tool boundaries, so one in-flight response
can consume its remaining budget.

The OpenAI-compatible Qwen adapter disables `store`, developer-role conversion,
strict tool mode and unsupported `reasoning_effort`; it uses `max_tokens`.
Model/network errors become a generic `MODEL_REQUEST_FAILED`. The sidecar
redacts the exact private API credential even if a provider splits it across
text chunks. Business-data redaction remains the Python application's job.

## Upstream attribution

Pi is authored by Mario Zechner and contributors and distributed under the MIT
license. Both packages are consumed as dependencies with their original license
metadata and exact npm lockfile resolution. See the official
[repository license](https://github.com/earendil-works/pi/blob/main/LICENSE).
No upstream command-line filesystem, terminal, or unrestricted shell tools are
registered by this bridge.
