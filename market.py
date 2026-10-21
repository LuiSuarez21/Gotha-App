"""TCG market signals: news headlines, social posts and tournament staples.

  - news(): latest headlines from free RSS/Atom feeds (MTGGoldfish, Google
    News searches, Reddit r/mtgfinance), tagged by topic (reprint, ban,
    announcement, sealed, price) with a simple word-based sentiment score.
    Cached for an hour in cache/news.json.
  - staples(): the most played cards of each tournament format, read from
    MTGGoldfish's "format staples" pages (% of decks that play the card).
    Cached for a day in cache/staples.json.

Every feed is optional: if one fails (offline, rate limit...), the others
and the last cached items are still used.
"""
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from history import CACHE_DIR

NEWS_FILE = CACHE_DIR / "news.json"
STAPLES_FILE = CACHE_DIR / "staples.json"
NEWS_MAX_AGE = 3600  # seconds
STAPLES_MAX_AGE = 20 * 3600
HEADERS = {"User-Agent": "Mozilla/5.0 (Gotha MTG price app)"}

_GNEWS = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="
FEEDS = [
    ("MTGGoldfish", "https://www.mtggoldfish.com/feed"),
    ("Google News", _GNEWS + '"Magic: The Gathering" (reprint OR "booster box" OR sealed OR banned OR prices)'),
    ("Google News", _GNEWS + '"Magic: The Gathering" (announced OR revealed OR "Secret Lair")'),
    ("r/mtgfinance", "https://www.reddit.com/r/mtgfinance/new/.rss"),
    ("r/magicTCG", "https://www.reddit.com/r/magicTCG/new/.rss"),
]
FORMATS = ("standard", "pioneer", "modern", "legacy", "pauper", "commander")

TAGS = {
    "reprint": r"\breprint",
    "ban": r"\b(un)?ban(ned|s|list)?\b|\bb&r\b|banned and restricted",
    "announcement": r"\bannounc|\breveal|\bspoiler|\bpreview|\bfirst look|\bset release",
    "sealed": r"\bbooster|\bbox(es)?\b|\bbundle|\bsealed|\bcollector|\bprecon|\bcommander deck|\bsecret lair",
    "price": r"\bprice|\bspike|\bbuyout|\bfinance|\bmarket|\bvalue|\bexpensive|\bcheap",
}
_TAGS = {k: re.compile(v, re.I) for k, v in TAGS.items()}

# Word lists for the sentiment score (from the point of view of the price)
POSITIVE = ("spike", "spiking", "surge", "soar", "sold out", "sells out", "sell out", "selling out",
            "sellout", "rise", "rising", "gain", "demand", "popular", "love", "loved",
            "great", "best", "strong", "hype", "record", "buyout", "chase", "must-have", "banger",
            "underrated", "undervalued", "out of print", "skyrocket", "hot", "win", "winning")
NEGATIVE = ("drop", "dropping", "crash", "fall", "falling", "plummet", "dump", "overprint",
            "overprinted", "reprint", "bad", "worst", "disappoint", "hate", "flop", "weak",
            "glut", "oversupply", "oversupplied", "banned", "backlash", "boring", "worthless",
            "overpriced", "overvalued", "tank", "tanking", "decline", "loss", "lose", "mistake",
            "scam", "fake", "counterfeit", "delay", "cheaper")


def sentiment(text):
    """(score from -1 to 1, words found). Plain word counting, no AI."""
    t = text.lower()
    pos = [w for w in POSITIVE if re.search(r"\b" + re.escape(w) + r"\b", t)]
    neg = [w for w in NEGATIVE if re.search(r"\b" + re.escape(w) + r"\b", t)]
    total = len(pos) + len(neg)
    return ((len(pos) - len(neg)) / total if total else 0.0), pos + neg


def _text(el, *names):
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def _date(raw):
    """RSS (RFC 822) or Atom (ISO 8601) date -> ISO string in UTC (or None)."""
    if not raw:
        return None
    try:
        d = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat(timespec="minutes")


