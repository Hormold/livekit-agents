from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class TwilioConfig:
    account_sid: str
    auth_token: str
    from_number: str

    @classmethod
    def from_env(cls) -> TwilioConfig:
        return cls(
            account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            from_number=os.getenv("TWILIO_PHONE_NUMBER", ""),
        )

    def is_configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.from_number)


@dataclass
class SendSMSResult:
    """Result of sending an SMS via Twilio."""
    success: bool
    message_sid: str | None = None
    error: str | None = None


async def send_sms(config: TwilioConfig, to_number: str, message: str, timeout: int = 30) -> SendSMSResult:
    if not config.is_configured():
        return SendSMSResult(success=False, error="Twilio not configured")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{config.account_sid}/Messages.json"
    auth = aiohttp.BasicAuth(config.account_sid, config.auth_token)
    payload = {"To": to_number, "From": config.from_number, "Body": message}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, auth=auth, data=payload, timeout=timeout) as resp:
                if resp.status in (200, 201):
                    result = await resp.json()
                    return SendSMSResult(success=True, message_sid=result.get("sid"))
                error = await resp.text()
                return SendSMSResult(success=False, error=f"HTTP {resp.status}: {error}")
    except aiohttp.ClientError as e:
        return SendSMSResult(success=False, error=f"Network error: {e}")
    except Exception as e:
        return SendSMSResult(success=False, error=str(e))


@dataclass
class PhoneNumberInfo:
    """Information about a Twilio phone number."""
    sid: str
    phone_number: str
    sms_url: str | None = None


async def get_phone_number_info(config: TwilioConfig, timeout: int = 30) -> PhoneNumberInfo | None:
    """Get the SID and current SMS webhook URL for a phone number."""
    if not config.is_configured():
        logger.error("Twilio not configured")
        return None

    # URL-encode the phone number for the query parameter
    encoded_number = quote(config.from_number, safe="")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{config.account_sid}/IncomingPhoneNumbers.json?PhoneNumber={encoded_number}"
    auth = aiohttp.BasicAuth(config.account_sid, config.auth_token)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, auth=auth, timeout=timeout) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.error(f"Failed to get phone number info: HTTP {resp.status}: {error}")
                    return None

                data = await resp.json()
                numbers = data.get("incoming_phone_numbers", [])
                if not numbers:
                    logger.error(f"Phone number {config.from_number} not found in account")
                    return None

                number_info = numbers[0]
                return PhoneNumberInfo(
                    sid=number_info.get("sid", ""),
                    phone_number=number_info.get("phone_number", ""),
                    sms_url=number_info.get("sms_url"),
                )
    except aiohttp.ClientError as e:
        logger.error(f"Network error getting phone info: {e}")
        return None
    except Exception as e:
        logger.error(f"Error getting phone info: {e}")
        return None


async def update_sms_webhook_url(config: TwilioConfig, phone_sid: str, sms_url: str, timeout: int = 30) -> bool:
    """Update the SMS webhook URL for a phone number."""
    if not config.is_configured():
        logger.error("Twilio not configured")
        return False

    url = f"https://api.twilio.com/2010-04-01/Accounts/{config.account_sid}/IncomingPhoneNumbers/{phone_sid}.json"
    auth = aiohttp.BasicAuth(config.account_sid, config.auth_token)
    payload = {"SmsUrl": sms_url}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, auth=auth, data=payload, timeout=timeout) as resp:
                if resp.status in (200, 201):
                    logger.info(f"Updated SMS webhook URL to: {sms_url}")
                    return True
                error = await resp.text()
                logger.error(f"Failed to update SMS webhook: HTTP {resp.status}: {error}")
                return False
    except aiohttp.ClientError as e:
        logger.error(f"Network error updating webhook: {e}")
        return False
    except Exception as e:
        logger.error(f"Error updating webhook: {e}")
        return False


async def ensure_sms_webhook(config: TwilioConfig, webhook_url: str) -> bool:
    """Ensure the SMS webhook URL is set correctly for the configured phone number.
    
    Returns True if webhook is already correct or was successfully updated.
    """
    expected_url = f"{webhook_url.rstrip('/')}/webhook/twilio/receive"
    
    # Get current phone number info
    phone_info = await get_phone_number_info(config)
    if not phone_info:
        logger.error("Could not get phone number info")
        return False

    # Check if webhook URL already matches
    if phone_info.sms_url == expected_url:
        logger.info(f"SMS webhook already configured: {expected_url}")
        return True

    # Update webhook URL
    logger.info(f"Updating SMS webhook from '{phone_info.sms_url}' to '{expected_url}'")
    return await update_sms_webhook_url(config, phone_info.sid, expected_url)
