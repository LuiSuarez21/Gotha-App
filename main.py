#!/usr/bin/env python3
"""
Cardmarket prices (EUR) for any MTG card, with the price 7 and 15 days ago
and a chart of the last 90 days.

Printings come from the Scryfall API; the daily Cardmarket price history
comes from MTGJSON (updated once a day and stored in cache/).

Usage:
    python main.py                         (asks for the card name)
    python main.py "Ragavan, Nimble Pilferer"
    python main.py "Ragavan" --no-chart

Requirements:
    pip install requests
"""
import sys
import time

import requests

import chart
from history import History, price_on

API = "https://api.scryfall.com"
# Scryfall asks for an identifiable User-Agent and Accept header
HEADERS = {"User-Agent": "MTGPrices/0.1", "Accept": "application/json"}
PAUSE = 0.1  # Scryfall asks for ~50-100 ms between requests

DAYS = (7, 15)  # "price X days ago" columns


def resolve_name(name):
    """Finds the official card name (tolerates small typos)."""
    r = requests.get(f"{API}/cards/named", headers=HEADERS,
                     params={"fuzzy": name}, timeout=15)
    time.sleep(PAUSE)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()["name"]


def get_printings(exact_name):
    """Returns every printing (edition) of the card."""
    url = f"{API}/cards/search"
    params = {"q": f'!"{exact_name}"', "unique": "prints", "order": "released"}
    cards = []
    while url:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        time.sleep(PAUSE)
        if r.status_code == 404 and params and "include_extras" not in params:
            # Special cards (promos, digital...) only show up with extras
            params["include_extras"] = "true"
            continue
        r.raise_for_status()
        data = r.json()
        cards.extend(data["data"])
        url = data.get("next_page") if data.get("has_more") else None
        params = None  # next_page already carries the parameters
    return cards


def fmt(price):
    return f"{float(price):>8.2f} €" if price else "       -  "


def show_card(name, hist, with_chart):
    exact_name = resolve_name(name)
    if not exact_name:
        print(f"Card not found: {name}")
        return

    printings = get_printings(exact_name)

    # Attach the daily history and the 7/15-day-old prices to each printing
    for c in printings:
        c["hist"] = hist.get(c["id"])
        c["guide"] = hist.guide(c.get("cardmarket_id"))
    dates = [d for c in printings for s in c["hist"].values() for d in s]
    ref = max(dates) if dates else None
    for c in printings:
        for kind in ("normal", "foil"):
            series = c["hist"].get(kind)
            c[kind] = {d: price_on(series, d, ref) for d in (0, *DAYS)} if ref else {}
        # Without history, use Scryfall's current price
        p = c.get("prices", {})
        c["normal"].setdefault(0, None)
        c["foil"].setdefault(0, None)
        c["normal"][0] = c["normal"][0] or (float(p["eur"]) if p.get("eur") else None)
        c["foil"][0] = c["foil"][0] or (float(p["eur_foil"]) if p.get("eur_foil") else None)

    print(f"\n{exact_name} — {len(printings)} printings"
          + (f"  (Cardmarket prices up to {ref})" if ref else "") + "\n")
    header = f"{'Edition':<30} {'Set':<5} {'No.':<5}"
    for label in ("Normal", "Foil"):
        header += f" │ {label + ' today':>11}" + "".join(f" {f'{d}D ago':>10}" for d in DAYS)
    print(header)
    print("─" * len(header))

    prices = []
    for c in printings:
        if c["normal"][0]:
            prices.append(c["normal"][0])
        line = f"{c['set_name'][:29]:<30} {c['set'].upper():<5} {c['collector_number'][:5]:<5}"
        for kind in ("normal", "foil"):
            v = c[kind]
            line += f" │ {fmt(v.get(0)):>11}" + "".join(
                f" {fmt(v.get(d)):>10}" for d in DAYS)
        print(line)

    print("─" * len(header))
    if prices:
        print(f"Cheapest (non-foil): {min(prices):.2f} €   "
              f"Most expensive: {max(prices):.2f} €")

    # Direct Cardmarket link (for the most recent printing)
    link = printings[-1].get("purchase_uris", {}).get("cardmarket") if printings else None
    if link:
        print(f"\nCardmarket: {link}")

    if with_chart:
        page = chart.generate(exact_name, printings, ref, DAYS, hist.guide_date)
        print(f"\nChart: {page}")
        chart.open_page(page)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    with_chart = "--no-chart" not in sys.argv
    hist = History()

    if args:
        show_card(" ".join(args), hist, with_chart)
        return

    # Interactive mode: keeps asking for cards until Enter is pressed on an empty line
    while True:
        try:
            name = input("\nCard name (Enter to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not name:
            break
        try:
            show_card(name, hist, with_chart)
        except requests.RequestException as e:
            print(f"Error contacting Scryfall: {e}")


if __name__ == "__main__":
    main()
