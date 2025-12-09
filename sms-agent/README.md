# SMS Agent

SMS agent using LiveKit Agents + Twilio. Two deployment approaches.

## Quick Start

```bash
uv sync
```

---

## Approach 1: Self-Hosted

Everything runs on your infrastructure. Single process handles webhooks and agent processing.

```bash
uv run server.py
```

Point Twilio webhook to `POST /webhook/twilio/receive`

**Files:**
| File | Description |
|------|-------------|
| `server.py` | Webhook server + agent processing |
| `agent/` | Agent logic, tools, context storage |

---

## Approach 2: LiveKit Cloud Dispatch

Split architecture for scalability. Your server handles webhooks + context, LiveKit Cloud runs the agent.

```
Twilio → dispatcher.py → LiveKit Cloud → worker.py
              ↑                              ↓
              └────── context callback ──────┘
```

**Your Server** (webhooks + context management):

```bash
WEBHOOK_URL=https://your-server.com uv run dispatcher.py
```

**Worker** (agent processing) — choose one:

```bash
# Option A: Deploy to LiveKit Cloud
lk cloud deploy

# Option B: Run locally alongside dispatcher
uv run worker.py dev
```

On startup, `dispatcher.py` auto-configures Twilio webhook URL.

**Files:**
| File | Description | Runs on |
|------|-------------|---------|
| `dispatcher.py` | Webhooks + context + dispatch | Your server |
| `worker.py` | Stateless agent worker | LiveKit Cloud or local |
| `agent/` | Shared agent logic | Both |

---

## Environment Variables

```bash
# Both approaches
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1...
LIVEKIT_URL=...
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...

# Approach 2 only
WEBHOOK_URL=https://your-server.com
```

## Endpoints

**Approach 1 (`server.py`):**

- `POST /webhook/twilio/receive` — Twilio webhook
- `POST /test` — Test endpoint
- `GET /health` — Health check

**Approach 2 (`dispatcher.py`):**

- `POST /webhook/twilio/receive` — Twilio webhook → dispatch
- `POST /webhook/agent/complete` — Worker callback → save context
- `GET /health` — Health check

## Customization

- **Agent behavior**: Edit `INSTRUCTIONS` in `agent/sms_agent.py`
- **Add tools**: Add `@function_tool()` methods to `SMSAgent` class
- **Change LLM**: Edit `llm` parameter
- **Context storage**: Replace `ContextManager` with DB (see TODO comments)
