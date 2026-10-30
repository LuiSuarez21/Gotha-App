#!/usr/bin/env python3
"""
Gotha app: live search of every Magic card and sealed product, a sealed
product tracker with a price outlook, and the latest TCG market news.

Runs a small web server on this computer only (http://127.0.0.1:8765) and
opens the app in the browser. The search runs on a local index
(cache/catalog.sqlite), so results appear while you type.

Usage:
    python server.py               (start and open the browser)
    python server.py --no-browser  (start only)
    python server.py --port 9000

Requirements:
    pip install requests
"""
import json
import sys
import threading
import traceback
import webbrowser
from datetime import date, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

import chart
import main
import market
import portfolio
import sealed
from catalog import Catalog
from history import History

WEB_DIR = Path(__file__).resolve().parent / "web"
DEFAULT_PORT = 8765
SIBLING_DAYS = (7, 30)  # "days ago" columns of the other products of a set

hist = catalog = tracker = None
card_lock = threading.Lock()  # one Scryfall lookup at a time (their rate limit)
write_lock = threading.Lock()  # watchlist.json


def _num(value):
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


# ---------- API ----------

def api_search(q):
    return catalog.search(
        q.get("q", ""), kind=q.get("kind") or None, category=q.get("category") or None,
        set_code=q.get("set") or None, min_price=_num(q.get("min")), max_price=_num(q.get("max")),
        sort=q.get("sort", "relevance"), limit=min(int(q.get("limit") or 30), 100),
        offset=int(q.get("offset") or 0), no_extras=q.get("extras") == "hide")


def api_info(_):
    return {"index": catalog.meta, "guide_date": hist.guide_date,
            "days_recorded": tracker.days_recorded(),
            "sets": catalog.sets(), "categories": catalog.categories()}


def api_card(body):
    """Looks a card up on Scryfall and redraws the chart page (index.html) with it."""
    with card_lock:
        name, printings, ref = main.lookup(body["name"], hist)
    if not name:
        return {"error": f"Card not found on Scryfall: {body['name']}"}
    chart.generate(name, printings, ref, main.DAYS, hist.guide_date)
    return {"name": name, "printings": len(printings)}


def api_sealed(q):
    pid = int(q["id"])
    product = catalog.sealed_product(pid)
    if not product:
        return {"error": "Unknown sealed product"}
    guide = hist.guide(pid)
    history = tracker.history(pid)
    news = market.news()["items"]
    out = sealed.outlook(product, guide, history, catalog, hist, news, market.staples())
    mine = next((t for t in sealed.tracked() if t["id"] == pid), None)
    # The other sealed products of the set, with their recorded price N days ago
    siblings = catalog.set_sealed(product["id_expansion"])
    ago = {d: tracker.prices_on([s["ref"] for s in siblings],
                                (date.today() - timedelta(days=d)).isoformat()) for d in SIBLING_DAYS}
    for s in siblings:
        s["ago"] = {d: ago[d].get(s["ref"]) for d in SIBLING_DAYS}
    return {"product": product, "guide": guide.get("normal", {}), "history": history,
            "outlook": out, "tracked": mine, "siblings": siblings, "guide_date": hist.guide_date}


def api_tracked(_):
    rows = sealed.portfolio(catalog, hist, tracker)
    news, staples = market.news()["items"], market.staples()
    for r in rows:
        product = catalog.sealed_product(r["id"])
        if product:
            o = sealed.outlook(product, hist.guide(r["id"]), tracker.history(r["id"]),
                               catalog, hist, news, staples)
            r["outlook"] = {"label": o["label"], "score": o["score"]}
    return {"items": rows}


def api_track(body):
    product = catalog.sealed_product(int(body["id"]))
    if not product:
        return {"error": "Unknown sealed product"}
    with write_lock:
        sealed.track({"id": product["ref"], "name": product["name"],
                      "qty": int(_num(body.get("qty")) or 1), "paid": _num(body.get("paid")),
                      "below": _num(body.get("below")), "above": _num(body.get("above"))})
    return {"ok": True}


