#!/usr/bin/env python3
"""Spike 3 (R1, R5): Kavita API contract check.

Verifies every endpoint Echo depends on, against the LIVE Kavita instance.
SAFE BY DESIGN: the only write re-posts a chapter's progress with the exact
values just read (a no-op), then re-reads to confirm the round-trip.

Usage:
    pip3 install requests --break-system-packages
    KAVITA_URL=http://YOUR_KAVITA:5000 KAVITA_API_KEY=<key> python3 check_kavita.py

Paste back the full output. Also produces swagger.json in this directory.
"""

import json
import os
import sys

import requests

BASE = os.environ.get("KAVITA_URL", "http://localhost:5000").rstrip("/")
KEY = os.environ.get("KAVITA_API_KEY")
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def main():
    if not KEY:
        sys.exit("Set KAVITA_API_KEY (Kavita → Settings → API key). Never commit it.")

    print(f"Kavita contract check → {BASE}\n")

    # 1. Auth
    print("1. Plugin auth")
    r = requests.post(f"{BASE}/api/Plugin/authenticate",
                      params={"apiKey": KEY, "pluginName": "Echo"}, timeout=15)
    if not check("POST /api/Plugin/authenticate", r.ok, f"HTTP {r.status_code}"):
        sys.exit("\nAuth failed — nothing else can run.")
    token = r.json().get("token")
    check("JWT present in response", bool(token))
    H = {"Authorization": f"Bearer {token}"}

    # 2. Server version (pins R1 contract tests)
    print("2. Server version")
    r = requests.get(f"{BASE}/api/Server/server-info-slim", headers=H, timeout=15)
    if not r.ok:  # older/newer Kavita
        r = requests.get(f"{BASE}/api/Server/server-info", headers=H, timeout=15)
    ver = r.json().get("kavitaVersion", "?") if r.ok else "?"
    check("server info", r.ok, f"Kavita version: {ver}")

    # 3. Swagger dump (for offline API diffing during build)
    print("3. Swagger")
    ok = False
    for path in ("/swagger/v1/swagger.json", "/api/swagger/v1/swagger.json"):
        r = requests.get(BASE + path, timeout=15)
        if r.ok and r.headers.get("content-type", "").startswith("application/json"):
            with open(os.path.join(os.path.dirname(__file__) or ".", "swagger.json"), "w") as f:
                f.write(r.text)
            ok = True
            break
    check("swagger.json downloaded", ok, "saved next to this script" if ok else
          "not exposed — check if swagger is enabled in Kavita settings")

    # 4. On-deck (active book detection)
    print("4. On-deck")
    r = requests.post(f"{BASE}/api/Series/on-deck", headers=H,
                      params={"libraryId": 0}, json={}, timeout=15)
    if not r.ok:
        r = requests.get(f"{BASE}/api/Series/on-deck", headers=H, timeout=15)
    ondeck = r.json() if r.ok else []
    check("on-deck reachable", r.ok, f"{len(ondeck)} series on deck")
    if not ondeck:
        print("     (empty on-deck — open any book in Kavita, read a page, rerun)")
        report_and_exit()

    series = ondeck[0]
    sid = series.get("id")
    print(f"     using series: {series.get('name', '?')!r} (id={sid})")

    # 5. Volumes/chapters metadata
    print("5. Series structure")
    r = requests.get(f"{BASE}/api/Series/volumes", headers=H,
                     params={"seriesId": sid}, timeout=15)
    vols = r.json() if r.ok else []
    chapters = [c for v in vols for c in v.get("chapters", [])]
    check("volumes+chapters", r.ok and bool(chapters),
          f"{len(vols)} volumes, {len(chapters)} chapters")
    if not chapters:
        report_and_exit()
    ch = chapters[0]
    chid, vid = ch.get("id"), vols[0].get("id")

    # 6. Progress read — THE crux: does bookScrollId exist and look like XPath?
    print("6. Progress read (the crux)")
    r = requests.get(f"{BASE}/api/Reader/get-progress", headers=H,
                     params={"chapterId": chid}, timeout=15)
    prog = r.json() if r.ok else {}
    check("GET get-progress", r.ok, json.dumps(prog)[:200])
    bsid = prog.get("bookScrollId")
    check("bookScrollId present", bsid is not None,
          f"{bsid!r}" if bsid else "null — read a page of an EPUB in the web reader, rerun")
    if bsid:
        check("bookScrollId looks like XPath", str(bsid).startswith("/"), str(bsid)[:120])

    # 7. Progress write round-trip — re-POST identical values (no-op), re-read, compare
    print("7. Progress write round-trip (no-op write of identical values)")
    payload = {
        "volumeId": prog.get("volumeId", vid),
        "chapterId": prog.get("chapterId", chid),
        "pageNum": prog.get("pageNum", 0),
        "seriesId": prog.get("seriesId", sid),
        "libraryId": prog.get("libraryId", series.get("libraryId")),
        "bookScrollId": prog.get("bookScrollId"),
    }
    r = requests.post(f"{BASE}/api/Reader/progress", headers=H, json=payload, timeout=15)
    check("POST /api/Reader/progress", r.ok, f"HTTP {r.status_code}")
    r = requests.get(f"{BASE}/api/Reader/get-progress", headers=H,
                     params={"chapterId": chid}, timeout=15)
    prog2 = r.json() if r.ok else {}
    check("round-trip: pageNum unchanged", prog2.get("pageNum") == prog.get("pageNum"),
          f"{prog.get('pageNum')} → {prog2.get('pageNum')}")
    check("round-trip: bookScrollId unchanged",
          prog2.get("bookScrollId") == prog.get("bookScrollId"))

    # 8. EPUB download endpoint exists (HEAD-ish probe via swagger knowledge is build-time;
    #    here just confirm the chapter download route responds)
    print("8. EPUB access probe")
    r = requests.get(f"{BASE}/api/Download/chapter", headers=H,
                     params={"chapterId": chid, "seriesId": sid}, timeout=30, stream=True)
    size = r.headers.get("content-length", "?")
    check("GET /api/Download/chapter", r.ok, f"HTTP {r.status_code}, {size} bytes")
    r.close()

    report_and_exit()


def report_and_exit():
    total, passed = len(results), sum(results)
    print(f"\n{'=' * 40}\n{passed}/{total} checks passed — "
          + ("GO" if passed == total else "review failures before Phase 1"))
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
