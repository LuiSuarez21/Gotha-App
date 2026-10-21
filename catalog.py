"""Local search index of every Magic product on Cardmarket (cards and sealed).

Built once a day from three free files:
  - Cardmarket's product lists (singles and non-singles: boosters, boxes,
    bundles, decks...), with the product id, name, category and expansion id;
  - MTGJSON's SetList, which gives each Cardmarket expansion id a set code,
    name, type and release date;
  - the price guide already stored in cache/prices.sqlite (see history.py).

Cards are grouped by Cardmarket's "metacard" id (one entry per card, with all
its printings); sealed products are one entry each. The names are indexed
with SQLite FTS5's trigram tokenizer, which finds any substring in a few
milliseconds and gives candidates for typo-tolerant (fuzzy) matching.

Each entry also gets a weekly price change, used by the "biggest gainers /
droppers" sorts: for cards, the 7-day vs the 30-day average sale price of
the cheapest printing; for sealed products (the guide has no averages for
them), today's price vs the price Gotha recorded at least a week ago.
"""
import json
import math
import re
import sqlite3
import time
import unicodedata
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import requests

from history import CACHE_DIR, DB as PRICES_DB, MAX_AGE_HOURS, _download
from sealed import DB as SEALED_DB

DB = CACHE_DIR / "catalog.sqlite"
LISTS = "https://downloads.s3.cardmarket.com/productCatalog/productList/"
SINGLES_URL = LISTS + "products_singles_1.json"
SEALED_URL = LISTS + "products_nonsingles_1.json"
SETS_URL = "https://mtgjson.com/api/v5/SetList.json"
SCRYFALL_SETS = "https://api.scryfall.com/sets"
SCRYFALL_HEADERS = {"User-Agent": "MTGPrices/0.1", "Accept": "application/json"}  # Scryfall asks for both
VERSION = "5"  # bump when the table layout changes, to force a rebuild

# Cardmarket category -> short label shown in the app
CATEGORIES = {
    "Magic Display": "Booster box",
    "Magic Booster": "Booster",
    "Magic Fatpack": "Bundle",
    "MtG Set": "Set / Secret Lair",
    "Magic Intropack": "Deck",
    "Magic Starter Deck": "Starter deck",
    "Magic Theme Deck Display": "Deck display",
    "Magic TournamentPack": "Tournament pack",
    "Magic Lot": "Lot",
    "Magic Event Tickets": "Event ticket",
    "Magic Miscellaneous": "Other",
}

SORTS = {
    "price_asc": "price IS NULL, price",
    "price_desc": "price IS NULL, price DESC",
    "change_desc": "change IS NULL, change DESC",  # biggest gainers
    "change_asc": "change IS NULL, change",  # biggest droppers
    "newest": "released IS NULL, released DESC",
    "name": "norm",
}

# Normalised-name patterns of products that aren't playable cards or real sets
EXTRAS = ["% token %", "% token", "art series %", "% online code%", "% arena code%",
          "magic online %", "arena code %", "rules tip%", "% helper card%"]


