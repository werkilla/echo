# Spike 1 — iOS lock-screen audio (R2)

Proves an iPhone PWA keeps playing a long audio file with the screen locked, that lock-screen (Media Session) controls work, and that a chapter-boundary file switch survives lock. Also decides Opus vs MP3 (§16 #1).

## Setup

1. Run spike 2 first with `--long 30`; copy `long_test_30min.ogg` and `long_test_30min.mp3` into this directory.
2. Serve from your dev machine: `cd spikes/01-ios-audio && python3 -m http.server 8765`
3. On iPhone Safari (same Wi-Fi): `http://<your-computer-ip>:8765`

## Test protocol

1. Tap **Play Opus**. Does audio start at all? (If not: Opus is out, use MP3 — that alone answers §16 #1.)
2. Lock the phone. Verify lock-screen shows title + play/pause/skip controls, and they work.
3. Leave locked ≥ 30 min. At file end the page auto-switches to the other file ("chapter handoff") — keep it locked through that.
4. Unlock, check the log: heartbeat lines every 30s. **Gaps = iOS suspended playback = fail.** The `ENDED — handoff` line followed by continuing heartbeats = handoff passed.
5. Repeat once via Add-to-Home-Screen (standalone) if plain Safari failed anything.

## What to paste back

The full log (tap-and-hold to copy), plus: Safari or standalone, iOS version.

**Pass:** ≥ 30 min locked playback, working lock-screen controls, handoff survives lock, in at least one format.

### Result

Oops. I didn't copy the log but I can tell you what I experienced:
- Tested in Safari iOS directly
- .ogg file DID PLAY. However, I'm not sure if it was intended, but the text was only spoken once. The rest was blank audio, so the player would automatically switch to the mp3 after 40 seconds or so. The mp3 file played correctly for the full duration, and then switched to the other file as well.
- Controls worked fine on locked screen