#!/usr/bin/env python3
"""Answers price questions from Gotha's local databases (see SKILL.md).

    python query.py status
    python query.py find TEXT [--kind card|sealed] [--limit N]
    python query.py card NAME [--id METACARD] [--history]
    python query.py sealed NAME [--id PRODUCT]
    python query.py movers [--kind card|sealed] [--down] [--min EUR] [--limit N]

The databases are opened read-only, so nothing is downloaded or changed.
Only `card --history` goes online: it asks Scryfall for the printings' ids.
"""
import argparse
import sqlite3
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(ROOT))  # to import the project's own modules

import catalog  # noqa: E402
from history import _unpack, price_on  # noqa: E402

PRICES = ROOT / "cache" / "prices.sqlite"
CATALOG = ROOT / "cache" / "catalog.sqlite"
SEALED = ROOT / "data" / "sealed_history.sqlite"


def ro(path):
    """Read-only connection (never builds or changes the file)."""
    if not path.exists():
        sys.exit(f"Missing {path.relative_to(ROOT)}. Start the app once (python server.py) to build it.")
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def eur(cents):
    return cents / 100 if cents else None


def money(x):
    return f"{x:.2f}" if x else "-"


def pct(new, old):
    return new / old - 1 if new and old else None


def fmt_pct(x):
    return f"{x:+.1%}" if x is not None else "-"


def table(headers, rows):
    rows = [[str(v) for v in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    for r in rows:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)))


def find_entries(con, text, kind=None, limit=10):
    """Entries whose name has every word of `text`: exact name first, then by price."""
    words = catalog.norm(text).split()
    if not words:
        return []
    nq = " ".join(words)
    where = ["e.norm LIKE ?"] * len(words)
    params = [f"%{w}%" for w in words]
    if kind:
        where.append("e.kind = ?")
        params.append(kind)
    return con.execute(
        "SELECT e.*, x.code FROM entries e LEFT JOIN expansions x ON x.id = e.id_expansion "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY e.norm = ? DESC, e.norm LIKE ? DESC, e.price IS NULL, e.price DESC LIMIT ?",
        params + [nq, nq + "%", limit]).fetchall()


def pick(con, args, kind):
    """The entry asked for (by --id or by name), listing the other matches."""
    if args.id:
        row = con.execute(
            "SELECT e.*, x.code FROM entries e LEFT JOIN expansions x ON x.id = e.id_expansion "
            "WHERE e.kind = ? AND e.ref = ?", (kind, args.id)).fetchone()
        if not row:
            sys.exit(f"No {kind} with id {args.id}.")
        return row
    hits = find_entries(con, " ".join(args.name), kind, 6)
    if not hits:
        sys.exit(f"No {kind} matches '{' '.join(args.name)}'. Try: query.py find ...")
    if len(hits) > 1:
        print("Other matches: " + "; ".join(f"{h['name']} ({h['code'] or '?'}, --id {h['ref']})"
                                           for h in hits[1:]) + "\n")
    return hits[0]


# ---------- commands ----------

def status(_):
    guide_date = ro(PRICES).execute("SELECT value FROM meta WHERE key = 'guide_date'").fetchone()
    meta = dict(ro(CATALOG).execute("SELECT key, value FROM meta").fetchall())
    days, first, last = ro(SEALED).execute(
        "SELECT COUNT(DISTINCT day), MIN(day), MAX(day) FROM history").fetchone()
    guide_date = guide_date[0] if guide_date else None
    print(f"Cardmarket price guide: {guide_date}")
    print(f"Search index built:     {meta.get('built')} ({meta.get('products')} products)")
    print(f"Sealed history:         {days} days recorded, {first} to {last}")
    if not guide_date or guide_date < (date.today() - timedelta(days=1)).isoformat():
        print("\nThe prices are older than yesterday. Starting the app (python server.py) "
              "downloads fresh data (~2-3 min).")


def find(args):
    rows = find_entries(ro(CATALOG), " ".join(args.name), args.kind, args.limit)
    table(["Kind", "Id", "Name", "Set", "Price", "Week"],
          [[r["kind"], r["ref"], r["name"][:55], r["code"] or "-", money(r["price"]),
            fmt_pct(r["change"])] for r in rows])


def card(args):
    con = ro(CATALOG)
    entry = pick(con, args, "card")
    prints = con.execute(
        "SELECT s.id_product, x.code, x.name AS set_name, x.released FROM singles s "
        "LEFT JOIN expansions x ON x.id = s.id_expansion WHERE s.id_metacard = ? "
        "ORDER BY x.released", (entry["ref"],)).fetchall()
    prices = ro(PRICES)
    ids = [p["id_product"] for p in prints]
    guide = {g["id_product"]: g for g in prices.execute(
        f"SELECT * FROM guide WHERE id_product IN ({','.join('?' * len(ids))})", ids)}
    print(f"{entry['name']}: {len(prints)} printings on Cardmarket (metacard id {entry['ref']})\n")
    rows = []
    for p in prints:
        g = guide.get(p["id_product"])
        if not g:
            continue
        avg7, avg30 = eur(g["avg7"]), eur(g["avg30"])
        rows.append([p["code"] or "-", (p["set_name"] or "?")[:28], p["id_product"],
                     money(eur(g["low"])), money(eur(g["trend"])), money(eur(g["avg1"])),
                     money(avg7), money(avg30), fmt_pct(pct(avg7, avg30)),
                     money(eur(g["trend_foil"]))])
    table(["Set", "Set name", "Product", "From", "Trend", "Avg1", "Avg7", "Avg30", "7v30",
           "Foil trend"], rows)
    if len(rows) < len(prints):
        print(f"({len(prints) - len(rows)} printings have no price in the guide)")
    if args.history:
        card_history(entry["name"], prints, prices)


