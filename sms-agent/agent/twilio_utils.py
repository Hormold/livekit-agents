from __future__ import annotations

import os
from dataclasses import dataclass

import aiohttp


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
class SMSResult:
    success: bool
    message_sid: str | None = None
    error: str | None = None


async def send_sms(config: TwilioConfig, to_number: str, message: str, timeout: int = 30) -> SMSResult:
    if not config.is_configured():
        return SMSResult(success=False, error="Twilio not configured")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{config.account_sid}/Messages.json"
    auth = aiohttp.BasicAuth(config.account_sid, config.auth_token)
    payload = {"To": to_number, "From": config.from_number, "Body": message}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, auth=auth, data=payload, timeout=timeout) as resp:
                if resp.status in (200, 201):
                    result = await resp.json()
                    return SMSResult(success=True, message_sid=result.get("sid"))
                error = await resp.text()
                return SMSResult(success=False, error=f"HTTP {resp.status}: {error}")
    except aiohttp.ClientError as e:
        return SMSResult(success=False, error=f"Network error: {e}")
    except Exception as e:
        return SMSResult(success=False, error=str(e))
