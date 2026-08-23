# Echo — Kavita Read-to-Listen Bridge

**Spec version:** 1.2 · 2026-07-18 · **Status:** ✅ v1 shipped 2026-07-18

> v1.1: full-book synthesis in the MVP, keep-everything retention, per-chapter playback
> files (§6 D12–D14). v1.2: `pageNum`=spine-index position model (§7.3) and MP3 format
> (§9.1), both verified live. Definition of done passed live on a real multi-chapter book:
> read in Kavita → Echo resumed at the position → locked-phone playback through a chapter
> boundary → Kavita reopened within a page.

*Runtime specifics (the actual URL, host, and deploy paths) live outside this repo; this
doc refers to upstreams by product name.*

---

## 1. Problem Statement

I read EPUBs (ebooks, light novels) from a self-hosted Kavita server on my phone.
Frequently I need to put the phone down mid-page (chores, a kid to chase) and want the book
to *continue as audio from that exact spot* — then later pick the phone back up and resume
reading where the audio stopped.

iOS's built-in screen reader is unusable for this: it breaks on em dashes, font changes, and
paragraph boundaries, and doesn't integrate with Kavita.

Nothing off the shelf does this. OpenReader has excellent TTS read-along but requires
abandoning Kavita's reader and library; batch converters (Audiblez/epub2tts) have no position
sync; Storyteller requires owning real audiobooks; Kavita itself has zero TTS (open feature
request, no roadmap commitment).

**Echo** is a small bridge service that makes Kavita's reading position the single bookmark
shared between reading and listening.

## 2. Goals

- **G1 — Read → listen:** Open Echo's player, hit play, audio begins at the paragraph I was
  last reading in Kavita. Target: audio starts ≤ 3 s after tapping play (warm cache) / ≤ 8 s
  (cold).
- **G2 — Listen → read:** While listening, Echo writes progress back to Kavita so reopening
  the book in Kavita lands within one page of where the audio stopped.
- **G3 — Hands-free listening:** Lock-screen play/pause/skip on the phone via Media Session;
  phone stays in a pocket.
- **G4 — Zero recurring cost:** All synthesis local (Kokoro). No cloud APIs.
- **G5 — Predictive readiness:** Active books are synthesized *in full* in the background; a
  manual "Prepare" button does the same for planned reads. Playback is instant from any
  position, and synthesized books are kept — the cache accumulates into a personal audiobook
  library.

## 3. Non-Goals (v1)

- PDF/TTRPG, manga, comics — EPUB only.
- Multi-user. Single Kavita account; a second user is a documented phase-2 option.
- Casting to smart speakers / displays — phase 2.
- Sleep timer, voice picker, word-level highlighting, offline downloads — phase 2 or later.
- Replacing Kavita's reader in any way. Echo has no reading UI, only a player.
- Public internet exposure. Home LAN + Tailscale only.

## 4. Prior Art (and why custom)

