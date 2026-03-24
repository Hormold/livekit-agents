#!/usr/bin/env python3
"""
Transcribe audio files using LiveKit Inference STT.

Streams audio frames over WebSocket to LiveKit Cloud and prints
the transcript in real time (interim = dim, final = normal).
After all files are processed, prints the complete transcript to stdout.

Usage:
    uv run python transcribe.py recording.wav
    uv run python transcribe.py -m deepgram/nova-3 -l en *.mp3
    uv run python transcribe.py interview.wav -o transcript.txt
    uv run python transcribe.py -q recording.wav          # just text, no live preview

Requires LIVEKIT_API_KEY and LIVEKIT_API_SECRET in .env or environment.
"""

import argparse
import asyncio
import os
import sys
import time

import aiohttp
from dotenv import load_dotenv
from livekit.agents import inference, stt
from livekit.agents.utils.audio import audio_frames_from_file

# ANSI escape codes for live terminal output
CLEAR_LINE = "\033[2K\r"
DIM = "\033[2m"
RESET = "\033[0m"
GREEN = "\033[32m"
CYAN = "\033[36m"

# STT expects 16kHz mono PCM — the decoder resamples any input format automatically
SAMPLE_RATE = 16000


async def transcribe_file(filepath: str, model: str, language: str, quiet: bool = False) -> str:
    """
    Push audio frames into the STT stream while collecting transcript events concurrently.

    Push and collect run in parallel so interim results appear during streaming.
    After all frames are pushed, flush() + end_input() signal the STT that audio is complete.
    A 30s timeout after end_input guards against provider hangs.
    """
    filename = os.path.basename(filepath)

    # We run outside the LiveKit agent framework, so we manage our own HTTP session.
    # (Inside an AgentSession this would be handled automatically.)
    http_session = aiohttp.ClientSession()
    stt_instance = inference.STT(model=model, language=language, http_session=http_session)
    stream = stt_instance.stream()

    final_parts: list[str] = []
    current_interim = ""
    input_done = asyncio.Event()
    t0 = time.time()

    def render_live():
        """Overwrite the current terminal line with the latest transcript state."""
        if quiet:
            return
        elapsed = time.time() - t0
        text = " ".join(final_parts)
        if current_interim:
            text += (" " if text else "") + f"{DIM}{current_interim}{RESET}"

        try:
            cols = os.get_terminal_size().columns
        except OSError:
            cols = 120
        max_len = cols - 10
        if len(text) > max_len:
            text = "…" + text[-(max_len - 1):]

        sys.stderr.write(f"{CLEAR_LINE}{CYAN}[{elapsed:5.1f}s]{RESET} {text}")
        sys.stderr.flush()

    # Deepgram needs all frames pushed fast then sequential collect.
    # ElevenLabs (and others) need concurrent push+collect with pacing for live interims.
    is_deepgram = model.startswith("deepgram/")

    async def push_audio():
        """Decode the file and push PCM frames into the STT stream."""
        async for frame in audio_frames_from_file(filepath, sample_rate=SAMPLE_RATE, num_channels=1):
            stream.push_frame(frame)
            # Deepgram needs half-realtime pacing, others need minimal pacing
            if is_deepgram:
                await asyncio.sleep(frame.duration * 0.5)
            else:
                await asyncio.sleep(0.005)
        stream.flush()
        stream.end_input()
        input_done.set()

    async def collect_transcript():
        """Consume speech events. Exit on END_OF_SPEECH after input is done."""
        nonlocal current_interim
        async for event in stream:
            if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                text = event.alternatives[0].text if event.alternatives else ""
                if text:
                    final_parts.append(text)
                    current_interim = ""
                    render_live()
            elif event.type == stt.SpeechEventType.INTERIM_TRANSCRIPT:
                current_interim = event.alternatives[0].text if event.alternatives else ""
                render_live()
            elif event.type == stt.SpeechEventType.END_OF_SPEECH:
                if input_done.is_set():
                    return

    async def collect_with_timeout():
        """Let events flow, but force-stop 5s (or 30s for Deepgram) after input ends."""
        timeout = 60.0 if is_deepgram else 5.0
        task = asyncio.create_task(collect_transcript())
        await input_done.wait()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    try:
        if not quiet:
            sys.stderr.write(f"{GREEN}▶ {filename}{RESET}  model={model}  lang={language}\n")
        await asyncio.gather(push_audio(), collect_with_timeout())
    finally:
        await stream.aclose()
        await stt_instance.aclose()
        await http_session.close()

    if not quiet:
        elapsed = time.time() - t0
        sys.stderr.write(f"{CLEAR_LINE}{GREEN}✓ {filename}{RESET}  {elapsed:.1f}s\n")
        sys.stderr.flush()

    return " ".join(final_parts)


async def run(args: argparse.Namespace) -> None:
    results: list[tuple[str, str]] = []

    for filepath in args.files:
        if not os.path.isfile(filepath):
            sys.stderr.write(f"Error: file not found: {filepath}\n")
            continue
        transcript = await transcribe_file(
            filepath, model=args.model, language=args.language, quiet=args.quiet,
        )
        results.append((filepath, transcript))

    # Write final transcripts to stdout (or file with -o)
    out = open(args.output, "w") if args.output else sys.stdout
    try:
        for filepath, transcript in results:
            if len(args.files) > 1:
                out.write(f"--- {os.path.basename(filepath)} ---\n")
            out.write(transcript + "\n")
            if len(args.files) > 1:
                out.write("\n")
    finally:
        if args.output:
            out.close()


def main():
    load_dotenv()

    for var in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        if not os.getenv(var):
            sys.stderr.write(f"Error: {var} not set\n")
            sys.exit(1)

    p = argparse.ArgumentParser(description="Transcribe audio files using LiveKit Inference STT")
    p.add_argument("files", nargs="+", help="Audio files (mp3, wav, ogg, …)")
    p.add_argument("-m", "--model", default="elevenlabs/scribe_v2_realtime", help="STT model")
    p.add_argument("-l", "--language", default="en", help="Language code")
    p.add_argument("-o", "--output", default=None, help="Output file (default: stdout)")
    p.add_argument("-q", "--quiet", action="store_true", help="Just output text, no live preview")

    asyncio.run(run(p.parse_args()))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)  # kill lingering aiohttp/livekit background threads


if __name__ == "__main__":
    main()
