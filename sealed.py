"""Sealed products (booster boxes, bundles, decks...): price history, tracker
and a price outlook.

Cardmarket's price guide only has *today's* numbers and MTGJSON has no
Cardmarket history for sealed products, so Gotha records its own: every day
the guide is downloaded, the prices of all ~5,000 sealed products are saved
in data/sealed_history.sqlite. That file lives outside cache/ on purpose:
deleting it loses the history, which can't be downloaded again (only the
days found in archived price guides can be imported back, see backfill.py).

The products you track (with quantity, price paid and optional alert
targets) are kept in watchlist.json under "sealed".

The outlook is a transparent score, not a guarantee: a few signals (price
momentum, age of the set, reprint risk, tournament play, news sentiment)
are each scored from -1 to +1, weighted and averaged, and every signal says
which numbers it was based on.
"""
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import market
from history import GUIDE_FIELDS

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB = DATA_DIR / "sealed_history.sqlite"
WATCHLIST = BASE_DIR / "watchlist.json"
OLD_GUIDE = BASE_DIR / "cache" / "price_guide_1.json"  # an older guide, imported once if found
RECENT_DAYS = 90  # the outlook's "recorded trend" only looks at this many recent days

# MTGJSON set types that usually carry reprints of older cards
REPRINT_SET_TYPES = {"masters", "commander", "draft_innovation", "box", "duel_deck",
                     "from_the_vault", "spellbook", "premium_deck", "arsenal"}


class Tracker:
    def __init__(self, db=DB):
        Path(db).parent.mkdir(exist_ok=True)
        self.con = sqlite3.connect(db, check_same_thread=False)
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS history (id_product INTEGER, day TEXT, "
            + ", ".join(f"{f} INTEGER" for f in GUIDE_FIELDS)
            + ", PRIMARY KEY (id_product, day)) WITHOUT ROWID")

    def record(self, hist, sealed_ids):
        """Saves today's guide prices of every sealed product (once per guide date)."""
        if not hist.guide_date:
            return 0
        n = len(GUIDE_FIELDS)
        rows = []
        for pid in sealed_ids:
            r = hist.con.execute("SELECT * FROM guide WHERE id_product = ?", (pid,)).fetchone()
            if r and any(r[1:n + 1]):
                rows.append((pid, hist.guide_date, *r[1:n + 1]))
        self._save(rows)
        if OLD_GUIDE.exists():
            self.import_guide(OLD_GUIDE, sealed_ids)
        return len(rows)

    def import_guide(self, guide, sealed_ids):
        """Adds the sealed prices of an older price guide to the history.

        `guide` is a saved price_guide_1.json (path) or its already-parsed content.
        Returns the number of products saved (0 if that day is already recorded).
        """
        if not isinstance(guide, dict):
            guide = json.loads(Path(guide).read_text(encoding="utf-8"))
        day = guide["createdAt"][:10]
        if self.con.execute("SELECT 1 FROM history WHERE day = ? LIMIT 1", (day,)).fetchone():
            return 0
        ids = set(sealed_ids)
        rows = [(g["idProduct"], day, *(round(g[f] * 100) if g.get(f) else None for f in GUIDE_FIELDS))
                for g in guide["priceGuides"] if g["idProduct"] in ids]
        self._save(rows)
        return len(rows)

    def _save(self, rows):
        self.con.executemany(f"INSERT OR REPLACE INTO history VALUES "
                             f"({', '.join('?' * (len(GUIDE_FIELDS) + 2))})", rows)
        self.con.commit()

    def history(self, id_product):
        """[{"day", "low", "trend", "avg1", "avg7", "avg30"}] oldest first, in euros."""
        rows = self.con.execute("SELECT * FROM history WHERE id_product = ? ORDER BY day",
                                (id_product,))
        return [{"day": r[1], **{f: (c / 100 if c else None) for f, c in zip(GUIDE_FIELDS, r[2:])}}
                for r in rows]

    def days_recorded(self):
        return self.con.execute("SELECT COUNT(DISTINCT day) FROM history").fetchone()[0]

    def days(self):
        """Set of the days that have recorded prices."""
        return {r[0] for r in self.con.execute("SELECT DISTINCT day FROM history")}

    def prices_on(self, ids, day):
        """{id_product: price} on `day`, or on the latest recorded day before it.

        The price is the trend, or the cheapest listing when there is no trend.
        """
        if not ids:
            return {}
        rows = self.con.execute(
            "SELECT h.id_product, h.trend, h.low FROM history h JOIN (SELECT id_product, "
            "MAX(day) AS day FROM history WHERE day <= ? AND (trend OR low) AND id_product IN "
            f"({','.join('?' * len(ids))}) GROUP BY id_product) last "
            "ON last.id_product = h.id_product AND last.day = h.day", [day, *ids])
        return {pid: (trend or low) / 100 for pid, trend, low in rows}