| Option | Verdict |
|---|---|
| [OpenReader](https://github.com/richardr1126/openreader) | Best-in-class TTS reader, but its own library + its own bookmark; doesn't know Kavita exists. Rejected because Kavita must stay the reader. Its sentence-segmentation and prefetch design is the reference model for Echo's audio pipeline. |
| Kavita native TTS ([discussion #3982](https://github.com/Kareadita/Kavita/discussions/3982)) | Doesn't exist; wishlist only. |
| Audiblez / epub2tts + Audiobookshelf | Pre-baked audiobooks, no position sync. Rejected. |
| Storyteller | Aligns *existing* audiobooks with ebooks. Wrong tool — no audio files owned. |

## 5. Architecture

```
┌─────────── phone ────────────┐
│  Kavita web reader (reading) │
│  Echo PWA player (listening) │──── lock-screen controls (Media Session)
└──────────────┬───────────────┘
               │ HTTP (LAN via reverse proxy, or Tailscale)
               ▼
┌────────────── the host ─ container: echo ────────────────┐
│  echo (FastAPI, port 7338)                               │
│   ├─ Kavita client: auth, progress read/write, EPUB pull │
│   ├─ EPUB text pipeline: parse → paragraphs → sentences  │
│   ├─ Position mapper: bookScrollId (XPath) ⇄ paragraph   │
│   ├─ Synthesis queue + warm-cache scheduler              │
│   ├─ Segment cache (disk, keyed audio segments)          │
│   └─ Player API + static PWA                             │
│                                                          │
│  kokoro (kokoro-fastapi-cpu, port 8880, internal only)   │
│   └─ OpenAI-compatible /v1/audio/speech                  │
└───────────────┬──────────────────────────────────────────┘
                │ Kavita REST API (x-api-key / JWT)
                ▼
        the Kavita server
        (source of truth: library, files, progress)
```

Design principles: Kavita is never modified — Echo is a pure API consumer. TTS is a swappable
OpenAI-compatible endpoint (Kokoro today; anything tomorrow). All state that matters (reading
progress) lives in Kavita; Echo's own DB is rebuildable cache/bookkeeping.

## 6. Resolved Design Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Buy vs build | Custom bridge | Nothing off-the-shelf syncs with Kavita position; Kavita stays the reader |
| D2 | Sync direction | Two-way | One continuous bookmark is the definition of "seamless" |
| D3 | Resume precision | Paragraph | Kavita's anchor is element-level; slight overlap aids re-entry; sentence-exact is guesswork about eye position |
| D4 | Playback client | PWA + Media Session | Phone-in-pocket with lock-screen controls covers the chores use case; casting deferred |
| D5 | TTS engine | Kokoro-FastAPI (CPU) | Free, local, faster-than-realtime on a modern CPU, robust to punctuation; pluggable interface regardless |
| D6 | Generation | Full-book pre-synthesis + on-demand progressive streaming | Active books synthesize to completion in background; play is instant; unprepped books stream progressively from current position |
| D7 | Scope | EPUB only | Clean text + reliable anchors; PDFs are a swamp |
| D8 | Users/access | Single user; LAN + Tailscale | Matches the rest of the home lab |
| D9 | Player features v1 | Speed 0.8–2.0×, ±paragraph skip | Sleep timer & voice picker → phase 2 |
| D10 | Stack/placement | Python 3.12 + FastAPI, Docker on a CPU host | Kokoro needs a reasonably fast CPU (the low-power NAS box wasn't enough); an existing service already proved the dev-machine → server deploy pattern |
| D11 | Name | Echo | short, and it fits "your book, echoed back as audio" |
| D12 *(v1.1)* | Synthesis scope | Entire book, not chapter-ahead | Storage is trivial (~150 MB/10-hr book); "queue while I read" becomes "done before dinner"; makes G1's 3 s target the norm |
| D13 *(v1.1)* | Retention | Keep everything by default | No LRU eviction, no post-read purge; ~50 GB soft cap with a warning; manual per-book delete in the PWA. Synthesized = kept |
| D14 *(v1.1)* | Playback artifact | One audio file per chapter + sentence→timestamp index | Synthesis stays per-sentence (cache/mapping unit), then concatenates per chapter. iOS treats playback like a normal podcast — de-risks R2 from "top risk" to routine; stepping stone to m4b export (phase 2) |

## 7. Kavita Integration

### 7.1 Authentication

- Use a Kavita **API key** → `POST /api/Plugin/authenticate?apiKey={key}&pluginName=Echo` →
  JWT; refresh on 401. API key stored in `.env`, never in the repo.

### 7.2 Endpoints used — **contract verified 2026-07-17 against live Kavita 0.9.0.2** (pinned version). Swagger UI is disabled on non-dev instances; the API reference is the published `openapi.json` from the Kavita repo — pin to the `v0.9.0.2` release tag, not `develop`. Re-run `spikes/03-kavita-contract/` on every Kavita upgrade.

| Purpose | Endpoint (expected) |
|---|---|
| Auth | `POST /api/Plugin/authenticate` |
| Active book detection | `GET /api/series/on-deck` (+ recently-updated progress) |
| Series/volume/chapter metadata | `GET /api/series/{id}`, `GET /api/series/volumes` |
| Read progress | `GET /api/reader/get-progress?chapterId={id}` |
| Write progress | `POST /api/reader/progress` (ProgressDto) |
| EPUB file | `GET /api/download/...` |

### 7.3 Kavita's EPUB progress model (the crux)

Kavita stores, per chapter: `pageNum` (its virtual pagination within the chapter, based on
`currentPage/totalPages`) and **`bookScrollId`** — an XPath to the top-visible element,
deliberately **descoped** from Kavita's UI DOM so it addresses the raw EPUB HTML directly.
This is Echo's paragraph anchor.

**Kavita's EPUB pagination — verified live on 0.9.0.2 (2026-07-17): `pageNum` IS the 0-based
spine index** (the UI shows pageNum+1); `bookScrollId` is an anchor *within that spine
document*, in one of two dialects: a descoped path XPath (`//body/p[7]`) or, when the
top-visible element has an `id` attribute, the function form `id("ch48")` (typical at chapter
headings). Both verified live; the mapper handles both. This makes both directions exact — no
proportional math.

**Read direction (Kavita → Echo):**
1. Fetch ProgressDto for the active chapter.
2. Spine item = `pageNum`. Resolve `bookScrollId` XPath within that document's HTML → element
   → nearest enclosing/following block element (`<p>`, `<h1..6>`, `<li>`, `<blockquote>`) →
   paragraph index.
3. Fallback if XPath missing/unresolvable: start of that spine item (pageNum already located
   the document; worst case = re-hearing the top of the current "page", which D3 overlap
   tolerates). Log every fallback (metric for mapper health).
4. Start synthesis/playback at that paragraph's first sentence.

**Write direction (Echo → Kavita):**
1. On segment completion, pause, and every 30 s of playback: current paragraph → compute that
   element's XPath in the raw spine-item HTML (same descoped form Kavita stores) →
   `bookScrollId`.
2. `pageNum` = the paragraph's spine index. Exact; sanity-check `totalPages` (Kavita's
   `pages`) equals Echo's spine count on book load — mismatch aborts write-back and alerts
   (R6 tripwire).
3. `POST /api/reader/progress` with volumeId/chapterId/pageNum/bookScrollId.
4. **Write-back safety rules:** never write a position *behind* Kavita's current one unless
   the user explicitly skipped back and kept listening ≥ 60 s; never write during active
   reading (if Kavita progress changed in the last 2 min from another source, Echo yields —
   reader wins).

Acceptance for the mapper: round-trip (Kavita position → Echo paragraph → written back →
reopened in Kavita) lands on the same screen for ≥ 95% of sampled positions across 5 test
books, including at least one light novel with images and one book with footnotes.

## 8. Text Pipeline

1. **Fetch:** Download EPUB from Kavita on first play/warm; cache the file (keyed by seriesId +
   chapterId + file mtime).
2. **Parse:** `ebooklib` + `lxml`/BeautifulSoup. Map Kavita chapters ↔ EPUB spine items via
   Kavita's chapter metadata (Kavita treats EPUB TOC entries as chapters; verify mapping on a
   real library during build — this is a named build-time task, not an assumption).
3. **Extract blocks:** Ordered list of block elements per chapter with: paragraph index, text,
   source XPath, char offsets. This table is the shared coordinate system between position
   mapping and audio.
4. **Content rules (v1):**
   - Read: paragraphs, headings (announce chapter/section titles), blockquotes, list items.
   - Skip: images (no alt-text reading), tables, code blocks, footnote *bodies*; strip inline
     footnote markers (`[1]`, superscripts).
   - Normalize for TTS: em/en dashes → comma-pause phrasing, smart quotes → plain, ellipses
     preserved, strip soft hyphens and mid-word line breaks. (This normalization layer is
     exactly what iOS's reader lacks; it gets its own test file of nasty light-novel
     typography.)
5. **Sentence segmentation:** Within each paragraph (`pysbd` or equivalent, abbreviation-aware).
   Segment unit = one sentence, merged to a 15–280 char window (tiny fragments merge forward;
   monster sentences split on clause boundaries) — this is the synthesis and cache unit.

## 9. Audio Pipeline

### 9.1 Synthesis

- Kokoro-FastAPI (CPU image), internal-only at `kokoro:8880`, OpenAI-compatible
  `POST /v1/audio/speech`.
- Default voice: `af_heart` (final pick by ear during build; one default voice in v1,
  config-level setting).
- Output: **MP3** (~29 MB/hr; a 10-hour book ≈ 290 MB). *Decided by spike 1 (2026-07-17):
  concatenated Ogg/Opus chunks play only the first stream in iOS Safari (rest is silence);
  concatenated MP3 plays perfectly — MP3 frames are self-contained, so chapter-file assembly
  is plain byte concatenation, no re-muxing.*

### 9.2 Audio store *(v1.1: per-chapter files, keep-everything)*

- Per-sentence segments are synthesized as the working unit, then **concatenated into one MP3
  file per chapter**: `{config}/audio/{bookId}/{voice}/{chapterId}.mp3`.
- SQLite index maps sentence ↔ paragraph ↔ char offsets ↔ **timestamp within chapter file** ↔
  duration. This index is the coordinate system for position mapping and seeking.
- Playback speed is client-side (`audio.playbackRate`) — **not** part of any key.
- **Retention: keep everything.** No LRU, no post-read purge. Soft cap 50 GB (config) → a
  warning as it approaches; manual per-book delete in the PWA. A fully synthesized book
  ≈ 150 MB — the store doubles as a permanent audiobook library.
- Voice note: changing `DEFAULT_VOICE` later means re-synthesizing kept books (files are
  per-voice); acceptable, disk is cheap.

### 9.3 Synthesis scheduler (full-book, background)

- Poll Kavita every 5 min for on-deck/progress changes.
- A book is **active** if its progress changed in the last 48 h.
- For active books: synthesize from current position → **end of book** (current chapter first,
  then forward), at low priority (bounded, niced worker pool; live playback requests preempt).
  Then backfill any earlier chapters.
- **"Prepare" button** per book in the PWA queues full-book synthesis for planned reads (books
  with no progress yet). No auto-warm of Kavita "Want to Read" (aspirational-shelf cruft;
  revisit phase 2 — see §16).
- Cold path (unprepped book): synthesize from current paragraph forward, streaming the growing
  chapter file progressively; playback starts as soon as the first seconds exist (≤ 8 s
  target).

## 10. Echo API (consumed by the PWA)

| Endpoint | Purpose |
|---|---|
| `GET /api/books` | Active/recent books w/ progress, cover (proxied from Kavita), cache status |
| `POST /api/books/{id}/prepare` | Force warm-cache |
| `GET /api/books/{id}/position` | Resolved position: chapter, paragraph idx, segment idx |
| `GET /api/books/{id}/chapters/{ch}/index` | Sentence→timestamp index (seek map, text snippets) |
| `GET /api/audio/{bookId}/{chapterId}.mp3` | Chapter audio file (range requests; progressive/chunked while synthesizing on miss) |
| `DELETE /api/books/{id}/audio` | Delete a book's stored audio (manual retention control) |
| `POST /api/books/{id}/progress` | Player reports playhead → triggers Kavita write-back rules |
| `GET /api/health` | Bridge + Kokoro + Kavita reachability |

Auth: single static bearer token in `.env`; the PWA stores it after first entry. (LAN/Tailscale
only; this is a seatbelt, not a vault.)

## 11. Player PWA

- Served by Echo at `/`. Installable (manifest + service worker for shell caching only — no
  offline audio in v1).
- Screens: **Book list** (active books, resume buttons, cache status) and **Player** (cover,
  chapter/paragraph indicator, current-sentence text, play/pause, ±paragraph skip, speed
  0.8–2.0× in 0.1 steps persisted in localStorage, scrub within chapter by paragraph).
- **Playback engine *(v1.1)*:** one `<audio>` element per chapter file; seek/skip via the
  sentence→timestamp index; next chapter preloaded near the end for a clean handoff. (Replaces
  the alternating-elements segment chain — per-chapter files made it unnecessary.)
- **Media Session API:** title/author/cover metadata; play/pause; `previoustrack`/`nexttrack`
  → ±paragraph. Works on the iOS lock screen.
- **iOS constraints (see R2, downgraded in v1.1):** first play must be a user gesture; keep
  one active audio pipeline to retain background-audio rights. Per-chapter files make this
  normal-podcast territory; the week-1 spike confirmed screen-locked playback ≥ 30 min
  including one chapter handoff, and settled Opus vs MP3.

## 12. Deployment & Ops

- Runs as a container (bridge networking, explicit ports — no `network_mode: host`, which is
  a known gotcha on macOS Docker hosts). The image is built from this repo; see the top-level
  `docker-compose.yml` and the `deploy/` example for running it under a stack manager.

```yaml
services:
  echo:
    image: echo:latest            # built from repo
    ports: ["7338:7338"]
    env_file: .env                # KAVITA_URL, KAVITA_API_KEY, ECHO_TOKEN, KOKORO_URL
    volumes:
      - ./data:/config            # cache + sqlite
    depends_on: [kokoro]
    restart: unless-stopped
  kokoro:
    image: ghcr.io/remsky/kokoro-fastapi-cpu:latest
    restart: unless-stopped       # internal-only preferred
```

- **Networking:** reach it on the LAN via a reverse proxy, or off-network over Tailscale.
  Host-specific DNS/proxy/IP details are deployment config, not part of the app.
- **Config:** `.env` — `KAVITA_URL`, `KAVITA_API_KEY`, `ECHO_TOKEN`, `KOKORO_URL`,
  `AUDIO_MAX_GB`, `POLL_MINUTES`, `DEFAULT_VOICE` (see `.env.example`).
- **Observability:** structured logs; `/api/health`; counters for XPath-fallback rate,
  synthesis realtime-factor, cache hit rate. Failure notifications (e.g. via ntfy) — "Kavita
  auth failing", "Kokoro down".

## 13. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | Kavita internal API changes across upgrades (progress DTO, auth) | Sync breaks | Pin tested Kavita version; contract-test suite against a live instance; health-check alerts before the user notices |
| R2 | iOS backgrounded PWA suspends audio | Primary UX dead on arrival | **Downgraded in v1.1:** per-chapter files (D14) ARE the old primary fallback — one long file per chapter is normal podcast playback. Spike ran week 1 (verify chapter handoff while locked; Opus vs MP3). Remaining fallbacks: Add-to-Home-Screen standalone mode; worst case, a trivial native shell later |
| R3 | XPath anchor fails on odd EPUBs (deep nesting, DRM-stripped weirdness, image-only pages) | Wrong resume point | Proportional-offset fallback + fallback-rate metric; per-book "reset position to Kavita" button |
| R4 | Kokoro CPU too slow under full-book load while other local models are busy | Buffering / slow prepare | **Measured 2026-07-17: 3.8× RTF single-stream, no parallel gain** (ONNX saturates cores per-request) → worker pool 1–2, playback preempts. Kokoro idle ≈ 1 GB RAM, ~0 CPU; load bursty per-book |
| R5 | Write-back corrupts reading progress | Trust destroyed | Conservative rules (§7.3), Echo logs every write with before/after, "undo last sync" endpoint |
| R6 | Chapter mapping mismatch (Kavita chapters vs EPUB spine) | Wrong chapter audio | Named build task with tests across a real library before the player is built |

## 14. Build Plan

**Phase 0 — Spikes: ✅ PASSED, GO (2026-07-17).** Results (details in `spikes/*/README.md`):
iOS locked-screen playback + Media Session controls + chapter handoff all work; **MP3 chosen**
(concatenated Ogg fails in Safari) · Kokoro: **3.8× RTF** single-stream, no parallel gain →
worker pool of 1–2; 10-hr book ≈ 2.6 hr prepare · Kavita 0.9.0.2 contract: auth, on-deck,
progress round-trip, EPUB download all pass; `bookScrollId` = `//body/p[5]` — exactly the
descoped XPath the mapper assumes.

**Phase 1 — MVP:** Kavita client + EPUB pipeline + position mapper (w/ round-trip tests) →
per-sentence synthesis + chapter-file assembly + progressive streaming endpoint → **full-book
scheduler + Prepare button (D12)** → PWA player (play/pause/speed/skip, Media Session) →
two-way sync with safety rules → deploy.

**Phase 1.5 — polish:** iOS Shortcuts "Resume" gesture (deep-link `/?play=latest` + autoplay
attempt in the PWA) · surface XPath-fallback rate + synthesis metrics in `/api/health` ·
failure notifications (§12).

**Phase 2 — Later:** **m4b/opus audiobook export with chapter markers** (drop into the library;
plays anywhere, no sync), sleep timer, voice picker, casting to smart speakers/displays, a
second user, auto-warm "Want to Read", TTRPG PDF exploration (separate spec if ever).

**Definition of done (v1):** read in Kavita on the phone → open Echo → play starts ≤ 3 s at
the right paragraph → lock phone, listen 30+ min through a chapter boundary with lock-screen
controls → open Kavita → land within one page of where audio stopped. Repeated across 5 books
without manual position fixing.

> **✅ Passed 2026-07-18** on the first book through the full loop: resume at position, seamless
> chapter handoff while locked, Kavita write-back landed within a page. Remaining for full
> acceptance: 4 more books, incl. one light novel with images and one with footnotes (§7.3).

## 15. Notes on publishing as an open repo

Written before release, kept as a record of the reasoning:

**Code hygiene (from day 1, not retroactively):** zero secrets or lab specifics in code or git
history — all config via env vars, ship `.env.example`; if anything leaks early, start a fresh
public repo rather than scrubbing history. Test fixtures use public-domain EPUBs only (Project
Gutenberg); never commit copyrighted book content, even snippets in test assertions.

**Release packaging:** MIT license (matches the OpenReader/Kavita ecosystem norms). A multi-arch
Docker image (amd64 + arm64) is worth publishing — the author's own host is arm64 but most
homelabbers run amd64, and single-arch kills adoption. Ship an example `docker-compose.yml`
bundling echo + kokoro (mirrors §12).

**Docs are most of the work:** a README with the problem statement, a short GIF of
read→listen→read, quick start, and this architecture; a setup guide (Kavita API key, Kokoro,
reverse proxy, Tailscale); a **Kavita compatibility table** stating tested versions explicitly
(Echo consumes internal-ish APIs — §13 R1); and troubleshooting for iOS PWA audio quirks (they
will be most of the issues filed).

**Why it's worth publishing:** it's not a todo-app clone — third-party API integration against
an undocumented surface, a text→audio streaming pipeline, cache/scheduler design, a PWA with
Media Session, and a written spec with explicit tradeoffs (this file). "Designed before built"
is rare and worth showing. Kavita TTS is also an open, unanswered feature request
([#3982](https://github.com/Kareadita/Kavita/discussions/3982)) — real demand, no incumbent.
The honest costs: issue triage, and the R1 treadmill (each Kavita release can break the
integration), which is why the README sets a plain "shared as-is, slow to respond" expectation.

## 16. Open Questions (punted, non-blocking)

1. ~~Opus-in-Ogg vs MP3 for iOS Safari `<audio>`~~ — *resolved by spike 1: MP3 (see §9.1).*
2. ~~Exact Kavita download endpoint & whether an SMB mount is simpler~~ — *resolved by spike 3:
   `GET /api/Download/chapter` works (returned the full EPUB); no SMB mount needed.*
3. Default Kokoro voice — pick by ear.
4. ~~Whether warm-cache should also pre-generate on "Want to Read" additions~~ — *resolved v1.1:
   no auto-warm; Prepare button covers planned reads. Revisit in phase 2 if the tap gets
   annoying.*
