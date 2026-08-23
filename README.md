# Echo — Kavita read-to-listen bridge

Echo turns the book you're **reading** in [Kavita](https://www.kavitareader.com/)
into an **audiobook you can listen to** — picking up exactly where you left off,
and writing your listening position back so the text and the audio always share
one bookmark. Speech is synthesized locally with
[Kokoro](https://github.com/remsky/Kokoro-FastAPI); nothing leaves your network
and there are no per-word cloud costs.

> **Heads up:** Echo was built for one person's setup and is shared because the
> approach might be useful to someone else — not as a maintained product. It
> works, it's tested, and the reasoning below is honest about the trade-offs.
> Issues are welcome, but I may be slow, and I'm not taking feature requests.

**Full design spec:** [docs/design.md](docs/design.md) — the problem, the decision table, the Kavita position model, and the risk register, written before the build.

## What it does

- Reads your current position from Kavita (down to the paragraph).
- Synthesizes the whole book to audio in the background, chapter by chapter,
  faster than real time, and keeps it.
- Plays it back as a phone web-app (PWA) with lock-screen controls, variable
  speed, paragraph/chapter skipping, and a live current-sentence display.
- Writes your listening progress back to Kavita, so when you open the book to
  read again, you're within a page of where you stopped listening.

## Why it exists

I read across two modes — text on a reader, audio in the car and the kitchen —
and the bookmark never followed me between them. Commercial audiobooks are a
separate purchase with a separate position; text-to-speech apps don't know what
my library says I'm reading. Echo closes that loop against a library I already
own, with a voice engine that runs on my own hardware.

## Requirements

- A running **Kavita** server with an API key (User Settings → API Key).
- A running **Kokoro FastAPI (CPU)** instance — the included `docker-compose.yml`
  starts one for you.
- **Docker** (for the quickstart) or **Python 3.12** (to run the API directly).
- A phone for the player; it installs as a home-screen web-app, no app store.

## Quickstart

```bash
git clone https://github.com/werkilla/echo.git
cd echo
cp .env.example .env        # then set KAVITA_URL, KAVITA_API_KEY, ECHO_TOKEN
docker compose up -d --build   # starts echo (:7338) and kokoro (internal)
```

Open `http://<the-host>:7338` on your phone, enter your `ECHO_TOKEN` once, read a
page of any book in Kavita, and it appears in Echo's library — tap **Resume** and
it synthesizes in the background (roughly 2–3 h of wall-clock for a full novel on
a modern CPU) and starts playing.

To run the API without Docker (you still need Kokoro reachable at `KOKORO_URL`):

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
set -a && . ./.env && set +a
uvicorn echo.main:app --port 7338
```

## Configuration

All configuration is environment variables (see `.env.example`):

| Var | What it is |
|---|---|
| `KAVITA_URL` | Base URL of your Kavita server |
| `KAVITA_API_KEY` | Kavita API key (User Settings → API Key) |
| `ECHO_TOKEN` | Shared bearer token the PWA sends; set it to anything long |
| `KOKORO_URL` | Where Kokoro is reachable (default `http://kokoro:8880`) |
| `AUDIO_MAX_GB` | Cap on stored synthesized audio before old books are pruned |
| `POLL_MINUTES` | How often to check Kavita's on-deck for new books |
| `DEFAULT_VOICE` | Kokoro voice id (default `af_heart`) |

## Deploying to your own server

`make deploy` builds the image from a repo clone on a server and (re)starts the
container. **The Makefile ships with placeholder hosts** — put your real target in
a git-ignored `deploy.local.mk` (copy `deploy.local.mk.example`); it's included
automatically and overrides the placeholders, so nothing host-specific is ever
committed. `deploy/dockge-compose.yml` is an image-only compose for running under
a stack manager like [Dockge](https://github.com/louislam/dockge). These are the
shape of *my* deploy; yours will differ.

## API quick reference

Bearer `ECHO_TOKEN`; media endpoints also accept `?token=`.

| | |
|---|---|
| `GET /api/health` | echo/kavita/kokoro status + stored audio GB (unauthenticated) |
| `GET /api/books` · `POST /api/books/refresh` | library (from Kavita on-deck) |
| `POST /api/books/{id}/prepare` | queue full-book synthesis |
| `GET /api/books/{id}/position` | Kavita position → spine/block/segment/offset |
| `GET /api/books/{id}/chapters/{sp}/index` | sentence→timestamp index |
| `GET /api/audio/{id}/{sp}.mp3` | assembled chapter (HTTP Range supported) |
| `GET /api/books/{id}/stream?spine=&from_seg=` | cold-path live stream |
| `POST /api/books/{id}/progress` | write position back to Kavita (safety rules below) |
| `POST /api/books/{id}/undo-sync` | restore Kavita progress before Echo's last write |
| `DELETE /api/books/{id}/audio` | delete a book's stored audio |

## Why it's built this way (and the hard-won notes)

These are the findings that took real time to earn — kept here because they're the
difference between "works on my machine" and "works":

1. **iOS + concatenated audio is why Echo is MP3.** On iOS Safari, MP3 frames
   concatenated back-to-back play seamlessly across chapter boundaries with the
   screen locked; chained Ogg/Opus streams play only the *first* chunk and stop.
   That single result (see `spikes/01-ios-audio/`) decided the container format.
2. **Position is mapped through Kavita's `bookScrollId` XPath.** Kavita reports
   reading position as an XPath-like anchor into the EPUB (e.g. `//body/p[5]`), and
   `pageNum` is the 0-based spine index. Echo resolves that anchor to a paragraph,
   synthesizes from there, and regenerates the same anchor to write back — the
   round-trip is covered by tests so a Kavita upgrade can't silently break sync.
3. **Write-back is guarded.** Echo only advances Kavita's position, never rewinds
   it below where you already were, and every write is reversible via
   `undo-sync`. Losing a real reading position to a playback glitch is
   unacceptable, so the write path is conservative by design.
4. **Synthesis is faster than playback, so it can stream cold.** Kokoro on a
   modern CPU runs several times real time single-stream (no parallel speedup —
   the model saturates cores per request), which is enough for the player to
   start on a not-yet-synthesized chapter and stay ahead of the listener.
5. **Pin the Kavita contract.** The integration is pinned to a known Kavita
   version; re-run `spikes/03-kavita-contract/` after any Kavita upgrade before
   trusting sync again.

The `spikes/` directory holds the throwaway experiments that de-risked each of
these before the real build — they're kept as evidence, not as part of the app.

## License

[MIT](LICENSE) © 2026 Adriana Cavazos.
