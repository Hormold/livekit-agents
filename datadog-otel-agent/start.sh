#!/bin/bash
set -euo pipefail

# Start the OTel Collector in the background. It listens on 127.0.0.1:4318
# and forwards traces to Datadog APM via the datadogexporter (config below).
# Requires DD_API_KEY and DD_SITE env vars at runtime.
/usr/local/bin/otelcol-contrib --config=/app/otel-collector.yaml &
COLLECTOR_PID=$!

# Give the collector a brief moment to bind its OTLP receiver before the
# agent's first BatchSpanProcessor flush attempts a POST.
sleep 1

# Forward shutdown signals to the collector when the agent terminates.
trap 'kill -TERM ${COLLECTOR_PID} 2>/dev/null || true' INT TERM EXIT

# Run the agent in the foreground (PID 1 after exec).
exec uv run agent.py start
