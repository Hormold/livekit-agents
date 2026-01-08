# Language Switch Agent Demo

Two patterns for automatic language detection and STT switching in LiveKit agents.

## Pattern 1: Function Tool (`agent.py`)

The LLM has a tool to switch language. After a few turns, it decides when to call it.

```
┌──────────────────────────────────────────────────────────┐
│                      AgentSession                        │
│                                                          │
│   User speaks  ──►  Deepgram STT (multi)                │
│                           │                              │
│                           ▼                              │
│                     GPT-4.1-mini                         │
│                    (has instructions                     │
│                     to detect language)                  │
│                           │                              │
│              ┌────────────┴────────────┐                │
│              │                         │                 │
│              ▼                         ▼                 │
│        Normal reply          Calls function tool         │
│                              set_detected_language()     │
│                                        │                 │
│                                        ▼                 │
│                              STT switches to "ru"        │
│                              TTS switches to "ru"        │
│                              (locked, one-time only)     │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

## Pattern 2: Background Observer (`agent_observer.py`)

The main agent knows nothing about languages. A background observer monitors transcripts and switches STT silently.

```
┌──────────────────────────────────────────────────────────┐
│                      AgentSession                        │
│                                                          │
│   ┌────────────────────────────────────────────────┐    │
│   │           FeedbackCollectorAgent               │    │
│   │         (only knows about feedback)            │    │
│   └────────────────────────────────────────────────┘    │
│                           │                              │
│                           │ user_input_transcribed       │
│                           ▼                              │
│   ┌────────────────────────────────────────────────┐    │
│   │           Language Observer (background)       │    │
│   │                                                │    │
│   │   1. Collect 3+ user turns                     │    │
│   │   2. Send to LLM: "What language is this?"     │    │
│   │   3. If confidence ≥ 95% AND coherent speech:  │    │
│   │      → Switch STT to detected language         │    │
│   │   4. If mixed languages or gibberish:          │    │
│   │      → Do nothing, keep "multi"                │    │
│   │                                                │    │
│   └────────────────────────────────────────────────┘    │
│                           │                              │
│                           ▼                              │
│                  Deepgram STT: multi → ru                │
│                  (TTS stays unchanged)                   │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

## How the Observer Decides

```
User turn 1: "Привет"
User turn 2: "Как дела?"
User turn 3: "Хочу оставить отзыв"
                │
                ▼
        ┌───────────────┐
        │  Observer LLM │
        │               │
        │  Messages:    │
        │  - Привет     │
        │  - Как дела?  │
        │  - Хочу...    │
        │               │
        │  All Russian? │
        │  Coherent?    │
        └───────┬───────┘
                │
        ┌───────┴───────┐
        │               │
        ▼               ▼
   YES (99%)        NO (mixed/gibberish)
        │               │
        ▼               ▼
   Switch STT      Keep "multi"
   to "ru"
```

## Run

```bash
uv sync --python 3.12
uv run agent.py download-files

# Pattern 1
uv run agent.py console

# Pattern 2
uv run agent_observer.py console
```
