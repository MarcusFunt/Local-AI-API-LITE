# API contract

The only generation endpoint in V1 is vLLM's non-streaming OpenAI-compatible `POST /v1/chat/completions`. The service is reachable only by clients attached to the private `ddu_inference_bus` network at `http://ddu-llm:8080`.

## Readiness and model identity

- `GET /health`: HTTP 200 means the server responds. Clients must not require a JSON body or a llama.cpp-specific `{"status":"ok"}` value.
- `GET /v1/models`: must list `ddu-narrator`, the served API compatibility alias.
- Every completion uses `model: "ddu-narrator"`, `stream: false`, and a bounded timeout.
- A successful completion must return `finish_reason: "stop"`; reject truncation (`"length"`), empty content, malformed JSON, wrong model identity, schema violations, missing fields, and unexpected fields.
- A 429 or 503 may be retried a bounded number of times with backoff. Other HTTP errors fail immediately. Clients must never hang indefinitely.

## Structured workloads

Use standard `response_format: {"type":"json_schema", ...}`. Applications validate the response independently; server-side constrained decoding does not replace consumer validation.

The history diagnostic uses a synthetic Danish diplomat prompt and this exact object shape:

```json
{"language":"da","reply":"..."}
```

The independent fictional-examiner diagnostic uses a separate prompt and schema:

```json
{"speaker":"examiner","reply":"..."}
```

Both schemas require a non-empty reply up to 600 characters and forbid additional properties. The examiner scenario contains no real patient information and requests no medical diagnosis.

For the pinned Qwen3.5 checkpoint, the model-specific `chat_template_kwargs: {"enable_thinking": false}` option is required for these short completion budgets. With reasoning enabled, the model used the full 180-token budget for reasoning, returned no content and ended with `finish_reason: "length"`; consumers must reject that response. Do not generalize this template option to unrelated models.

## Privacy and logging

Record only case name, API model alias, success/failure category, elapsed time, request count, and exception class. Do not log prompts, completion bodies, capabilities, credentials, or full HTTP error bodies. The vLLM endpoint is unauthenticated by design and therefore must remain on a trusted private Docker network with no host-published port.