def norm(text):
    """Lower case, no accents, only letters/digits separated by single spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _build(guide_db, sealed_db):
    CACHE_DIR.mkdir(exist_ok=True)
    print("Updating the search index (Cardmarket product lists, once a day)...")
    files = {url: CACHE_DIR / url.rsplit("/", 1)[1] for url in (SINGLES_URL, SEALED_URL, SETS_URL)}
    for url, path in files.items():
        _download(url, path)

    def read(url):
        return json.loads(files[url].read_text(encoding="utf-8"))

    singles = read(SINGLES_URL)["products"]
    sealed = read(SEALED_URL)["products"]
    sets = read(SETS_URL)["data"]
    for p in sealed:
        # Some names come with CSV-style doubled quotes: Deck ""Peace Offering""
        p["name"] = p["name"].replace('""', '"')

    tmp = DB.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript("""
        CREATE TABLE expansions (id INTEGER PRIMARY KEY, code TEXT, name TEXT,
                                 released TEXT, type TEXT, icon TEXT);
        CREATE TABLE singles (id_product INTEGER PRIMARY KEY, id_metacard INTEGER,
                              id_expansion INTEGER, name TEXT);
        CREATE INDEX singles_card ON singles (id_metacard);
        CREATE INDEX singles_exp ON singles (id_expansion);
        CREATE TABLE entries (id INTEGER PRIMARY KEY, kind TEXT, ref INTEGER, name TEXT,
                              norm TEXT, category TEXT, id_expansion INTEGER,
                              printings INTEGER, price REAL, released TEXT, change REAL,
                              tcgplayer_id INTEGER);
        CREATE TABLE upcoming (code TEXT, name TEXT, released TEXT, type TEXT);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """)

    # Expansions: MTGJSON knows the Cardmarket id of most of them (and of their "Extras")
    exp = {}
    for s in sets:
        for key, suffix in (("mcmId", ""), ("mcmIdExtras", ": Extras")):
            if s.get(key):
                exp[s[key]] = (s["code"], (s.get("mcmName") or s["name"]) + suffix,
                               s["releaseDate"], s["type"])
    # The rest: name unknown, release date ~ the first time Cardmarket listed it
    first_added = {}
    for p in singles + sealed:
        day = p["dateAdded"][:10]
        if day > "2007-01-01":  # 2007-01-01 is a placeholder for old products
            first_added[p["idExpansion"]] = min(day, first_added.get(p["idExpansion"], day))
    # The rest: guess the name from the sealed products' names ("Star Trek Play Booster
    # Box" + "Star Trek Collector Booster Box" -> "Star Trek") and look it up by name
    by_name = {norm(s["name"]): s for s in sets if not s.get("mcmId")}
    for eid in {p["idExpansion"] for p in singles + sealed} - exp.keys():
        name = _common_name([p["name"] for p in sealed if p["idExpansion"] == eid])
        s = by_name.get(norm(name)) if name else None
        exp[eid] = ((s["code"], s["name"], s["releaseDate"], s["type"]) if s else
                    (None, name or f"Expansion #{eid}", first_added.get(eid), None))
    icons = _set_icons()
    con.executemany("INSERT INTO expansions VALUES (?, ?, ?, ?, ?, ?)",
                    ((k, *v, icons.get(v[0])) for k, v in exp.items()))

    # Product pictures: MTGJSON links many sealed products to their TCGplayer
    # product id, and TCGplayer has a picture for every product id
    tcgplayer = {}
    for s in sets:
        for sp in s.get("sealedProduct") or []:
            ids = sp.get("identifiers") or {}
            mcm, tcg = str(ids.get("mcmId", "")), str(ids.get("tcgplayerProductId", ""))
            if mcm.isdigit() and tcg.isdigit():
                tcgplayer[int(mcm)] = int(tcg)

    today = time.strftime("%Y-%m-%d")
    con.executemany("INSERT INTO upcoming VALUES (?, ?, ?, ?)",
                    ((s["code"], s["name"], s["releaseDate"], s["type"])
                     for s in sets if s["releaseDate"] > today and not s.get("isOnlineOnly")))

    # Today's price of every product (trend, or the cheapest listing if no trend),
    # and the weekly change of the 7-day vs the 30-day average sale price
    price, change = {}, {}
    if guide_db.exists():
        g = sqlite3.connect(guide_db)
        for pid, low, trend, avg7, avg30 in g.execute(
                "SELECT id_product, low, trend, avg7, avg30 FROM guide"):
            if trend or low:
                price[pid] = (trend or low) / 100
            if avg7 and avg30:
                change[pid] = round(avg7 / avg30 - 1, 4)
        g.close()
    # Sealed products have no averages in the guide: use Gotha's own recorded prices
    change |= _recorded_change(sealed_db, price, {p["idProduct"] for p in sealed})

    con.executemany("INSERT INTO singles VALUES (?, ?, ?, ?)",
                    ((p["idProduct"], p["idMetacard"], p["idExpansion"], p["name"]) for p in singles))

    # One entry per card (all printings together)...
    cards = {}
    for p in singles:
        c = cards.setdefault(p["idMetacard"], {"name": p["name"], "prints": [], "prices": []})
        c["prints"].append(p)
        if p["idProduct"] in price:
            c["prices"].append((price[p["idProduct"]], p["idProduct"]))
    rows = []
    for mid, c in cards.items():
        latest = max(c["prints"], key=lambda p: exp[p["idExpansion"]][2] or "")
        # The entry shows the cheapest printing's price, so it shows that printing's change too
        cheapest, pid = min(c["prices"]) if c["prices"] else (None, None)
        rows.append(("card", mid, c["name"], norm(c["name"]), None, latest["idExpansion"],
                     len(c["prints"]), cheapest, exp[latest["idExpansion"]][2], change.get(pid),
                     None))
    # ...and one per sealed product
    for p in sealed:
        rows.append(("sealed", p["idProduct"], p["name"], norm(p["name"]),
                     CATEGORIES.get(p["categoryName"], p["categoryName"]), p["idExpansion"],
                     1, price.get(p["idProduct"]), exp[p["idExpansion"]][2],
                     change.get(p["idProduct"]), tcgplayer.get(p["idProduct"])))
    con.executemany("INSERT INTO entries (kind, ref, name, norm, category, id_expansion, printings, "
                    "price, released, change, tcgplayer_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows)

    # Full-text index on the names (falls back to plain LIKE if FTS5/trigram is missing)
    try:
        con.execute("CREATE VIRTUAL TABLE entries_fts USING fts5(norm, content='entries', "
                    "content_rowid='id', tokenize='trigram')")
        con.execute("INSERT INTO entries_fts (rowid, norm) SELECT id, norm FROM entries")
    except sqlite3.OperationalError as e:
        print(f"  (no FTS5 trigram support in this SQLite: {e}; search will be slower)")
    con.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("version", VERSION), ("built", time.strftime("%Y-%m-%d %H:%M")),
        ("products", str(len(singles) + len(sealed)))])
    con.commit()
    con.close()
    tmp.replace(DB)
    for path in files.values():
        path.unlink(missing_ok=True)


def _set_icons():
    """Set code -> URL of the set's symbol (from Scryfall); {} if Scryfall can't be reached.

    Promo and Secret Lair sets get their parent's symbol (PBLB -> blb.svg, SLD -> star.svg).
    """
    try:
        r = requests.get(SCRYFALL_SETS, headers=SCRYFALL_HEADERS, timeout=30)
        r.raise_for_status()
        return {s["code"].upper(): s["icon_svg_uri"].split("?")[0]
                for s in r.json()["data"] if s.get("icon_svg_uri")}
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"  (no set symbols this time: {e})")
        return {}


def _recorded_change(path, price, ids, days=7):
    """Change of today's price vs the latest price Gotha recorded at least `days` ago.

    Uses data/sealed_history.sqlite (see sealed.py); {} if there is no such day yet.
    """
    if not Path(path).exists():
        return {}
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT id_product, trend, low FROM history WHERE day <= ? "
                           "ORDER BY day", (cutoff,)).fetchall()
    except sqlite3.OperationalError:  # no history table yet
        return {}
    finally:
        con.close()
    before = {pid: (trend or low) / 100 for pid, trend, low in rows if trend or low}  # latest day wins
    return {pid: round(price[pid] / old - 1, 4) for pid, old in before.items()
            if pid in ids and pid in price}


PRODUCT_WORDS = {"booster", "box", "display", "pack", "bundle", "fat", "collector", "play",
                 "draft", "set", "jumpstart", "prerelease", "commander", "deck", "case",
                 "starter", "kit", "gift", "edition", "theme", "beginner", "sleeved"}


def _common_name(names):
    """Common leading words of a set's sealed product names, without product words."""
    if not names:
        return None
    lists = [n.replace(":", " ").split() for n in names]
    words = []
    for group in zip(*lists):
        if len({w.lower() for w in group}) > 1:
            break
        words.append(group[0])
    while words and words[-1].lower() in PRODUCT_WORDS | {"-", "|", "&"}:
        words.pop()
    return " ".join(words) or None


