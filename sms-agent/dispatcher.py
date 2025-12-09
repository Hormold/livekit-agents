"""
SMS Dispatcher - Webhook handler + context manager that dispatches to LiveKit Cloud workers.

This server:
1. Auto-configures Twilio webhook URL on startup
2. Receives incoming SMS via Twilio webhook
3. Loads conversation context and dispatches to LiveKit worker
4. Receives updated context from worker and saves it

Environment Variables:
    WEBHOOK_URL: Public URL for this server (e.g., https://your-server.com)
    SMS_AGENT_PORT: Port to listen on (default: 5000)
    TWILIO_ACCOUNT_SID: Twilio account SID
    TWILIO_AUTH_TOKEN: Twilio auth token
    TWILIO_PHONE_NUMBER: Twilio phone number
    LIVEKIT_URL: LiveKit server URL
    LIVEKIT_API_KEY: LiveKit API key
    LIVEKIT_API_SECRET: LiveKit API secret
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv
from livekit import api
from livekit.agents.llm import ChatContext

local_env = Path(__file__).parent / ".env"
root_env = Path(__file__).parent.parent / ".env"
load_dotenv(local_env if local_env.exists() else root_env)

from agent import ContextManager, TwilioConfig, ensure_sms_webhook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

PORT = int(os.getenv("SMS_AGENT_PORT", "5000"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
AGENT_NAME = "sms-agent"

# Maximum metadata size (50KB to be safe, well under 64KB limit)
MAX_METADATA_SIZE = 50 * 1024


def truncate_context(chat_ctx_dict: dict, max_size: int = MAX_METADATA_SIZE) -> dict:
    """Truncate chat context to fit within metadata size limits.
    
    Keeps most recent messages if context is too large.
    """
    items = chat_ctx_dict.get("items", [])
    if not items:
        return chat_ctx_dict
    
    # Try full context first
    test_json = json.dumps(chat_ctx_dict)
    if len(test_json.encode("utf-8")) <= max_size:
        return chat_ctx_dict
    
    # Truncate from the beginning (keep recent messages)
    logger.warning(f"Context too large ({len(test_json)} bytes), truncating...")
    while items and len(json.dumps({"items": items}).encode("utf-8")) > max_size - 1024:
        items = items[1:]  # Remove oldest item
    
    logger.info(f"Truncated to {len(items)} items")
    return {"items": items}


async def dispatch_sms_agent(
    app: web.Application,
    phone_number: str,
    incoming_message: str,
    chat_context: dict | None,
) -> str | None:
    """Dispatch an SMS agent to process the message.
    
    Returns the dispatch ID on success, None on failure.
    """
    twilio_config: TwilioConfig = app["twilio_config"]
    
    # Build metadata payload
    metadata = {
        "phone_number": phone_number,
        "incoming_message": incoming_message,
        "callback_url": f"{WEBHOOK_URL.rstrip('/')}/webhook/agent/complete",
        "twilio_config": {
            "account_sid": twilio_config.account_sid,
            "auth_token": twilio_config.auth_token,
            "from_number": twilio_config.from_number,
        },
    }
    
    # Add chat context if available (truncate if needed)
    if chat_context:
        metadata["chat_context"] = truncate_context(chat_context)
    
    metadata_json = json.dumps(metadata)
    logger.info(f"Metadata size: {len(metadata_json.encode('utf-8'))} bytes")
    
    # Create unique room name for this SMS interaction
    room_name = f"sms-{phone_number.replace('+', '')}-{uuid.uuid4().hex[:8]}"
    
    try:
        lkapi = api.LiveKitAPI()
        dispatch = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata_json,
            )
        )
        await lkapi.aclose()
        
        logger.info(f"Dispatched agent to room {room_name}, dispatch_id={dispatch.id}")
        return dispatch.id
    except Exception as e:
        logger.exception(f"Failed to dispatch agent: {e}")
        return None


async def handle_twilio_webhook(request: web.Request) -> web.Response:
    """Handle incoming SMS from Twilio webhook."""
    try:
        data = await request.post()
        from_number = data.get("From", "")
        to_number = data.get("To", "")
        body = data.get("Body", "")

        if not from_number or not body:
            return web.json_response(
                {"status": "error", "message": "Missing required fields"},
                status=400,
            )

        logger.info(f"Incoming SMS from {from_number}: {body}")

        # Load existing context
        # TODO: Replace with database query for production
        # e.g., chat_context = await db.get_chat_context(from_number)
        context_manager: ContextManager = request.app["context_manager"]
        saved_ctx = context_manager.get(from_number)
        chat_context = saved_ctx.to_dict(exclude_function_call=False) if saved_ctx else None
        
        if saved_ctx:
            logger.info(f"Loaded {len(saved_ctx.items)} history items for {from_number}")

        # Dispatch agent to process SMS
        dispatch_id = await dispatch_sms_agent(
            request.app,
            from_number,
            body,
            chat_context,
        )

        if dispatch_id:
            return web.json_response({
                "status": "dispatched",
                "dispatch_id": dispatch_id,
                "message": "Agent dispatched to process SMS",
            })
        else:
            return web.json_response(
                {"status": "error", "message": "Failed to dispatch agent"},
                status=500,
            )

    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_agent_complete(request: web.Request) -> web.Response:
    """Handle agent completion callback - save updated context."""
    try:
        data = await request.json()
        phone_number = data.get("phone_number")
        chat_context = data.get("chat_context")
        result = data.get("result", {})

        if not phone_number:
            return web.json_response(
                {"status": "error", "message": "Missing phone_number"},
                status=400,
            )

        logger.info(f"Agent completed for {phone_number}: {result.get('action', 'unknown')}")

        # Save updated context
        # TODO: Replace with database save for production
        # e.g., await db.save_chat_context(phone_number, chat_context)
        if chat_context:
            context_manager: ContextManager = request.app["context_manager"]
            ctx = ChatContext.from_dict(chat_context)
            context_manager.save(phone_number, ctx)
            logger.info(f"Saved {len(ctx.items)} context items for {phone_number}")

        return web.json_response({
            "status": "success",
            "message": "Context saved",
        })

    except Exception as e:
        logger.exception(f"Agent complete error: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    """Health check endpoint."""
    return web.json_response({"status": "healthy", "mode": "livekit-dispatch"})


async def on_startup(app: web.Application) -> None:
    """Configure Twilio webhook on server startup."""
    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL not set - skipping Twilio webhook auto-configuration")
        return

    twilio_config: TwilioConfig = app["twilio_config"]
    if not twilio_config.is_configured():
        logger.warning("Twilio not configured - skipping webhook auto-configuration")
        return

    logger.info("Checking Twilio webhook configuration...")
    success = await ensure_sms_webhook(twilio_config, WEBHOOK_URL)
    if success:
        logger.info("Twilio webhook configured successfully")
    else:
        logger.error("Failed to configure Twilio webhook")


def create_app() -> web.Application:
    """Create the aiohttp application."""
    app = web.Application()
    app["context_manager"] = ContextManager()
    app["twilio_config"] = TwilioConfig.from_env()
    
    # Routes
    app.router.add_post("/webhook/twilio/receive", handle_twilio_webhook)
    app.router.add_post("/webhook/agent/complete", handle_agent_complete)
    app.router.add_get("/health", handle_health)
    
    # Startup hook
    app.on_startup.append(on_startup)
    
    return app


def main() -> None:
    """Main entry point."""
    twilio_config = TwilioConfig.from_env()

    logger.info("SMS Dispatcher starting")
    logger.info(f"URL: http://localhost:{PORT}")
    logger.info(f"Webhook URL: {WEBHOOK_URL or 'Not configured'}")
    logger.info(f"Twilio: {twilio_config.from_number or 'Not configured'}")
    logger.info(f"Agent name: {AGENT_NAME}")

    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL not set - workers won't be able to callback")

    if not twilio_config.is_configured():
        logger.warning("Missing TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, or TWILIO_PHONE_NUMBER")

    # Check LiveKit configuration
    livekit_url = os.getenv("LIVEKIT_URL", "")
    livekit_key = os.getenv("LIVEKIT_API_KEY", "")
    livekit_secret = os.getenv("LIVEKIT_API_SECRET", "")
    
    if not all([livekit_url, livekit_key, livekit_secret]):
        logger.warning("Missing LIVEKIT_URL, LIVEKIT_API_KEY, or LIVEKIT_API_SECRET")

    web.run_app(create_app(), port=PORT, print=None)


if __name__ == "__main__":
    main()

