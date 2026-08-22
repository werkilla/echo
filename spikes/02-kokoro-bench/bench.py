#!/usr/bin/env python3
"""Spike 2 (R4): Kokoro realtime-factor benchmark.

Measures how much faster than realtime Kokoro synthesizes speech,
single-stream and in parallel. Run against your target host(s).

Usage:
    pip3 install requests --break-system-packages   # stdlib otherwise
    python3 bench.py                                # assumes http://localhost:8880
    python3 bench.py --url http://YOUR_HOST:8880
    python3 bench.py --long 30                      # also generate ~30 min test
                                                    # files (ogg+mp3) for spike 1

Pass criteria: aggregate RTF >= 3x on the chosen host.
"""

import argparse
import concurrent.futures
import contextlib
import io
import json
import struct
import sys
import time
import urllib.request

SENTENCES = [
    "The rain had not stopped for three days, and the river was beginning to rise.",
    "She closed the book slowly — the ending was not what she had expected at all.",
    "In the distance, a bell rang twice; the sound carried strangely over the water.",
    "“You can't be serious,” he said, though he already knew that she was.",
    "The library smelled of dust and old paper, of decades of quiet afternoons.",
    "Somewhere above them, footsteps crossed the ceiling and paused at the stairs.",
    "It was the kind of morning that made even the most practical people believe in omens.",
    "He counted the coins again: still seven short, and the ferry left at dawn.",
    "The letters were tied with a faded ribbon, unopened for thirty years.",
    "Nothing about the house had changed, which was precisely what unsettled her.",
]

WORDS_PER_MIN = 150.0  # rough narration speed, used to estimate audio duration


def synth(url, text, voice, fmt):
    body = json.dumps({
        "model": "kokoro", "input": text, "voice": voice,
        "response_format": fmt, "speed": 1.0,
    }).encode()
    req = urllib.request.Request(
        f"{url}/v1/audio/speech", data=body,
        headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=300) as r:
        audio = r.read()
    return audio, time.monotonic() - t0


def est_duration(text):
    return len(text.split()) / WORDS_PER_MIN * 60.0


def run_pass(url, voice, fmt, workers):
    wall0 = time.monotonic()
    if workers == 1:
        results = [synth(url, s, voice, fmt) for s in SENTENCES]
    else:
        with concurrent.futures.ThreadPoolExecutor(workers) as ex:
            results = list(ex.map(lambda s: synth(url, s, voice, fmt), SENTENCES))
    wall = time.monotonic() - wall0
    audio_secs = sum(est_duration(s) for s in SENTENCES)
    return audio_secs, wall, sum(len(a) for a, _ in results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8880")
    ap.add_argument("--voice", default="af_heart")
    ap.add_argument("--long", type=int, default=0, metavar="MINUTES",
                    help="also generate long test audio (ogg+mp3) for spike 1")
    args = ap.parse_args()
    url = args.url.rstrip("/")

    print(f"Kokoro benchmark → {url}  (voice: {args.voice})")
    print(f"Host info: run `sysctl -n machdep.cpu.brand_string` (macOS) or "
          f"`lscpu | grep 'Model name'` (Linux) and include it when pasting back.\n")

    # warm-up (model load skews first request)
    synth(url, "Warm up the model, please.", args.voice, "mp3")

    print(f"{'mode':<22}{'audio est':>10}{'wall':>9}{'RTF':>7}")
    for label, workers in (("single-stream", 1), ("parallel x4", 4), ("parallel x8", 8)):
        audio_secs, wall, _ = run_pass(url, args.voice, "mp3", workers)
        rtf = audio_secs / wall
        print(f"{label:<22}{audio_secs:>8.1f}s{wall:>8.1f}s{rtf:>6.1f}x")

    print("\nPASS if best RTF >= 3.0x (cold path must outrun 2.0x playback).")

    if args.long:
        text = " ".join(SENTENCES)
        # ~10 sentences ≈ 45s of audio; repeat to reach target minutes
        reps = max(1, int(args.long * 60 / est_duration(text)))
        chunk_texts = [text] * reps
        for fmt, ext in (("opus", "ogg"), ("mp3", "mp3")):
            out = f"long_test_{args.long}min.{ext}"
            print(f"\nGenerating {out} ({reps} chunks, format={fmt}) …")
            with open(out, "wb") as f:
                for i, t in enumerate(chunk_texts):
                    audio, _ = synth(url, t, args.voice, fmt)
                    f.write(audio)
                    sys.stdout.write(f"\r  {i + 1}/{reps}")
                    sys.stdout.flush()
            print(f"\n  wrote {out}")
        print("\nNOTE: naive concatenation is fine for the spike (players tolerate "
              "chained ogg/mp3 streams); real Echo re-muxes properly. "
              "Copy both files into spikes/01-ios-audio/ for the iPhone test.")


if __name__ == "__main__":
    main()