def api_untrack(body):
    with write_lock:
        sealed.untrack(int(body["id"]))
    return {"ok": True}


def api_owned(_):
    return {"items": portfolio.rows(hist), "fee": portfolio.SELL_FEE}


def api_own(body):
    """Adds an owned card. The printing is looked up on Scryfall by name (and set / number)."""
    qty, paid, foil = int(_num(body.get("qty")) or 1), _num(body.get("paid")), bool(body.get("foil"))
    with card_lock:
        name = main.resolve_name(body["name"])
        found = main.get_printings(name) if name else []
    if not name:
        return {"error": f"Card not found on Scryfall: {body['name']}"}
    want_set, want_no = (body.get("set") or "").strip().lower(), (body.get("number") or "").strip()
    found = [c for c in found if c.get("cardmarket_id")
             and (not want_set or c["set"] == want_set)
             and (not want_no or c["collector_number"] == want_no)]
    if not found:
        return {"error": f"No Cardmarket printing of {name} matches that set / number"}
    kind = "foil" if foil else "normal"
    # Without a set, take the cheapest printing (like the alerts do)
    best = min(found, key=lambda c: hist.guide(c["cardmarket_id"]).get(kind, {}).get("trend") or 1e9)
    with write_lock:
        portfolio.own({"id": best["cardmarket_id"], "name": name, "set": best["set"].upper(),
                       "number": best["collector_number"], "foil": foil, "qty": qty, "paid": paid})
    return {"ok": True, "name": name, "set": best["set"].upper()}


def api_disown(body):
    with write_lock:
        portfolio.disown(int(body["id"]), bool(body.get("foil")))
    return {"ok": True}


def api_news(q):
    return market.news(refresh=q.get("refresh") == "1")


GET = {"/api/search": api_search, "/api/info": api_info, "/api/sealed": api_sealed,
       "/api/tracked": api_tracked, "/api/owned": api_owned, "/api/news": api_news}
POST = {"/api/card": api_card, "/api/track": api_track, "/api/untrack": api_untrack,
        "/api/own": api_own, "/api/disown": api_disown}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt, *args):
        pass  # keep the console quiet (search sends a request per key press)

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _run(self, fn, arg):
        try:
            self._json(fn(arg))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            self._json({"error": f"Bad request: {e}"}, 400)
        except requests.RequestException as e:
            self._json({"error": f"Network error: {e}"}, 502)
        except Exception as e:  # report it to the page instead of dropping the connection
            traceback.print_exc()
            self._json({"error": str(e)}, 500)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in GET:
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            return self._run(GET[url.path], q)
        if url.path == "/":
            self.path = "/app.html"
        return super().do_GET()

    def end_headers(self):
        # data.js changes on every card lookup: make the browser always re-check files
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_POST(self):
        url = urlparse(self.path)
        if url.path not in POST:
            return self._json({"error": "Not found"}, 404)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as e:
            return self._json({"error": f"Bad JSON: {e}"}, 400)
        self._run(POST[url.path], body)


def start():
    global hist, catalog, tracker
    hist = History()
    catalog = Catalog()
    tracker = sealed.Tracker()
    n = tracker.record(hist, catalog.sealed_ids())
    print(f"Index: {catalog.meta.get('products')} products (built {catalog.meta.get('built')}); "
          f"sealed prices recorded for {hist.guide_date}: {n}, "
          f"{tracker.days_recorded()} day(s) of sealed history.")
    # Warm the news and tournament caches in the background
    threading.Thread(target=lambda: (market.news(), market.staples()), daemon=True).start()


def main_():
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else DEFAULT_PORT
    start()
    for p in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            break
        except OSError:
            continue
    else:
        sys.exit(f"No free port between {port} and {port + 9}.")
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Gotha is running at {url}  (Ctrl+C to stop)")
    if "--no-browser" not in args:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main_()
