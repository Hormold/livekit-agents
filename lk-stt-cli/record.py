#!/usr/bin/env python3
"""
Record audio from the default microphone. Press Ctrl+C to stop.

Usage:
    uv run python record.py                  # saves to recording.wav
    uv run python record.py my_speech.wav    # custom filename
"""

import argparse
import signal
import sys
import wave

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1


def main():
    p = argparse.ArgumentParser(description="Record audio from default mic")
    p.add_argument("output", nargs="?", default="recording.wav", help="Output wav file")
    p.add_argument("-r", "--rate", type=int, default=SAMPLE_RATE, help="Sample rate")
    args = p.parse_args()

    frames: list[np.ndarray] = []
    stop = False

    def on_signal(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_signal)

    sys.stderr.write(f"Recording → {args.output} @ {args.rate}Hz  [Ctrl+C to stop]\n")

    with sd.InputStream(samplerate=args.rate, channels=CHANNELS, dtype="int16") as mic:
        while not stop:
            data, _ = mic.read(args.rate // 10)  # 100ms chunks
            frames.append(data.copy())
            sys.stderr.write(f"\r  {len(frames) * 0.1:.1f}s")
            sys.stderr.flush()

    sys.stderr.write("\n")

    audio = np.concatenate(frames)
    with wave.open(args.output, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(args.rate)
        wf.writeframes(audio.tobytes())

    sys.stderr.write(f"Saved {len(audio) / args.rate:.1f}s → {args.output}\n")


if __name__ == "__main__":
    main()