# ---------- watchlist ("sealed" section of watchlist.json) ----------

def load_watchlist():
    return json.loads(WATCHLIST.read_text(encoding="utf-8")) if WATCHLIST.exists() else {"cards": []}


def tracked():
    return load_watchlist().get("sealed", [])


def track(entry):
    """Adds or updates a tracked product: {"id", "name", "qty", "paid", "below", "above"}."""
    config = load_watchlist()
    items = [s for s in config.get("sealed", []) if s["id"] != entry["id"]]
    items.append({k: v for k, v in entry.items() if v not in (None, "")})
    config["sealed"] = items
    _write_watchlist(config)


def untrack(id_product):
    config = load_watchlist()
    config["sealed"] = [s for s in config.get("sealed", []) if s["id"] != id_product]
    _write_watchlist(config)


def _write_watchlist(config):
    """Writes watchlist.json keeping its hand-edited layout: one entry per line."""
    def one_line(item):
        return "{ " + json.dumps(item, ensure_ascii=False, separators=(", ", ": "))[1:-1] + " }"

    parts = []
    for key, value in config.items():
        if isinstance(value, list):
            inner = ",\n".join("    " + one_line(v) for v in value)
            parts.append(f'  "{key}": [\n{inner}\n  ]' if value else f'  "{key}": []')
        else:
            parts.append(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}")
    tmp = WATCHLIST.with_suffix(".tmp")
    tmp.write_text("{\n" + ",\n".join(parts) + "\n}\n", encoding="utf-8")
    tmp.replace(WATCHLIST)


# ---------- outlook ----------

def _clamp(x):
    return max(-1.0, min(1.0, x))


def _pct(a, b):
    return (a - b) / b if a and b else None


