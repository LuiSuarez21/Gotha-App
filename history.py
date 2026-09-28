"""Daily Cardmarket price history (last ~90 days), via MTGJSON.

MTGJSON publishes AllPrices daily (every card, 90 days of prices). Since the
file is large, it is downloaded at most once a day and reduced to a local
SQLite database holding only Cardmarket prices, keyed by Scryfall id.

The same database also keeps Cardmarket's free daily price guide (lowest
price, trend and 1/7/30-day averages), keyed by Cardmarket product id.

To keep the database small, each printing is stored as:
  - key: the Scryfall id as 16 raw bytes (instead of 36 characters of text);
  - value: zlib-compressed JSON {"start": date, "normal": [...], "foil": [...]},
    with one price per day (in cents, as integers) starting at "start".
"""
import csv
import json
import lzma
import sqlite3
import time
import uuid
import zlib
from datetime import date, timedelta
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).resolve().parent / "cache"
DB = CACHE_DIR / "prices.sqlite"
PRICES_URL = "https://mtgjson.com/api/v5/AllPrices.json.xz"
IDS_URL = "https://mtgjson.com/api/v5/csv/cardIdentifiers.csv.xz"
GUIDE_URL = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_1.json"
MAX_AGE_HOURS = 20  # MTGJSON and Cardmarket update once a day

# Price guide fields kept, in database column order ("-foil" is added for foil)
GUIDE_FIELDS = ("low", "trend", "avg1", "avg7", "avg30")


