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


INSTRUCTIONS = """You are a friendly SMS assistant. You have full conversation history with this user — use it to give contextual answers.

CRITICAL: You cannot reply directly. You MUST use send_sms tool to send any response.

Tools:
- get_weather(city): Get weather info
- send_sms(message): Send reply — USE THIS FOR ALL REAL MESSAGES
- skip_response(reason): RARELY use, only for obvious spam

ALWAYS RESPOND with send_sms to:
- Any message from a real person
- Short messages like "nice", "ok", "thanks" — these are real people!
- Questions about previous conversation — you CAN see the history, reference it!

ONLY skip_response for:
- "STOP", "UNSUBSCRIBE" — opt-out requests
- Obvious automated messages like "Your code is 123456"

When in doubt — RESPOND. Real humans deserve a reply.

Style: friendly, casual, short (under 160 chars). No markdown."""


class SMSAgent(AgentTask[SMSResult]):
    def __init__(self, chat_ctx: ChatContext | None = None) -> None:
        super().__init__(instructions=INSTRUCTIONS, chat_ctx=chat_ctx)

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

    # Load conversation history
    saved_ctx = context_manager.get(from_number)
    if saved_ctx:
        logger.info(f"Restored {len(saved_ctx.items)} history items")
    else:
        logger.info("No history, starting fresh")

    async with AgentSession[SMSContext](
        llm="openai/gpt-4o-mini",
        userdata=sms_context,
        max_tool_steps=10,
    ) as session:
        await session.start(SMSAgent(chat_ctx=saved_ctx))

        sms_result: SMSResult | None = None
        try:
            result: RunResult[SMSResult] = await session.run(user_input=body, output_type=SMSResult)
            sms_result = result.final_output
        except RuntimeError as e:
            logger.warning(f"Agent didn't call tool: {e}")

        # Merge old history + new items and save
        old_items = saved_ctx.items if saved_ctx else []
        new_items = session.history.items
        all_items = list(old_items) + list(new_items)
        context_manager.save(from_number, ChatContext(all_items))

        # Log stats
        user_msgs = sum(1 for i in all_items if i.type == "message" and i.role == "user")
        tool_calls = sum(1 for i in all_items if i.type == "function_call")
        logger.info(f"Context: {len(all_items)} items | {user_msgs} user msgs | {tool_calls} tool calls")

        if sms_result:
            return sms_result

        # Fallback: if agent generated text but forgot to call send_sms, send it anyway
        for item in reversed(session.history.items):
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