def card_history(name, prints, prices):
    """Trend price today / 7 / 30 / 60 days ago, from MTGJSON's history (needs Scryfall)."""
    import main  # the project's Scryfall client (it pauses between requests)
    exact = main.resolve_name(name)
    if not exact:
        print(f"\nScryfall doesn't know '{name}'.")
        return
    by_cm = {s["cardmarket_id"]: s["id"] for s in main.get_printings(exact) if s.get("cardmarket_id")}
    series = {}
    for p in prints:
        sid = by_cm.get(p["id_product"])
        r = sid and prices.execute("SELECT data FROM prices WHERE scryfall_id = ?",
                                   (uuid.UUID(sid).bytes,)).fetchone()
        if r:
            series[p["id_product"]] = (p, _unpack(r[0]).get("normal"))
    dates = [d for _, s in series.values() if s for d in s]
    if not dates:
        print("\nNo price history for these printings.")
        return
    ref = max(dates)
    print(f"\nTrend price history (non-foil), up to {ref}:\n")
    rows = []
    for p, s in series.values():
        now, d7, d30, d60 = (price_on(s, d, ref) for d in (0, 7, 30, 60))
        rows.append([p["code"] or "-", (p["set_name"] or "?")[:28], money(now), money(d7),
                     money(d30), money(d60), fmt_pct(pct(now, d30))])
    table(["Set", "Set name", "Today", "7d ago", "30d ago", "60d ago", "30d change"], rows)


def sealed(args):
    con = ro(CATALOG)
    entry = pick(con, args, "sealed")
    pid = entry["ref"]
    g = ro(PRICES).execute("SELECT low, trend, avg FROM guide WHERE id_product = ?", (pid,)).fetchone()
    print(f"{entry['name']} ({entry['code'] or '?'}, {entry['category']}, product id {pid}, "
          f"released {entry['released'] or '?'})")
    if g:
        print(f"Cardmarket today: From {money(eur(g['low']))}  Trend {money(eur(g['trend']))}  "
              f"Avg sale {money(eur(g['avg']))}")
    hist = [(day, eur(trend or low)) for day, trend, low in ro(SEALED).execute(
        "SELECT day, trend, low FROM history WHERE id_product = ? AND (trend OR low) ORDER BY day",
        (pid,))]
    if not hist:
        print("\nNo recorded history for this product yet.")
        return
    last_day, last_price = hist[-1]
    print(f"\nRecorded history: {len(hist)} days, {hist[0][0]} to {last_day}\n")
    rows = []
    for days in (7, 30, 90, 180, 365):
        target = date.fromisoformat(last_day) - timedelta(days=days)
        # Like the app: the latest record up to 10 days before the target day
        before = [h for h in hist if (target - timedelta(days=10)).isoformat() <= h[0] <= target.isoformat()]
        if before:
            day, price = before[-1]
            rows.append([f"{days} days ago", day, money(price), fmt_pct(pct(last_price, price))])
    rows.insert(0, ["Latest", last_day, money(last_price), ""])
    table(["When", "Day", "Price", "Change to latest"], rows)
    year = [h for h in hist if h[0] >= (date.fromisoformat(last_day) - timedelta(days=365)).isoformat()]
    low, high = min(year, key=lambda h: h[1]), max(year, key=lambda h: h[1])
    print(f"\nLast 365 days: lowest {money(low[1])} on {low[0]}, highest {money(high[1])} on {high[0]}")


def movers(args):
    con = ro(CATALOG)
    where = ["e.change IS NOT NULL", "e.price >= ?",
             "NOT (" + " OR ".join("e.norm LIKE ?" for _ in catalog.EXTRAS) + ")"]
    params = [args.min, *catalog.EXTRAS]
    if args.kind:
        where.append("e.kind = ?")
        params.append(args.kind)
    rows = con.execute(
        "SELECT e.*, x.code FROM entries e LEFT JOIN expansions x ON x.id = e.id_expansion "
        f"WHERE {' AND '.join(where)} ORDER BY e.change {'ASC' if args.down else 'DESC'} LIMIT ?",
        params + [args.limit]).fetchall()
    built = con.execute("SELECT value FROM meta WHERE key = 'built'").fetchone()[0]
    print(f"Biggest {'droppers' if args.down else 'gainers'} of the week "
          f"(search index built {built}, price from {args.min:.2f} EUR)\n")
    table(["Kind", "Id", "Name", "Set", "Price", "Week"],
          [[r["kind"], r["ref"], r["name"][:55], r["code"] or "-", money(r["price"]),
            fmt_pct(r["change"])] for r in rows])


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=status)
    p = sub.add_parser("find")
    p.add_argument("name", nargs="+")
    p.add_argument("--kind", choices=("card", "sealed"))
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(fn=find)
    p = sub.add_parser("card")
    p.add_argument("name", nargs="*")
    p.add_argument("--id", type=int, help="Cardmarket metacard id (from find)")
    p.add_argument("--history", action="store_true", help="add the 60-day trend history (asks Scryfall)")
    p.set_defaults(fn=card)
    p = sub.add_parser("sealed")
    p.add_argument("name", nargs="*")
    p.add_argument("--id", type=int, help="Cardmarket product id (from find)")
    p.set_defaults(fn=sealed)
    p = sub.add_parser("movers")
    p.add_argument("--kind", choices=("card", "sealed"))
    p.add_argument("--down", action="store_true", help="biggest droppers instead of gainers")
    p.add_argument("--min", type=float, default=2.0, help="lowest price in EUR (default 2)")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=movers)
    args = ap.parse_args()
    if args.cmd in ("card", "sealed") and not args.name and not args.id:
        ap.error(f"{args.cmd}: give a name or --id")
    args.fn(args)


if __name__ == "__main__":
    main()
