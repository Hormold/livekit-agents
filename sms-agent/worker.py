"""
LiveKit SMS Worker - Processes SMS messages dispatched from livekit-server.py

This worker:
1. Receives SMS processing jobs via LiveKit dispatch
2. Parses metadata containing phone number, message, context, and callback URL
3. Runs SMSAgent to generate and send response via Twilio
4. POSTs updated context back to the server
5. Shuts down

Run with: python worker.py dev  (or use LiveKit Cloud deployment)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from livekit.agents import JobContext, WorkerOptions, cli
from livekit.agents import AgentSession, RunResult
from livekit.agents.llm import ChatContext

local_env = Path(__file__).parent / ".env"
root_env = Path(__file__).parent.parent / ".env"
load_dotenv(local_env if local_env.exists() else root_env)

from agent import SMSAgent, SMSResult, TwilioConfig
from agent.http_tools import get_weather_by_city
from agent.twilio_utils import send_sms

# Quiet noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("livekit").setLevel(logging.WARNING)
logging.getLogger("livekit.agents").setLevel(logging.INFO)
logging.getLogger("asyncio").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@dataclass
class WorkerSMSContext:
    """Context for SMS processing in worker mode."""
    phone_number: str
    incoming_message: str
    twilio_config: TwilioConfig
    callback_url: str


async def post_context_update(
    callback_url: str,
    phone_number: str,
    chat_context: dict,
    result: dict,
) -> bool:
    """POST updated context back to the webhook server."""
    if not callback_url:
        logger.warning("No callback URL provided, skipping context update")
        return False
    
    payload = {
        "phone_number": phone_number,
        "chat_context": chat_context,
        "result": result,
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                callback_url,
                json=payload,
                timeout=30,
            ) as resp:
                if resp.status in (200, 201):
                    logger.info(f"Context update posted successfully to {callback_url}")
                    return True
                error = await resp.text()
                logger.error(f"Failed to post context update: HTTP {resp.status}: {error}")
                return False
    except Exception as e:
        logger.exception(f"Error posting context update: {e}")
        return False


async def entrypoint(ctx: JobContext) -> None:
    """Worker entrypoint for processing SMS via LiveKit dispatch."""
    
    # Parse metadata from dispatch request
    try:
        metadata = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
    except json.JSONDecodeError:
        logger.error(f"Invalid metadata JSON: {ctx.job.metadata}")
        return
    
    phone_number = metadata.get("phone_number", "")
    incoming_message = metadata.get("incoming_message", "")
    callback_url = metadata.get("callback_url", "")
    twilio_config_data = metadata.get("twilio_config", {})
    chat_context_data = metadata.get("chat_context")
    
    if not phone_number or not incoming_message:
        logger.error("Missing phone_number or incoming_message in metadata")
        return
    
    logger.info(f"Processing SMS from {phone_number}: {incoming_message}")
    
    # Build Twilio config from metadata
    twilio_config = TwilioConfig(
        account_sid=twilio_config_data.get("account_sid", ""),
        auth_token=twilio_config_data.get("auth_token", ""),
        from_number=twilio_config_data.get("from_number", ""),
    )
    
    if not twilio_config.is_configured():
        logger.error("Twilio not configured in metadata")
        return
    
    # Restore chat context from metadata
    saved_ctx = None
    if chat_context_data:
        try:
            saved_ctx = ChatContext.from_dict(chat_context_data)
            logger.info(f"Restored {len(saved_ctx.items)} history items")
        except Exception as e:
            logger.warning(f"Failed to restore chat context: {e}")
    
    # Create worker context (doesn't need ContextManager since we callback)
    worker_context = WorkerSMSContext(
        phone_number=phone_number,
        incoming_message=incoming_message,
        twilio_config=twilio_config,
        callback_url=callback_url,
    )
    
    # Run the SMS agent
    sms_result: SMSResult | None = None
    all_items = []
    
    async with AgentSession[WorkerSMSContext](
        llm="openai/gpt-4o-mini",
        userdata=worker_context,
        max_tool_steps=10,
    ) as session:
        await session.start(SMSAgent(chat_ctx=saved_ctx))
        
        try:
            result: RunResult[SMSResult] = await session.run(
                user_input=incoming_message,
                output_type=SMSResult,
            )
            sms_result = result.final_output
        except RuntimeError as e:
            logger.warning(f"Agent didn't call tool: {e}")
        
        # Merge old history + new items
        old_items = saved_ctx.items if saved_ctx else []
        new_items = session.history.items
        all_items = list(old_items) + list(new_items)
        
        # Log stats
        user_msgs = sum(1 for i in all_items if i.type == "message" and i.role == "user")
        tool_calls = sum(1 for i in all_items if i.type == "function_call")
        logger.info(f"Context: {len(all_items)} items | {user_msgs} user msgs | {tool_calls} tool calls")
        
        # Handle fallback if agent didn't call send_sms
        if not sms_result:
            for item in reversed(session.history.items):
                if item.type == "message" and item.role == "assistant":
                    text = item.content[0] if item.content else None
                    if text and isinstance(text, str):
                        logger.info(f"Fallback: sending assistant message as SMS: {text}")
                        send_result = await send_sms(twilio_config, phone_number, text)
                        if send_result.success:
                            sms_result = SMSResult(action="sent", message=text)
                        else:
                            logger.error(f"Fallback failed: {send_result.error}")
                            sms_result = SMSResult(action="error", reason=send_result.error)
                        break
    
    # Log result
    if sms_result:
        if sms_result.action == "sent":
            logger.info(f"→ Sent: {sms_result.message}")
        elif sms_result.action == "skipped":
            logger.info(f"→ Skipped: {sms_result.reason}")
        else:
            logger.error(f"→ Error: {sms_result.reason}")
    else:
        logger.error("→ No output from agent")
        sms_result = SMSResult(action="error", reason="No output from agent")
    
    # POST updated context back to webhook server
    updated_context = ChatContext(all_items).to_dict(exclude_function_call=False)
    result_dict = {
        "action": sms_result.action,
        "message": sms_result.message,
        "reason": sms_result.reason,
    }
    
    await post_context_update(callback_url, phone_number, updated_context, result_dict)
    
    # Shutdown - we're done processing
    logger.info(f"SMS processing complete for {phone_number}")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="sms-agent"))

