"""
Minimal LiveKit Agents -> Datadog example.

Two data paths land in Datadog:

1. stdout/stderr logs -- via LiveKit Cloud's log drain. Set the
   DATADOG_TOKEN secret on the deployed agent. Nothing in this file
   needs to change for that; logs flow regardless.

2. OpenTelemetry traces / APM -- via `set_tracer_provider`. The agent
   exports OTLP/HTTP to whatever `OTEL_EXPORTER_OTLP_ENDPOINT` points
   at -- typically a local OTel Collector running `datadogexporter`,
   or the Datadog Agent's OTLP receiver.

If `OTEL_EXPORTER_OTLP_ENDPOINT` is unset the tracer setup is skipped
entirely -- safe to deploy to LiveKit Cloud without a Collector wired
up yet.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli, inference
from livekit.agents.telemetry import set_tracer_provider

load_dotenv(".env.local")
logger = logging.getLogger("dd-otel")


def setup_datadog_tracing():
    """Wire LiveKit's OTel spans into an OTLP/HTTP exporter.

    Returns the TracerProvider (so the caller can flush on shutdown),
    or None if no endpoint is configured.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set, skipping trace export")
        return None

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({
        SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "livekit-otel-demo"),
        "deployment.environment": os.environ.get("DD_ENV", "dev"),
        "service.version": os.environ.get("DD_VERSION", "0.1.0"),
    })
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    set_tracer_provider(provider)
    logger.info("OTel traces -> %s", endpoint)
    return provider


class MyAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a friendly voice assistant for a Datadog telemetry demo. "
                "Greet the caller, answer briefly, and don't be afraid to be short."
            ),
            llm=inference.LLM("openai/gpt-4.1-mini"),
            stt=inference.STT("deepgram/nova-3"),
            tts=inference.TTS("inworld/inworld-tts-2"),
        )

    async def on_enter(self) -> None:
        self.session.generate_reply()


server = AgentServer()


@server.rtc_session(agent_name="livekit-otel-demo")
async def entrypoint(ctx: JobContext) -> None:
    provider = setup_datadog_tracing()
    if provider is not None:
        ctx.add_shutdown_callback(provider.force_flush)

    logger.info("session starting room=%s", ctx.room.name)
    session = AgentSession()
    await session.start(agent=MyAgent(), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(server)