def _slope_per_30_days(points):
    """Least-squares slope of [(day, price)], as a fraction of the mean price per 30 days."""
    xs = [date.fromisoformat(d).toordinal() for d, _ in points]
    ys = [p for _, p in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    if not var or not my:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var * 30 / my


def outlook(product, guide, history, catalog, hist, news_items, staple_lists):
    """Price outlook for one sealed product.

    Returns {"score" (-1..1), "label", "confidence", "projection", "signals": [...]};
    each signal is {"name", "score", "weight", "text"}.
    """
    signals = []

    def add(name, value, weight, text):
        signals.append({"name": name, "score": round(_clamp(value), 2), "weight": weight, "text": text})

    g = guide.get("normal", {})
    trend, low = g.get("trend"), g.get("low")
    price = trend or low

    # 1. Momentum from Cardmarket's numbers (available from day one). Sealed products
    #    have no 7/30-day averages, only the trend and the average sale price.
    m = _pct(g.get("avg7"), g.get("avg30"))
    if m is not None:
        add("Momentum (Cardmarket)", m / 0.15, 2,
            f"7-day average sale {g['avg7']:.2f} € vs 30-day average {g['avg30']:.2f} € ({m:+.1%}).")
    elif (m := _pct(trend, g.get("avg"))) is not None:
        add("Momentum (Cardmarket)", m / 0.15, 1.5,
            f"Price trend {trend:.2f} € vs average sale price {g['avg']:.2f} € ({m:+.1%}).")
    else:
        add("Momentum (Cardmarket)", 0, 0, "Not enough recent sales on Cardmarket.")

    # 2. Gotha's own recorded history (gets better every day it runs). Only the last
    #    90 days count: a year-long history would describe the past, not where it's heading.
    since = (date.today() - timedelta(days=RECENT_DAYS)).isoformat()
    points = [(h["day"], h["trend"] or h["low"]) for h in history
              if (h["trend"] or h["low"]) and h["day"] >= since]
    span = (date.fromisoformat(points[-1][0]) - date.fromisoformat(points[0][0])).days if points else 0
    if len(points) >= 3 and span >= 7:
        s = _slope_per_30_days(points)
        add("Recorded trend", s / 0.1 if s is not None else 0, 2,
            f"{len(points)} daily prices over the last {span} days: {s:+.1%} per 30 days."
            if s is not None else "Prices recorded, but flat.")
    else:
        add("Recorded trend", 0, 0, f"Only {len(points)} day(s) recorded so far; "
            "this signal starts counting after a week of history.")

    # 3. Cheapest listing vs trend: sellers undercutting means supply pressure. Weak and
    #    noisy (the cheapest listing may be another language or a damaged box).
    if trend and low:
        spread = (low - trend) / trend
        add("Listings vs trend", max(spread / 0.4, -0.5) if spread < 0 else min(spread / 0.1, 0.5),
            0.5, f"Cheapest listing {low:.2f} € vs trend {trend:.2f} € ({spread:+.1%}).")

    # 4. Age of the product (sealed usually dips after release, then rises once out of print)
    exp = product.get("set") or {}
    released = exp.get("released") or product.get("released")
    if released:
        months = (date.today() - date.fromisoformat(released)).days / 30.4
        if months < 0:
            v, why = -0.3, "not released yet (pre-order prices often fall at release)"
        elif months < 4:
            v, why = -0.6, "just released: supply is at its peak and prices usually fall"
        elif months < 12:
            v, why = -0.1, "still in print: prices are usually flat"
        elif months < 36:
            v, why = 0.4, "probably out of print: sealed supply only shrinks from here"
        else:
            v, why = 0.6, "long out of print: old sealed tends to appreciate"
        add("Product age", v, 1.5, f"Released {released} ({max(months, 0):.0f} months ago): {why}.")

    # 5. Reprint risk: valuable cards of the set not yet reprinted, upcoming reprint sets, news
    singles = catalog.set_singles(product["id_expansion"])
    best = {}
    for s in singles:
        p = hist.guide(s["id_product"]).get("normal", {}).get("trend")
        if p and p > best.get(s["id_metacard"], (0, ""))[0]:
            best[s["id_metacard"]] = (p, s["name"])
    chase = sorted(best.items(), key=lambda x: -x[1][0])[:10]
    upcoming = [u for u in catalog.upcoming(180) if u["type"] in REPRINT_SET_TYPES]
    risk_text, risk = [], 0.0
    if chase:
        # Valuable cards that were never reprinted are the ones a reprint would hit;
        # only cards worth 10 € or more really move the price of sealed product.
        value = sum(p for _, (p, _) in chase)
        unreprinted = [(p, n) for mid, (p, n) in chase
                       if p >= 10 and not catalog.printings_after(mid, released)]
        share = sum(p for p, _ in unreprinted) / value if value else 0
        risk -= 0.4 * share
        risk_text.append(f"{len(unreprinted)} card(s) worth 10 € or more among the set's top "
                         f"{len(chase)} were never reprinted ({share:.0%} of their value)"
                         + (f", e.g. {', '.join(n for _, n in unreprinted[:3])}" if unreprinted else ""))
    # Masters/remastered sets reprint the most; commander decks and other products less
    pressure = sum(0.15 if u["type"] == "masters" else 0.05 for u in upcoming)
    if upcoming:
        risk -= min(pressure, 0.3)
        risk_text.append(f"{len(upcoming)} reprint-type set(s) in the next 6 months: "
                         + ", ".join(f"{u['name']} ({u['released']}, {u['type']})" for u in upcoming[:4]))
    names = [exp.get("name"), product["name"]] + [n for _, (_, n) in chase[:5]]
    reprint_news = [i for i in market.mentions(news_items, names) if "reprint" in i["tags"]]
    if reprint_news:
        risk -= 0.4
        risk_text.append(f"{len(reprint_news)} recent headline(s) mention a reprint: "
                         f"“{reprint_news[0]['title']}”")
    if risk_text:
        add("Reprint risk", risk, 1, "; ".join(risk_text) + ".")

    # 6. Tournament play: how many format staples come from this set
    in_set = {s["name"] for s in singles}
    played = []
    for fmt, cards in staple_lists.items():
        for c in cards:
            if c["name"] in in_set:
                played.append((c["decks"], c["name"], fmt))
    if staple_lists:
        demand = sum(d for d, _, _ in played)
        top = sorted(played, reverse=True)[:4]
        add("Tournament play", demand / 80, 1.5,
            (f"{len(played)} staple(s) from this set; top: "
             + ", ".join(f"{n} ({d}% of {f} decks)" for d, n, f in top) + ".")
            if played else "No card from this set is among the most played tournament cards.")

    # 7. News and social posts mentioning the set or product
    talk = market.mentions(news_items, [exp.get("name"), product["name"]])
    if talk:
        avg = sum(i["sentiment"] for i in talk) / len(talk)
        add("News & social sentiment", avg, 1,
            f"{len(talk)} recent headline(s)/post(s); average tone {avg:+.2f}. "
            f"Latest: “{talk[0]['title']}”.")
    else:
        add("News & social sentiment", 0, 0, "No recent headline or post mentions this set.")

    total_w = sum(s["weight"] for s in signals)
    score = sum(s["score"] * s["weight"] for s in signals) / total_w if total_w else 0
    label = "Likely up" if score > 0.2 else "Likely down" if score < -0.2 else "Sideways"
    with_data = sum(1 for s in signals if s["weight"])
    confidence = "medium" if span >= 30 and with_data >= 5 else "low"
    projection = None
    if price:
        # Rough 90-day range: the score sets the centre, the spread widens with uncertainty
        centre = score * 0.15
        band = 0.08 if confidence == "medium" else 0.15
        projection = {"days": 90, "low": round(price * (1 + centre - band), 2),
                      "mid": round(price * (1 + centre), 2),
                      "high": round(price * (1 + centre + band), 2)}
    return {"score": round(score, 2), "label": label, "confidence": confidence,
            "projection": projection, "signals": signals,
            "news": talk[:8], "chase": [{"name": n, "price": p} for _, (p, n) in chase]}


def portfolio(catalog, hist, tracker):
    """The tracked products with today's value, 7-day change and profit/loss."""
    rows = []
    for t in tracked():
        product = catalog.sealed_product(t["id"]) or {"name": t.get("name"), "set": None}
        g = hist.guide(t["id"]).get("normal", {})
        price = g.get("trend") or g.get("low")
        past = [h for h in tracker.history(t["id"]) if h["trend"] or h["low"]]
        # Change since the latest recorded day that is at least a week old
        week_ago = [h for h in past if (date.today() - date.fromisoformat(h["day"])).days >= 7]
        before = (week_ago[-1]["trend"] or week_ago[-1]["low"]) if week_ago else None
        qty = t.get("qty", 1)
        rows.append({
            "id": t["id"], "name": product["name"], "set": product.get("set"),
            "category": product.get("category"), "qty": qty, "paid": t.get("paid"),
            "below": t.get("below"), "above": t.get("above"),
            "price": price, "low": g.get("low"), "change7": _pct(price, before),
            "since": week_ago[-1]["day"] if week_ago else None,
            "value": round(price * qty, 2) if price else None,
            "pl": round((price - t["paid"]) * qty, 2) if price and t.get("paid") else None,
        })
    return rows
