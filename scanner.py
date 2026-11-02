"""Opportunity scanner: looks in today's Cardmarket price guide for cards that start to rise.

Signal: the 7-day average is much higher than the 30-day average, and
today's average (avg1) is not below the 7-day one and the trend is above the
30-day average (the rise goes on).
Run: python scanner.py   (options: --min EUR, --rise PERCENT, --foil, --down, --limit N)
It only reads cache/prices.sqlite and cache/catalog.sqlite (start the app once to build them).
"""
import argparse
import sqlite3
import sys
from pathlib import Path

import catalog

CACHE = Path(__file__).resolve().parent / "cache"


def scan(min_price=5.0, rise=25.0, foil=False, down=False, limit=25):
    """Returns (guide_date, rows). Row: name, set, trend, avg7, avg30, change (7d vs 30d)."""
    for f in ("prices.sqlite", "catalog.sqlite"):
        if not (CACHE / f).exists():
            raise SystemExit(f"Missing cache/{f}. Start the app once (python server.py) to build it.")
    con = sqlite3.connect((CACHE / "prices.sqlite").as_uri() + "?mode=ro", uri=True)
    con.execute("ATTACH DATABASE ? AS cat", ((CACHE / "catalog.sqlite").as_uri() + "?mode=ro",))
    guide_date = con.execute("SELECT value FROM meta WHERE key = 'guide_date'").fetchone()[0]
    sfx = "_foil" if foil else ""
    avg1, avg7, avg30, trend = (f"g.{f}{sfx}" for f in ("avg1", "avg7", "avg30", "trend"))
    # rising: avg7 above avg30 by `rise`%, and avg1 >= avg7, trend >= avg30; falling is the opposite
    if down:
        where = f"{avg7} <= {avg30} * ? AND {avg1} <= {avg7} AND {trend} <= {avg30}"
        factor, order = 1 - rise / 100, "ASC"
    else:
        where = f"{avg7} >= {avg30} * ? AND {avg1} >= {avg7} AND {trend} >= {avg30}"
        factor, order = 1 + rise / 100, "DESC"
    extras = " OR ".join("lower(replace(s.name, ':', '')) LIKE ?" for _ in catalog.EXTRAS)
    rows = con.execute(
        f"SELECT s.name, x.code, {trend}, {avg7}, {avg30}, {avg7} * 1.0 / {avg30} - 1 AS change "
        "FROM guide g JOIN cat.singles s ON s.id_product = g.id_product "
        "LEFT JOIN cat.expansions x ON x.id = s.id_expansion "
        f"WHERE {avg30} >= ? AND {where} AND NOT ({extras}) "
        f"ORDER BY change {order} LIMIT ?",
        [round(min_price * 100), factor, *catalog.EXTRAS, limit]).fetchall()
    return guide_date, rows


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Find cards whose price starts to rise (or fall).")
    ap.add_argument("--min", type=float, default=5.0, help="lowest 30-day average in EUR (default 5)")
    ap.add_argument("--rise", type=float, default=25.0, help="7-day vs 30-day average, in %% (default 25)")
    ap.add_argument("--foil", action="store_true", help="look at foil prices")
    ap.add_argument("--down", action="store_true", help="falling prices instead of rising")
    ap.add_argument("--limit", type=int, default=25)
    a = ap.parse_args()
    guide_date, rows = scan(a.min, a.rise, a.foil, a.down, a.limit)
    print(f"\nCards with a {'falling' if a.down else 'rising'} price"
          f"{' (foil)' if a.foil else ''}, price guide of {guide_date}\n")
    if not rows:
        print("Nothing found. Try a lower --rise or --min.")
        return
    print(f"{'Card':40} {'Set':6} {'Trend':>8} {'Avg 7d':>8} {'Avg 30d':>8} {'7d vs 30d':>10}")
    print("─" * 86)
    for name, code, trend, a7, a30, change in rows:
        t = f"{trend / 100:.2f}" if trend else "-"
        print(f"{name[:40]:40} {(code or '?')[:6]:6} {t:>8} {a7 / 100:>8.2f} {a30 / 100:>8.2f} {change:>+10.0%}")


if __name__ == "__main__":
    main()