def _download(url, dest):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {dest.name}: {done * 100 // total:3d}%", end="", flush=True)
    print()


def _read_prices(path):
    """Walks through AllPrices.json.xz without loading it all into memory.

    Yields (mtgjson_uuid, {"normal": {...}, "foil": {...}}).
    """
    dec = json.JSONDecoder()
    with lzma.open(path, "rt", encoding="utf-8") as f:
        buf = f.read(1 << 20)
        pos = buf.index('"data":{') + len('"data":{')
        eof = False
        while True:
            if len(buf) - pos < (1 << 19) and not eof:
                more = f.read(1 << 22)
                eof = not more
                buf, pos = buf[pos:] + more, 0
            while buf[pos] in " ,\n\r\t":
                pos += 1
            if buf[pos] == "}":
                return
            key, pos = dec.raw_decode(buf, pos)
            pos = buf.index(":", pos) + 1
            value, pos = dec.raw_decode(buf, pos)
            retail = value.get("paper", {}).get("cardmarket", {}).get("retail")
            if retail:
                yield key, retail


def _pack(prices):
    """{"normal": {date: price}, "foil": {...}} -> compressed bytes."""
    days = [date.fromisoformat(d) for s in prices.values() for d in s]
    start = min(days).toordinal()
    length = max(days).toordinal() - start + 1
    packed = {"start": date.fromordinal(start).isoformat()}
    for kind, series in prices.items():
        cents = [None] * length
        for d, p in series.items():
            cents[date.fromisoformat(d).toordinal() - start] = round(p * 100)
        packed[kind] = cents
    return zlib.compress(json.dumps(packed, separators=(",", ":")).encode(), 9)


def _unpack(blob):
    """Compressed bytes -> {"normal": {date: price}, "foil": {...}}."""
    packed = json.loads(zlib.decompress(blob))
    start = date.fromisoformat(packed.pop("start")).toordinal()
    return {kind: {date.fromordinal(start + i).isoformat(): c / 100
                   for i, c in enumerate(cents) if c is not None}
            for kind, cents in packed.items()}


def _build():
    CACHE_DIR.mkdir(exist_ok=True)
    print("Updating price history (MTGJSON, once a day, ~2 min)...")
    ids_file = CACHE_DIR / "cardIdentifiers.csv.xz"
    prices_file = CACHE_DIR / "AllPrices.json.xz"
    _download(IDS_URL, ids_file)
    _download(PRICES_URL, prices_file)
    guide_file = CACHE_DIR / "price_guide.json"
    _download(GUIDE_URL, guide_file)

    print("  Processing...")
    with lzma.open(ids_file, "rt", encoding="utf-8", newline="") as f:
        uuid_to_scryfall = {row["uuid"]: row["scryfallId"]
                            for row in csv.DictReader(f) if row.get("scryfallId")}

    rows = {}
    for mtgjson_id, retail in _read_prices(prices_file):
        sid = uuid_to_scryfall.get(mtgjson_id)
        if not sid:
            continue
        # Several MTGJSON entries can point to the same Scryfall card
        current = rows.setdefault(sid, {})
        for kind in ("normal", "foil"):
            if retail.get(kind):
                current.setdefault(kind, {}).update(retail[kind])

    tmp = DB.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    # WITHOUT ROWID: the table is stored directly by its key, with no extra index
    con.execute("CREATE TABLE prices (scryfall_id BLOB PRIMARY KEY, data BLOB) WITHOUT ROWID")
    con.executemany("INSERT INTO prices VALUES (?, ?)",
                    ((uuid.UUID(k).bytes, _pack(v)) for k, v in rows.items() if v))
    _save_guide(con, guide_file)
    con.commit()
    con.close()
    tmp.replace(DB)
    prices_file.unlink(missing_ok=True)
    ids_file.unlink(missing_ok=True)
    guide_file.unlink(missing_ok=True)


def _save_guide(con, path):
    """Saves the Cardmarket price guide, with prices in cents (0 = no data)."""
    guide = json.loads(path.read_text(encoding="utf-8"))
    cols = [f + suffix for suffix in ("", "-foil") for f in GUIDE_FIELDS]
    con.execute(f"CREATE TABLE guide (id_product INTEGER PRIMARY KEY, "
                f"{', '.join(c.replace('-', '_') + ' INTEGER' for c in cols)})")
    con.executemany(f"INSERT INTO guide VALUES ({', '.join('?' * (len(cols) + 1))})",
                    ([g["idProduct"]] + [round(g[c] * 100) if g.get(c) else None for c in cols]
                     for g in guide["priceGuides"]))
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('guide_date', ?)", (guide["createdAt"][:10],))


class History:
    def __init__(self):
        age = (time.time() - DB.stat().st_mtime) / 3600 if DB.exists() else None
        if age is None or age > MAX_AGE_HOURS or not _has_guide():
            try:
                _build()
            except (requests.RequestException, OSError, ValueError) as e:
                if not DB.exists():
                    raise
                print(f"Warning: update failed ({e}); using cached data.")
        self.con = sqlite3.connect(DB)
        row = self.con.execute("SELECT value FROM meta WHERE key = 'guide_date'").fetchone()
        self.guide_date = row[0] if row else None

    def get(self, scryfall_id):
        """{"normal": {date: price}, "foil": {...}} for one printing (or {})."""
        r = self.con.execute("SELECT data FROM prices WHERE scryfall_id = ?",
                             (uuid.UUID(scryfall_id).bytes,)).fetchone()
        return _unpack(r[0]) if r else {}


    def guide(self, cardmarket_id):
        """Today's price guide for one Cardmarket product (or {}).

        {"normal": {"low": ..., "trend": ..., "avg1": ..., "avg7": ..., "avg30": ...}, "foil": {...}}
        """
        if not cardmarket_id:
            return {}
        r = self.con.execute("SELECT * FROM guide WHERE id_product = ?",
                             (cardmarket_id,)).fetchone()
        if not r:
            return {}
        n = len(GUIDE_FIELDS)
        return {kind: {f: (c / 100 if c else None) for f, c in zip(GUIDE_FIELDS, values)}
                for kind, values in (("normal", r[1:n + 1]), ("foil", r[n + 1:]))}


def _has_guide():
    """True if the cache already has the price guide (older caches don't)."""
    if not DB.exists():
        return False
    con = sqlite3.connect(DB)
    try:
        return bool(con.execute("SELECT 1 FROM sqlite_master WHERE name = 'guide'").fetchone())
    finally:
        con.close()


def price_on(series, days_ago, reference):
    """Price `days_ago` days before `reference` (or the last known one before that date)."""
    if not series:
        return None
    target = (date.fromisoformat(reference) - timedelta(days=days_ago)).isoformat()
    dates = [d for d in series if d <= target]
    return series[max(dates)] if dates else None