def _clean(raw):
    """HTML snippet -> short plain text."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:280] + ("…" if len(text) > 280 else "")


def parse_feed(source, xml_text):
    """Items of an RSS 2.0 or Atom feed: [{source, title, link, date, summary}]."""
    root = ET.fromstring(xml_text)
    atom = "{http://www.w3.org/2005/Atom}"
    items = []
    for it in root.iter("item"):  # RSS
        items.append({"title": _text(it, "title"), "link": _text(it, "link"),
                      "date": _date(_text(it, "pubDate")),
                      "summary": _clean(_text(it, "description")),
                      "source": _text(it, "source") or source})
    for it in root.iter(atom + "entry"):  # Atom
        link = it.find(atom + "link")
        items.append({"title": html.unescape(_text(it, atom + "title")),
                      "link": link.get("href") if link is not None else "",
                      "date": _date(_text(it, atom + "published", atom + "updated")),
                      "summary": _clean(_text(it, atom + "summary", atom + "content")),
                      "source": source})
    return [i for i in items if i["title"] and i["link"].startswith(("http://", "https://"))]


def _annotate(item):
    text = f"{item['title']} {item['summary']}"
    item["tags"] = [k for k, rx in _TAGS.items() if rx.search(text)]
    item["sentiment"], item["words"] = sentiment(text)
    return item


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(path, data):
    CACHE_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def news(refresh=False):
    """{"fetched", "items": [...newest first], "errors": [...]} (cached for an hour)."""
    cached = _load(NEWS_FILE)
    if cached and not refresh and time.time() - cached["fetched"] < NEWS_MAX_AGE:
        return cached
    old = {i["link"]: i for i in (cached or {}).get("items", [])}
    items, errors = {}, []
    for source, url in FEEDS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            for item in parse_feed(source, r.content):
                items.setdefault(item["link"], _annotate(item))
        except (requests.RequestException, ET.ParseError) as e:
            errors.append(f"{source}: {e.__class__.__name__}"
                          + (f" {e.response.status_code}" if getattr(e, "response", None) is not None else ""))
    # Keep older items for up to 30 days (a feed may be down today)
    cutoff = datetime.fromtimestamp(time.time() - 30 * 86400, timezone.utc).isoformat()
    for link, item in old.items():
        if link not in items and (item.get("date") or "") >= cutoff:
            items[link] = item
    data = {"fetched": time.time(),
            "items": sorted(items.values(), key=lambda i: i.get("date") or "", reverse=True)[:300],
            "errors": errors}
    _save(NEWS_FILE, data)
    return data


def mentions(items, names):
    """News items whose title/summary mentions any of the names (whole words, any case)."""
    names = [n for n in names if n and len(n) >= 4]
    if not names:
        return []
    rx = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I)
    return [i for i in items if rx.search(i["title"] + " " + i["summary"])]


_STAPLE = re.compile(
    r"data-card-id=\"([^\"]+?) \[(\w*)\]\".*?<td class='text-end'>(\d+)%</td>", re.S)


def staples(refresh=False):
    """{format: [{"name", "set", "decks": % of decks}]} for the top tournament cards."""
    cached = _load(STAPLES_FILE)
    if cached and not refresh and time.time() - cached["fetched"] < STAPLES_MAX_AGE:
        return cached["formats"]
    formats = dict((cached or {}).get("formats", {}))
    for fmt in FORMATS:
        try:
            r = requests.get(f"https://www.mtggoldfish.com/format-staples/{fmt}/full/all",
                             headers=HEADERS, timeout=20)
            r.raise_for_status()
        except requests.RequestException:
            continue  # keep yesterday's list for this format
        rows = _STAPLE.findall(r.text)
        if rows:
            formats[fmt] = [{"name": html.unescape(n), "set": s, "decks": int(p)} for n, s, p in rows]
        time.sleep(0.5)
    _save(STAPLES_FILE, {"fetched": time.time(), "formats": formats})
    return formats
