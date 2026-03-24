# lk-stt-cli

Minimal CLI to transcribe audio files using [LiveKit Inference](https://docs.livekit.io/agents/models/inference/) STT.

Streams audio frames over WebSocket to LiveKit Cloud — shows a live transcript in the terminal, then outputs the final text.

## Setup

```bash
cd lk-stt-cli
uv sync
```

Create a `.env` file with your LiveKit Cloud credentials:

```
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_URL=wss://your-project.livekit.cloud
```

## Usage

```bash
# Transcribe a file
uv run python transcribe.py recording.wav

# Use a different model
uv run python transcribe.py -m deepgram/nova-3 -l en audio.mp3

# Multiple files, save to file
uv run python transcribe.py *.mp3 -o transcripts.txt
```

### Record from mic

```bash
# Record (Ctrl+C to stop), then transcribe
uv run python record.py my_speech.wav
uv run python transcribe.py my_speech.wav
```

## Available models

Any model from [LiveKit Inference STT](https://docs.livekit.io/agents/models/stt/) works. Examples:

| Model | ID |
|---|---|
| ElevenLabs Scribe v2 (default) | `elevenlabs/scribe_v2_realtime` |
| Deepgram Nova-3 | `deepgram/nova-3` |
| Deepgram Flux | `deepgram/flux-general` |
| AssemblyAI Universal-3 Pro | `assemblyai/u3-rt-pro` |
| Cartesia Ink Whisper | `cartesia/ink-whisper` |

## Prompt used to create this project

This entire project was built by Claude Code in a single session using this prompt:

> I need a minimal Python CLI to batch-transcribe audio files using LiveKit Inference STT. Files should be streamed frame-by-frame over WebSocket (not sent as a batch). Use `elevenlabs/scribe_v2_realtime` as the default model. Show the transcript in real time in the terminal — interim results displayed live, cleared and replaced by final text when confirmed. Include a helper script to record audio from the default Mac microphone. The app should exit cleanly when done. Keep it minimalist: use UV, minimal dependencies, clean code with sensible comments suitable for sharing as an example. Study the LiveKit Agents 1.5.1 source via MCP to understand the correct API usage.
