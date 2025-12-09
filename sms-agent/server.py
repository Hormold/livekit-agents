from __future__ import annotations

import logging
import os
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

local_env = Path(__file__).parent / ".env"
root_env = Path(__file__).parent.parent / ".env"
load_dotenv(local_env if local_env.exists() else root_env)

from agent import ContextManager, SMSResult, process_sms, TwilioConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
# Quiet noisy loggers
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

PORT = int(os.getenv("SMS_AGENT_PORT", "5000"))


async def handle_twilio_webhook(request: web.Request) -> web.Response:
    try:
        data = await request.post()
        from_number = data.get("From", "")
        to_number = data.get("To", "")
        body = data.get("Body", "")

        if not from_number or not body:
            return web.json_response({"status": "error", "message": "Missing required fields"}, status=400)

        result = await process_sms(
            from_number, to_number, body,
            request.app["context_manager"],
            request.app["twilio_config"],
        )
        _log_result(result)

        return web.json_response({
            "status": "success",
            "action": result.action,
            "message": result.message,
            "reason": result.reason,
        })
    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "healthy"})


async def handle_test(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        from_number = data.get("from", "+1234567890")
        body = data.get("body", "Hello!")

        result = await process_sms(
            from_number, request.app["twilio_config"].from_number, body,
            request.app["context_manager"],
            request.app["twilio_config"],
        )
        _log_result(result)

        return web.json_response({
            "status": "success",
            "action": result.action,
            "message": result.message,
            "reason": result.reason,
        })
    except Exception as e:
        logger.exception(f"Test error: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


def _log_result(result: SMSResult) -> None:
    if result.action == "sent":
        logger.info(f"→ Sent: {result.message}")
    elif result.action == "skipped":
        logger.info(f"→ Skipped: {result.reason}")
    else:
        logger.error(f"→ Error: {result.reason}")


def create_app() -> web.Application:
    app = web.Application()
    app["context_manager"] = ContextManager()
    app["twilio_config"] = TwilioConfig.from_env()
    app.router.add_post("/webhook/twilio/receive", handle_twilio_webhook)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/test", handle_test)
    return app


def main() -> None:
    twilio_config = TwilioConfig.from_env()

    logger.info("SMS Agent Server starting")
    logger.info(f"URL: http://localhost:{PORT}")
    logger.info(f"Twilio: {twilio_config.from_number or 'Not configured'}")

    if not twilio_config.is_configured():
        logger.warning("Missing TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, or TWILIO_PHONE_NUMBER")

    web.run_app(create_app(), port=PORT, print=None)


if __name__ == "__main__":
    main()
