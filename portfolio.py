"""Portfolio tracker: the cards I own, what I paid, value today and profit after fees.

The cards are in watchlist.json under "portfolio":
  { "name": "Ragavan, Nimble Pilferer", "qty": 2, "paid": 40.0, "set": "MH2", "foil": false }
"paid" is the price for ONE card. Run: python portfolio.py
"""
import json
import sys

import requests

import alerts
from history import History

FEE = 0.05  # Cardmarket's selling commission (5%); change it here if it changes


def net(value, fee=FEE):
    """What is left of a sale price after the Cardmarket fee."""
    return value * (1 - fee)


def evaluate(entry, hist):
    """Returns the numbers of one portfolio line (prices for the whole quantity)."""
    qty = entry.get("qty", 1)
    query = dict(entry)
    query.setdefault("price", "trend")
    name, best = alerts.best_price(query, hist)
    if not best:
        raise ValueError("no price found")
    price, c = best
    paid = entry["paid"] * qty
    value = price * qty
    where = f"{c['set'].upper()} #{c['collector_number']}" + (" foil" if entry.get("foil") else "")
    return {"name": name, "where": where, "qty": qty, "paid": paid, "value": value,
            "after_fees": net(value), "profit": net(value) - paid}


def main():
    config = {}
    if alerts.WATCHLIST.exists():
        config = json.loads(alerts.WATCHLIST.read_text(encoding="utf-8"))
    entries = config.get("portfolio", [])
    if not entries:
        raise SystemExit('No "portfolio" in watchlist.json (see watchlist.example.json).')
    hist = History()
    rows, errors = [], []
    for e in entries:
        try:
            rows.append(evaluate(e, hist))
        except (ValueError, KeyError, requests.RequestException) as ex:
            errors.append(f"  ⚠ {e.get('name')}: {ex}")

    print(f"\nPortfolio (Cardmarket trend, {FEE:.0%} fee, prices up to {hist.guide_date})\n")
    print(f"{'Card':32} {'Printing':12} {'Qty':>3} {'Paid':>9} {'Value':>9} {'After fees':>10} {'Profit':>9}")
    print("─" * 92)
    for r in rows:
        print(f"{r['name'][:32]:32} {r['where']:12} {r['qty']:>3} {r['paid']:>7.2f} € "
              f"{r['value']:>7.2f} € {r['after_fees']:>8.2f} € {r['profit']:>+7.2f} €")
    if rows:
        paid = sum(r["paid"] for r in rows)
        profit = sum(r["profit"] for r in rows)
        print("─" * 92)
        print(f"{'Total':32} {'':12} {'':>3} {paid:>7.2f} € {sum(r['value'] for r in rows):>7.2f} € "
              f"{sum(r['after_fees'] for r in rows):>8.2f} € {profit:>+7.2f} €"
              + (f"  ({profit / paid:+.1%})" if paid else ""))
    if errors:
        print("\n" + "\n".join(errors))
    sys.exit(1 if errors and not rows else 0)


if __name__ == "__main__":
    main()