class Catalog:
    def __init__(self, guide_db=PRICES_DB, sealed_db=SEALED_DB):
        age = (time.time() - DB.stat().st_mtime) / 3600 if DB.exists() else None
        if age is None or age > MAX_AGE_HOURS or _version() != VERSION:
            try:
                _build(guide_db, sealed_db)
            except (requests.RequestException, OSError, ValueError, KeyError) as e:
                if not DB.exists():
                    raise
                if _version() != VERSION:  # the cached index has an older layout: unusable
                    raise RuntimeError(
                        f"The search index must be rebuilt for this version of Gotha, but that "
                        f"failed ({e}). If another Gotha window (server.py) is open, close it "
                        "and try again.") from e
                print(f"Warning: search index update failed ({e}); using the cached one.")
        # The app server answers each request in its own thread
        self.con = sqlite3.connect(DB, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.meta = dict(self.con.execute("SELECT key, value FROM meta"))
        self.fts = bool(self.con.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'entries_fts'").fetchone())
        self._set_list = None

    # ---------- search ----------

    def search(self, q="", kind=None, category=None, set_code=None, min_price=None,
               max_price=None, sort="relevance", limit=30, offset=0, no_extras=False):
        """Ranked search with filters, one page at a time.

        Returns {"results", "total", "offset", "next_offset", "facets", "sets", "ms"}:
        `next_offset` is None on the last page, and `sets` lists the sets whose
        name or code matches the text (first page only). `no_extras` leaves out
        tokens, Art Series, online codes and rules tips (used by the trending lists).
        """
        t0 = time.perf_counter()
        offset = max(0, offset)
        nq = norm(q or "")
        where, params = [], []
        if no_extras:
            where.append("NOT (" + " OR ".join("e.norm LIKE ?" for _ in EXTRAS) + ")")
            params += EXTRAS
        if category:
            where.append("e.category = ?")
            params.append(category)
        if set_code:
            # A card matches if any of its printings is in that set
            where.append("(e.id_expansion IN (SELECT id FROM expansions WHERE code = ?) OR "
                         "(e.kind = 'card' AND e.ref IN (SELECT s.id_metacard FROM singles s "
                         "JOIN expansions x ON x.id = s.id_expansion WHERE x.code = ?)))")
            params += [set_code.upper(), set_code.upper()]
        if min_price is not None:
            where.append("e.price >= ?")
            params.append(min_price)
        if max_price is not None:
            where.append("e.price <= ?")
            params.append(max_price)

        if nq:
            rows = self._candidates(nq, where, params)
            in_set = self._set_code_hits(nq, where, params)
            seen = {r["id"] for r in rows}
            rows += [r for r in in_set.values() if r["id"] not in seen]
            scored = [(s, r) for r in rows
                      if (s := score(nq, r, r["id"] in in_set)) is not None]

            # Facets (counts per kind / category) before the kind filter is applied
            facets = {"kind": {}, "category": {}}
            for _, r in scored:
                facets["kind"][r["kind"]] = facets["kind"].get(r["kind"], 0) + 1
                if r["category"]:
                    facets["category"][r["category"]] = facets["category"].get(r["category"], 0) + 1
            if kind in ("card", "sealed"):
                scored = [x for x in scored if x[1]["kind"] == kind]

            if sort in SORTS:
                def missing_last(field, sign=1):
                    return lambda x: (x[1][field] is None, sign * (x[1][field] or 0))
                key = {"price_asc": missing_last("price"),
                       "price_desc": missing_last("price", -1),
                       "change_desc": missing_last("change", -1),
                       "change_asc": missing_last("change"),
                       "newest": lambda x: x[1]["released"] or "",
                       "name": lambda x: x[1]["norm"]}[sort]
                scored.sort(key=key, reverse=sort == "newest")
            else:
                scored.sort(key=lambda x: -x[0])
            total = len(scored)
            page = scored[offset:offset + limit]
        else:
            # No text: browse everything that matches the filters, paged by SQLite
            filters = " WHERE " + " AND ".join(where) if where else ""
            facets = {"kind": dict(self.con.execute(
                f"SELECT e.kind, COUNT(*) FROM entries e{filters} GROUP BY e.kind", params)),
                "category": dict(self.con.execute(
                    f"SELECT e.category, COUNT(*) FROM entries e{filters}"
                    f"{' AND' if where else ' WHERE'} e.category IS NOT NULL GROUP BY e.category",
                    params))}
            if kind in ("card", "sealed"):
                where, params = where + ["e.kind = ?"], params + [kind]
                filters = " WHERE " + " AND ".join(where)
            total = (facets["kind"].get(kind, 0) if kind in ("card", "sealed")
                     else sum(facets["kind"].values()))
            # e.id last keeps the order stable from one page to the next
            order = SORTS.get(sort, "price IS NULL, price DESC")
            page = [(0, r) for r in self.con.execute(
                f"SELECT e.* FROM entries e{filters} ORDER BY {order}, e.id LIMIT ? OFFSET ?",
                params + [limit, offset])]

        exps = self._expansions({r["id_expansion"] for _, r in page})
        results = [dict(r) | {"score": round(s, 1), "set": exps.get(r["id_expansion"])}
                   for s, r in page]
        for r in results:
            del r["norm"]
        end = offset + len(results)
        return {"results": results, "total": total, "offset": offset,
                "next_offset": end if end < total else None, "facets": facets,
                "sets": self.search_sets(nq) if nq and offset == 0 else [],
                "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def search_sets(self, nq, limit=6):
        """Sets whose code or name matches the normalised query, best match first."""
        found = []
        qwords = nq.split()
        for s in self._sets_index():
            name, words = s["norm"], s["norm"].split()
            if s["code"].lower() == nq:
                base = 1000
            elif name == nq:
                base = 900
            elif name.startswith(nq):
                base = 800
            elif all(any(w.startswith(q) for w in words) for q in qwords):
                base = 650
            elif nq in name:
                base = 500
            else:
                # Typos ("bloomburow"): each query word against its closest name word
                sim = sum(max(SequenceMatcher(None, q, w).ratio() for w in words)
                          for q in qwords) / len(qwords)
                if sim < 0.8 or len(qwords) > len(words):
                    continue
                base = 450 * sim
            # Shorter names first ("Bloomburrow" before "Bloomburrow: Promos"), then newest
            found.append((base - len(name) * 0.5, s["released"] or "", s))
        found.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [{k: v for k, v in s.items() if k != "norm"} for _, _, s in found[:limit]]

    def _sets_index(self):
        """Every set with a code, with its number of cards and sealed products (cached)."""
        if self._set_list is None:
            cards = dict(self.con.execute(
                "SELECT x.code, COUNT(DISTINCT s.id_metacard) FROM singles s "
                "JOIN expansions x ON x.id = s.id_expansion WHERE x.code IS NOT NULL GROUP BY x.code"))
            sealed = dict(self.con.execute(
                "SELECT x.code, COUNT(*) FROM entries e JOIN expansions x ON x.id = e.id_expansion "
                "WHERE e.kind = 'sealed' AND x.code IS NOT NULL GROUP BY x.code"))
            self._set_list = [
                {"code": s["code"], "name": s["name"], "norm": norm(s["name"]),
                 "released": s["released"], "cards": cards.get(s["code"], 0),
                 "sealed": sealed.get(s["code"], 0)} for s in self.sets()]
        return self._set_list

    def _candidates(self, nq, where, params):
        filters = "".join(" AND " + w for w in where)
        if self.fts and len(nq) >= 3:
            # 1) names containing the text as typed
            rows = self.con.execute(
                "SELECT e.* FROM entries_fts f JOIN entries e ON e.id = f.rowid "
                f"WHERE entries_fts MATCH ? {filters} LIMIT 1500",
                [f'"{nq}"'] + params).fetchall()
            if len(rows) >= 20:
                return rows
            # 2) typos: names sharing the most 3-letter pieces with the query
            grams = {w[i:i + 3] for w in nq.split() for i in range(len(w) - 2)}
            if grams:
                seen = {r["id"] for r in rows}
                rows += [r for r in self.con.execute(
                    "SELECT e.* FROM entries_fts f JOIN entries e ON e.id = f.rowid "
                    f"WHERE entries_fts MATCH ? {filters} ORDER BY rank LIMIT 300",
                    [" OR ".join(f'"{g}"' for g in grams)] + params) if r["id"] not in seen]
            return rows
        # Short queries (or no FTS): names or words starting with the text
        return self.con.execute(
            "SELECT e.* FROM entries e WHERE (e.norm LIKE ? OR e.norm LIKE ? OR "
            f"(? AND e.norm LIKE ?)) {filters} LIMIT 3000",
            [nq + "%", "% " + nq + "%", not self.fts, "%" + nq + "%"] + params).fetchall()

    def _set_code_hits(self, nq, where, params):
        """A query like "mh3" also finds the sealed products and cards of that set."""
        if not re.fullmatch(r"[a-z0-9]{2,6}", nq):
            return {}
        filters = "".join(" AND " + w for w in where)
        rows = self.con.execute(
            "SELECT e.* FROM entries e WHERE ((e.kind = 'sealed' AND e.id_expansion IN "
            "(SELECT id FROM expansions WHERE code = ?)) OR (e.kind = 'card' AND e.ref IN "
            "(SELECT s.id_metacard FROM singles s JOIN expansions x ON x.id = s.id_expansion "
            f"WHERE x.code = ?))) {filters} LIMIT 1000", [nq.upper(), nq.upper()] + params)
        return {r["id"]: r for r in rows}

    # ---------- lookups used by the rest of the app ----------

    def _expansions(self, ids):
        ids = [i for i in ids if i is not None]
        if not ids:
            return {}
        rows = self.con.execute(f"SELECT * FROM expansions WHERE id IN ({','.join('?' * len(ids))})", ids)
        return {r["id"]: {"code": r["code"], "name": r["name"], "released": r["released"],
                          "type": r["type"], "icon": r["icon"]} for r in rows}

    def sets(self):
        """Set codes for the filter, newest first."""
        return [dict(r) for r in self.con.execute(
            "SELECT code, MIN(name) AS name, MAX(released) AS released FROM expansions "
            "WHERE code IS NOT NULL GROUP BY code ORDER BY released DESC")]

    def categories(self):
        return [r[0] for r in self.con.execute(
            "SELECT DISTINCT category FROM entries WHERE category IS NOT NULL ORDER BY 1")]

    def sealed_product(self, id_product):
        r = self.con.execute("SELECT * FROM entries WHERE kind = 'sealed' AND ref = ?",
                             (id_product,)).fetchone()
        if not r:
            return None
        return dict(r) | {"set": self._expansions([r["id_expansion"]]).get(r["id_expansion"])}

    def sealed_ids(self):
        return [r[0] for r in self.con.execute("SELECT ref FROM entries WHERE kind = 'sealed'")]

    def set_sealed(self, id_expansion):
        """Sealed products of the same set (same set code, or same expansion), dearest first."""
        rows = self.con.execute(
            "SELECT e.ref, e.name, e.category, e.price, e.change, e.tcgplayer_id FROM entries e "
            "JOIN expansions x ON x.id = e.id_expansion WHERE e.kind = 'sealed' AND "
            "(e.id_expansion = ? OR (x.code IS NOT NULL AND x.code = "
            "(SELECT code FROM expansions WHERE id = ?))) ORDER BY e.price IS NULL, e.price DESC",
            (id_expansion, id_expansion))
        return [dict(r) for r in rows]

    def set_singles(self, id_expansion):
        """Single cards printed in the same set (main and "Extras" expansions)."""
        return [dict(r) for r in self.con.execute(
            "SELECT s.*, x.code FROM singles s JOIN expansions x ON x.id = s.id_expansion "
            "WHERE s.id_expansion = ? OR (x.code IS NOT NULL AND x.code = "
            "(SELECT code FROM expansions WHERE id = ?))", (id_expansion, id_expansion))]

    def printings_after(self, id_metacard, released):
        """Number of printings of a card in sets released after `released`."""
        return self.con.execute(
            "SELECT COUNT(DISTINCT s.id_expansion) FROM singles s JOIN expansions x "
            "ON x.id = s.id_expansion WHERE s.id_metacard = ? AND x.released > ?",
            (id_metacard, released or "")).fetchone()[0]

    def upcoming(self, days=180):
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM upcoming WHERE released <= date('now', ?) ORDER BY released",
            (f"+{days} days",))]


def score(nq, row, in_set=False):
    """Relevance of one entry for the normalised query (None = not a match).

    Exact name > name starts with the query > every word starts a word of
    the name > the query appears inside the name > close spelling (typos).
    Ties are broken by popularity (price, number of printings) and a
    shorter name; tokens and Art Series cards go after the real card.
    """
    name = row["norm"]
    words = name.split()
    qwords = nq.split()
    if in_set:
        base = 600  # the query is this entry's set code
    elif name == nq:
        base = 1000
    elif name.startswith(nq):
        base = 800
    elif all(any(w.startswith(q) for w in words) for q in qwords):
        base = 650
    elif nq in name:
        base = 500
    else:
        # Typos: compare with the whole name and with its beginning
        ratio = max(SequenceMatcher(None, nq, name).ratio(),
                    SequenceMatcher(None, nq, name[:len(nq) + 2]).ratio())
        # ...and word by word (each query word against its closest name word)
        per_word = sum(max(SequenceMatcher(None, q, w).ratio() for w in words)
                       for q in qwords) / len(qwords)
        similarity = max(ratio, per_word)
        if similarity < 0.72:
            return None
        base = 450 * similarity
    popularity = 25 * math.log10(1 + (row["price"] or 0)) + min(row["printings"] or 1, 30) / 2
    # Tokens and Art Series cards share the name of the real card: show them after it
    if name.startswith("art series ") or " token" in name:
        base -= 150
    return base + popularity - len(name) * 0.4


def _version():
    if not DB.exists():
        return None
    con = sqlite3.connect(DB)
    try:
        r = con.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
        return r[0] if r else None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()
