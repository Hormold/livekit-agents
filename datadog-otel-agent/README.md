# LiveKit Agents → Datadog (logs + APM traces)

A minimal LiveKit voice agent that ships **two streams of telemetry** to Datadog from a single container deployed to LiveKit Cloud:

1. **stdout/stderr logs** → Datadog Logs, via LiveKit Cloud's built-in [log drain](https://docs.livekit.io/deploy/agents/cloud/logs#datadog) (just set `DATADOG_TOKEN` as an agent secret — zero code).
2. **OpenTelemetry traces** → Datadog APM, via an [OTel Collector](https://docs.datadoghq.com/opentelemetry/collector_exporter/) baked into the same container. The agent exports OTLP/HTTP to `localhost:4318`; the Collector forwards to Datadog with the `datadogexporter`.

```
┌──────────────────── Container (LiveKit Cloud pod) ────────────────────┐
│                                                                       │
│   livekit agent ──OTLP/HTTP──▶ otelcol-contrib ──datadogexporter──▶ Datadog APM
│   (Python, :*)                  (:4318)
│         │
│         └─ stdout/stderr ────────────────────────────────▶ Datadog Logs
│                                                            (via LK Cloud log drain)
└───────────────────────────────────────────────────────────────────────┘
```

LiveKit Cloud has **no built-in OTel collector** for traces — the SDK emits the same spans LiveKit Cloud uses for Agent Insights, but it's on you to point a `TracerProvider` somewhere. This example puts the Collector in the same container so there's no external infra to operate.

## What you'll see in Datadog

**APM → Services → `livekit-otel-demo` → Traces:**

```
agent_turn   (root)
├── llm_node           openai/gpt-4.1-mini       ttft 0.25s
├── tts_node           inworld/inworld-tts-2     ttfb 0.51s
│   └── tts_request_run                          retry_count=0
└── agent_speaking
```

Each span carries `lk.chat_ctx`, `lk.response.text`, `gen_ai.request.model`, `room_id`, `job_id`, plus standard `env` / `version` / `host` tags.

**Logs → service:cloud.livekit.io** — your agent's stdout, including any `logger.info(...)` you write. The agent ID lands in `host`.

## Deploy

```bash
# 1. Configure secrets locally
cp .env.example .env.local
# Fill in DD_API_KEY, DD_SITE, DATADOG_TOKEN
# (DD_API_KEY/DD_SITE → consumed by the Collector for trace export)
# (DATADOG_TOKEN     → consumed by LK Cloud for the log drain)

# 2. Deploy
lk a create               # first time — registers the agent + builds + deploys
lk agent update-secrets --secrets-file .env.local

# 3. Make a call (dispatch agent_name = "livekit-otel-demo") and check
#    Datadog APM. Traces show up under service `livekit-otel-demo` within ~30s.
```

## Local development

```bash
# Run the collector locally so you can hit `python agent.py dev` against it.
docker compose up -d
uv run python agent.py dev
```

`docker-compose.yaml` boots the same `otelcol-contrib` config used in production. Traces flow to the same Datadog account as the cloud deploy.

## Files

| File | Purpose |
|---|---|
| `agent.py` | Voice agent. `setup_datadog_tracing()` wires `set_tracer_provider`. Skips if `OTEL_EXPORTER_OTLP_ENDPOINT` is unset. |
| `otel-collector.yaml` | `otelcol-contrib` config: OTLP HTTP receiver → `datadog` exporter. |
| `Dockerfile` | Multi-stage build. Copies `otelcol-contrib` binary from `otel/opentelemetry-collector-contrib`, sets `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318`, runs `start.sh`. |
| `start.sh` | Starts the Collector in the background, then `exec uv run agent.py start` as PID 1. |
| `docker-compose.yaml` | Local dev only — runs the Collector on the host so `python agent.py dev` works. |
| `pyproject.toml` / `uv.lock` | Pinned deps: `livekit-agents`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`. |

## Notes

- **Logs vs traces are independent paths.** The log drain is a LiveKit Cloud feature that needs no code; traces require the OpenTelemetry plumbing in this repo. To correlate the two in Datadog, also emit `dd.trace_id` / `dd.span_id` on each log line (not shown here).
- **Datadog direct OTLP intake** (no Collector at all) exists but is in [Preview](https://docs.datadoghq.com/opentelemetry/setup/otlp_ingest/traces/) and needs CSM allowlisting. Once GA, drop the Collector entirely and POST OTLP from the SDK straight to Datadog.
- **Python only** as of `livekit-agents` 1.5.x — the Node.js SDK doesn't yet expose `set_tracer_provider`.
