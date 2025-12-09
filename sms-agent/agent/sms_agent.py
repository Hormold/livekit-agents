from __future__ import annotations

import logging
from dataclasses import dataclass

from livekit.agents import AgentSession, AgentTask, RunContext, RunResult, function_tool
from livekit.agents.llm import ChatContext

from .context_manager import ContextManager
from .http_tools import get_weather_by_city
from .twilio_utils import TwilioConfig, send_sms

logger = logging.getLogger(__name__)


@dataclass
class SMSResult:
    action: str
    message: str | None = None
    reason: str | None = None


@dataclass
class SMSContext:
    phone_number: str
    incoming_message: str
    context_manager: ContextManager
    twilio_config: TwilioConfig


INSTRUCTIONS = """You are a friendly SMS assistant.

CRITICAL: You cannot reply directly. You MUST use send_sms tool to send any response. Never just write text — always call send_sms with your message.

Tools:
- get_weather(city): Get weather info
- send_sms(message): Send reply to user — ALWAYS use this to respond
- skip_response(reason): Skip automated/spam messages only

When to skip (use skip_response):
- Carrier/voicemail notifications
- Marketing spam, "STOP" requests
- Auto-replies from systems

Style: friendly, casual, short (under 160 chars). No markdown.

REMEMBER: To reply, you must call send_sms(). Do not just write text."""


class SMSAgent(AgentTask[SMSResult]):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)

    @function_tool()
    async def get_weather(self, context: RunContext[SMSContext], city: str) -> str:
        """Get current weather for a city."""
        logger.info(f"Getting weather for: {city}")
        result = await get_weather_by_city(city)
        logger.info(f"Weather result: {result}")
        return result

    @function_tool()
    async def send_sms(self, context: RunContext[SMSContext], message: str) -> None:
        """Send an SMS reply to the user."""
        ctx: SMSContext = context.session.userdata
        logger.info(f"Sending SMS to {ctx.phone_number}: {message}")

        result = await send_sms(ctx.twilio_config, ctx.phone_number, message)

        if result.success:
            logger.info(f"SMS sent, SID: {result.message_sid}")
            self.complete(SMSResult(action="sent", message=message))
        else:
            logger.error(f"SMS failed: {result.error}")
            self.complete(SMSResult(action="error", reason=result.error))

    @function_tool()
    async def skip_response(self, context: RunContext[SMSContext], reason: str) -> None:
        """Skip responding to this message."""
        logger.info(f"Skipping: {reason}")
        self.complete(SMSResult(action="skipped", reason=reason))


async def process_sms(
    from_number: str,
    to_number: str,
    body: str,
    context_manager: ContextManager,
    twilio_config: TwilioConfig,
) -> SMSResult:
    """Process an incoming SMS and generate a response."""
    logger.info(f"Incoming SMS from {from_number}: {body}")

    reply_config = TwilioConfig(
        account_sid=twilio_config.account_sid,
        auth_token=twilio_config.auth_token,
        from_number=to_number or twilio_config.from_number,
    )

    sms_context = SMSContext(
        phone_number=from_number,
        incoming_message=body,
        context_manager=context_manager,
        twilio_config=reply_config,
    )

    async with AgentSession[SMSContext](
        llm="openai/gpt-4o-mini",
        userdata=sms_context,
        max_tool_steps=10,
    ) as session:
        await session.start(SMSAgent())

        # Restore conversation history if available
        saved = context_manager.get_chat_ctx_dict(from_number)
        if saved:
            session.history.merge(ChatContext.from_dict(saved))

        sms_result: SMSResult | None = None
        try:
            result: RunResult[SMSResult] = await session.run(user_input=body, output_type=SMSResult)
            sms_result = result.final_output
        except RuntimeError as e:
            logger.warning(f"Agent didn't call tool: {e}")

        # Save conversation context (excluding handoff items)
        items = [i for i in session.history.items if i.type != "agent_handoff"]
        context_manager.save_chat_ctx(
            from_number,
            ChatContext(items).to_dict(exclude_function_call=False),
        )

        # Log stats
        user_msgs = sum(1 for i in items if i.type == "message" and i.role == "user")
        tool_calls = sum(1 for i in items if i.type == "function_call")
        logger.info(f"Context: {len(items)} items | {user_msgs} user msgs | {tool_calls} tool calls")

        if sms_result:
            return sms_result

        # Fallback: if agent generated text but forgot to call send_sms, send it anyway
        for item in reversed(items):
            if item.type == "message" and item.role == "assistant":
                text = item.content[0] if item.content else None
                if text and isinstance(text, str):
                    logger.info(f"Fallback: sending assistant message as SMS: {text}")
                    send_result = await send_sms(reply_config, from_number, text)
                    if send_result.success:
                        return SMSResult(action="sent", message=text)
                    logger.error(f"Fallback failed: {send_result.error}")
                    return SMSResult(action="error", reason=send_result.error)
                break

        logger.error("No output from agent")
        return SMSResult(action="error", reason="No output from agent")
