#!/usr/bin/env python3
"""
Daily price alerts: checks the cards in watchlist.json against Cardmarket's
price guide and sends a phone notification (via ntfy.sh) for every card that
reached its target price.

Usage:
    python alerts.py           (check the watchlist and notify)
    python alerts.py --test    (just send a test notification)

Meant to be run once a day by Windows Task Scheduler (see README).
"""
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import requests

import main
from history import GUIDE_FIELDS, History

BASE_DIR = Path(__file__).resolve().parent
WATCHLIST = BASE_DIR / "watchlist.json"
LOG = BASE_DIR / "cache" / "alerts.log"
NTFY = "https://ntfy.sh"


def notify(topic, title, message, click=None):
    """Sends a push notification to everyone subscribed to the ntfy topic."""
    headers = {"Title": title, "Tags": "moneybag"}
    if click:
        headers["Click"] = click  # tapping the notification opens this link
    r = requests.post(f"{NTFY}/{topic}", data=message.encode("utf-8"),
                      headers=headers, timeout=15)
    r.raise_for_status()


def best_price(card, hist):
    """Cheapest price of the card across its printings, as (price, printing).

    Uses the watchlist entry's "price" field (default "low" = cheapest listing),
    only foil or non-foil ("foil": true/false), and only one set if "set" is given.
    """
    field = card.get("price", "low")
    if field not in GUIDE_FIELDS:
        raise ValueError(f"'price' must be one of {', '.join(GUIDE_FIELDS)}")
    kind = "foil" if card.get("foil") else "normal"
    exact_name = main.resolve_name(card["name"])
    if not exact_name:
        raise ValueError(f"card not found: {card['name']}")

    best = None
    for c in main.get_printings(exact_name):
        if card.get("set") and c["set"].upper() != card["set"].upper():
            continue
        price = hist.guide(c.get("cardmarket_id")).get(kind, {}).get(field)
        if price and (best is None or price < best[0]):
            best = (price, c)
    return exact_name, best


def check(config, hist):
    """Returns (text lines of the cards that hit their target, link of the first one)."""
    hits, errors, link = [], [], None
    for card in config["cards"]:
        try:
            name, best = best_price(card, hist)
        except (ValueError, requests.RequestException) as e:
            errors.append(f"⚠ {card.get('name')}: {e}")
            continue
        if not best:
            print(f"  {name}: no price found")
            continue
        price, c = best
        where = f"{c['set'].upper()} #{c['collector_number']}" + (" foil" if card.get("foil") else "")
        print(f"  {name}: {price:.2f} € ({where})")
        if "below" in card and price <= card["below"]:
            hits.append(f"↓ {name}: {price:.2f} € ({where}), target ≤ {card['below']:.2f} €")
        elif "above" in card and price >= card["above"]:
            hits.append(f"↑ {name}: {price:.2f} € ({where}), target ≥ {card['above']:.2f} €")
        else:
            continue
        link = link or (c.get("purchase_uris") or {}).get("cardmarket")
    return hits, errors, link


def run():
    config = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    topic = config["ntfy_topic"]

    if "--test" in sys.argv:
        notify(topic, "Gotha test", "Price alerts are working!")
        print(f"Test notification sent to topic '{topic}'.")
        return

    print(f"\n=== {datetime.now():%Y-%m-%d %H:%M} ===")
    hist = History()
    print(f"Price guide from {hist.guide_date}")
    hits, errors, link = check(config, hist)

    if hits or errors:
        title = f"Gotha: {len(hits)} card(s) hit your price" if hits else "Gotha: price check problems"
        notify(topic, title, "\n".join(hits + errors), click=link if len(hits) == 1 else None)
        print(f"Notification sent ({len(hits)} hit(s), {len(errors)} error(s)).")
    else:
        print("No card hit its target; no notification sent.")


if __name__ == "__main__":
    # When run by Task Scheduler with pythonw there is no console: write to a log file
    if sys.stdout is None or Path(sys.executable).stem.lower() == "pythonw":
        LOG.parent.mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8")
    try:
        run()
    except Exception as e:
        traceback.print_exc()
        # Let the phone know the daily check failed, if we can
        try:
            topic = json.loads(WATCHLIST.read_text(encoding="utf-8"))["ntfy_topic"]
            notify(topic, "Gotha: price check failed", str(e))
        except Exception:
            pass
        sys.exit(1)
