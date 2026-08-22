# Spike 2 — Kokoro realtime-factor benchmark (R4)

Proves Kokoro synthesizes faster than realtime, and by how much, on the target host.

## On the host (over SSH)

```bash
ssh you@your-host
mkdir -p ~/kokoro-bench && cd ~/kokoro-bench
# copy docker-compose.yml + bench.py here (scp), then:
docker compose up -d          # first pull is ~2 GB
python3 bench.py
python3 bench.py --long 30    # generates long_test_30min.{ogg,mp3} for spike 1
docker compose down           # when done benching (or leave up — it's the real container)
```

## Comparing a second host (optional)

Bring the same `docker-compose.yml` up on the other machine, then point the bench at it:

```bash
python3 bench.py --url http://SECOND_HOST:8880
```

Tear the second one down after — it's only for the numbers.

## What to record

The full output of `bench.py` from each machine, plus the CPU model line.

**Pass:** best RTF ≥ 3.0×.

### Result (Apple M1 Max)

```
kokoro-bench % python3 bench.py
Kokoro benchmark → http://localhost:8880  (voice: af_heart)
Host info: run `sysctl -n machdep.cpu.brand_string` (macOS) or `lscpu | grep 'Model name'` (Linux) and include it when pasting back.

mode                   audio est     wall    RTF
single-stream             54.8s    14.6s   3.8x
parallel x4               54.8s    13.8s   4.0x
parallel x8               54.8s    13.9s   3.9x

PASS if best RTF >= 3.0x (cold path must outrun 2.0x playback).
```

