# SMS Agent

Template for an SMS agent using LiveKit Agents + Twilio.

## Setup

```
uv sync
uv run server.py
```

Env variables:

- TWILIO_ACCOUNT_SID
- TWILIO_AUTH_TOKEN
- TWILIO_PHONE_NUMBER (your Twilio phone number sending the SMS out to the user)
- LIVEKIT_API_KEY
- LIVEKIT_API_SECRET
- LIVEKIT_URL

Point Twilio webhook to POST /webhook/twilio/receive

## Files

server.py — HTTP server, webhooks, /test endpoint for debugging

agent/sms_agent.py — Main file. Agent logic and tools. Edit INSTRUCTIONS to change behavior.

agent/http_tools.py — Example tool (weather). Add your own tools here.

agent/twilio_utils.py — Sends SMS via Twilio API.

agent/context_manager.py — Saves conversation history per phone number to JSON.

## How to customize

Change agent behavior: edit INSTRUCTIONS in sms_agent.py

Add new tools: add @function_tool() methods to SMSAgent class

Change LLM model: edit llm parameter in process_sms()

## Endpoints

POST /webhook/twilio/receive — Twilio webhook
POST /test — test with {"from": "+1234567890", "body": "Hello"}
GET /health — health check
