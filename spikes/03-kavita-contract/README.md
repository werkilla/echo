# Spike 3 — Kavita API contract check (R1, R5)

Verifies every Kavita endpoint Echo depends on, against your live instance. Safe: the only write re-posts identical progress values (a no-op) and confirms the round-trip.

## Run

```bash
pip3 install requests --break-system-packages
cd spikes/03-kavita-contract
KAVITA_URL=http://YOUR_KAVITA:5000 KAVITA_API_KEY=<your-key> python3 check_kavita.py
```

API key: Kavita → user settings (top right) → API key. Don't commit it, don't paste it in chat — the script output doesn't echo it.

Before running: open an EPUB in Kavita's web reader and read at least one page, so `get-progress` returns a real `bookScrollId`.

## What to paste back

Full output. It includes your Kavita version (pins the contract tests) and whether `bookScrollId` looks like the XPath the position mapper expects — the single most important line of all three spikes. The script also saves the server's `swagger.json` locally (gitignored) for offline API diffing.

### Result

```
Kavita contract check → http://YOUR_KAVITA:5000

1. Plugin auth
  [PASS] POST /api/Plugin/authenticate — HTTP 200
  [PASS] JWT present in response
2. Server version
  [PASS] server info — Kavita version: 0.9.0.2
3. Swagger
  [FAIL] swagger.json downloaded — not exposed — check if swagger is enabled in Kavita settings
4. On-deck
  [PASS] on-deck reachable — 1 series on deck
     using series: 'A Court of Mist and Fury' (id=610)
5. Series structure
  [PASS] volumes+chapters — 1 volumes, 1 chapters
6. Progress read (the crux)
  [PASS] GET get-progress — {"volumeId": 918, "chapterId": 1868, "pageNum": 58, "seriesId": 610, "libraryId": 1, "bookScrollId": "//body/p[5]", "lastModifiedUtc": "2026-07-17T03:04:35.989378"}
  [PASS] bookScrollId present — '//body/p[5]'
  [PASS] bookScrollId looks like XPath — //body/p[5]
7. Progress write round-trip (no-op write of identical values)
  [PASS] POST /api/Reader/progress — HTTP 200
  [PASS] round-trip: pageNum unchanged — 58 → 58
  [PASS] round-trip: bookScrollId unchanged
8. EPUB access probe
  [PASS] GET /api/Download/chapter — HTTP 200, 7155078 bytes

========================================
12/13 checks passed — review failures before Phase 1
```

---

Re the swagger.json. Found this online:
Kavita’s REST API is documented using Swagger, however, the UI is disabled for non-dev instances. You can see Kavita’s API Docs on the main Kavita website.
https://www.kavitareader.com/docs/api/#/
Downloaded manually from: https://raw.githubusercontent.com/Kareadita/Kavita/develop/openapi.json