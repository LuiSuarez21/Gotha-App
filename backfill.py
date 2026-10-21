#!/usr/bin/env python3
"""
Fills Gotha's sealed price history with past days, from archived copies of
Cardmarket's daily price guide (the same free file Gotha downloads each day).

Gotha only knows the sealed prices of the days it ran, and nobody publishes
sealed price history for free. But some people saved the daily price guide:

  - cusco/cardmarket_prices (https://github.com/cusco/cardmarket_prices)
    shares its MTG copies at https://ovh.tretas.eu/~cusco/catalogs/
    (~280 days in 2025-2026, one ~2.5 MB file per day);
  - the Internet Archive's Wayback Machine has a few copies.

Each day found is imported once into data/sealed_history.sqlite (only the
sealed products; days Gotha already has are skipped). It's a one-time job:
the files are downloaded one at a time, with a pause between them, because
the archive is someone's personal server.

Usage:
    python backfill.py                     (import every missing day)
    python backfill.py --since 2026-06-01  (only days from this date on)
    python backfill.py --list              (show what would be downloaded)
"""
import gzip
import json
import re
import sqlite3
import sys
import time

import requests

import catalog
import sealed

ARCHIVE = "https://ovh.tretas.eu/~cusco/catalogs/"
WAYBACK_CDX = ("https://web.archive.org/cdx/search/cdx?url=downloads.s3.cardmarket.com/"
               "productCatalog/priceGuide/price_guide_1.json&output=json&fl=timestamp,digest"
               "&filter=statuscode:200&collapse=digest")
WAYBACK_FILE = ("https://web.archive.org/web/{}id_/https://downloads.s3.cardmarket.com/"
                "productCatalog/priceGuide/price_guide_1.json")
HEADERS = {"User-Agent": "Gotha MTG price tracker (personal use, one-time history import)"}
PAUSE = 1.0  # seconds between downloads


def archive_files():
    """[(day, url)] from the cusco archive: one file per distinct content, oldest first.

    The archive saves the guide twice a day, so the same content (same md5 in
    the name) shows up under two dates; the earliest is the day it belongs to.
    """
    try:
        r = requests.get(ARCHIVE, headers=HEADERS, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"Archive not available ({e}); skipping it.")
        return []
    first = {}
    for name, day, md5 in re.findall(
            r'href="((\d{4}-\d{2}-\d{2})_([0-9a-f]{32})_price_guide_1\.json\.gz)"', r.text):
        if md5 not in first or day < first[md5][0]:
            first[md5] = (day, ARCHIVE + name)
    return sorted(first.values())


def wayback_files():
    """[(day, url)] of the Wayback Machine's copies (empty if it can't be reached)."""
    try:
        rows = requests.get(WAYBACK_CDX, headers=HEADERS, timeout=60).json()[1:]
    except (requests.RequestException, ValueError) as e:
        print(f"Wayback Machine not available ({e}); skipping it.")
        return []
    return [(f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}", WAYBACK_FILE.format(ts)) for ts, _ in rows]


def sealed_ids():
    """Ids of the sealed products, read from the search index without rebuilding it
    (a running server.py keeps it open, and Windows can't replace an open file)."""
    if catalog.DB.exists():
        con = sqlite3.connect(catalog.DB)
        try:
            ids = [r[0] for r in con.execute("SELECT ref FROM entries WHERE kind = 'sealed'")]
        except sqlite3.OperationalError:
            ids = []
        finally:
            con.close()
        if ids:
            return ids
    return catalog.Catalog().sealed_ids()


def download(url):
    r = requests.get(url, headers=HEADERS, timeout=120)
    r.raise_for_status()
    data = r.content
    if data[:2] == b"\x1f\x8b":  # gzip
        data = gzip.decompress(data)
    return json.loads(data)


def main():
    args = sys.argv[1:]
    since = args[args.index("--since") + 1] if "--since" in args else "0000"
    tracker = sealed.Tracker()
    have = tracker.days()

    print("Listing the archived price guides...")
    files = {}
    for day, url in wayback_files() + archive_files():
        files.setdefault(day, url)  # one source per day is enough
    todo = [(d, u) for d, u in sorted(files.items()) if d >= since and d not in have]
    print(f"{len(files)} archived day(s) found, {len(have)} already recorded, "
          f"{len(todo)} to import (~{len(todo) * 2.5:.0f} MB to download).")
    if "--list" in args or not todo:
        for day, url in todo:
            print(f"  {day}  {url}")
        return

    ids = sealed_ids()
    added = 0
    for i, (day, url) in enumerate(todo, 1):
        try:
            guide = download(url)
            n = tracker.import_guide(guide, ids)
        except (requests.RequestException, ValueError, KeyError, OSError) as e:
            print(f"[{i}/{len(todo)}] {day}: failed ({e}); skipped")
            continue
        real_day = guide["createdAt"][:10]
        note = "" if real_day == day else f" (guide of {real_day})"
        print(f"[{i}/{len(todo)}] {day}{note}: "
              + (f"{n} sealed prices" if n else "already recorded"))
        added += n > 0
        time.sleep(PAUSE)
    print(f"Done: {added} day(s) added; {tracker.days_recorded()} day(s) of sealed history now. "
          "The weekly changes in the search use them from the next daily index update.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. The days imported so far are kept; run it again to continue.")
