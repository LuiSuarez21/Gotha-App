"""Portfolio of the cards you own: quantity, price paid, value today and
profit or loss after the Cardmarket selling fee.

The cards are kept in watchlist.json under "owned". Each one is a printing,
stored with its Cardmarket product id (the key of the price guide).
"""

import sealed

SELL_FEE = 0.05  # Cardmarket's commission on a sale (5%); change it here if it changes


def owned():
    return sealed.load_watchlist().get("owned", [])


def _key(card):
    return card["id"], bool(card.get("foil"))


def own(entry):
    """Adds or updates an owned card: {"id", "name", "set", "number", "foil", "qty", "paid"}."""
    config = sealed.load_watchlist()
    items = [c for c in config.get("owned", []) if _key(c) != _key(entry)]
    items.append({k: v for k, v in entry.items() if v not in (None, "", False)})
    config["owned"] = items
    sealed._write_watchlist(config)


def disown(id_product, foil=False):
    config = sealed.load_watchlist()
    config["owned"] = [c for c in config.get("owned", []) if _key(c) != (id_product, bool(foil))]
    sealed._write_watchlist(config)


def rows(hist):
    """The owned cards with today's price, value, and profit/loss after fees."""
    out = []
    for c in owned():
        g = hist.guide(c["id"]).get("foil" if c.get("foil") else "normal", {})
        price = g.get("trend") or g.get("low")
        qty = c.get("qty", 1)
        net = round(price * (1 - SELL_FEE), 2) if price else None  # what you receive per card
        out.append({
            "id": c["id"], "name": c["name"], "set": c.get("set"), "number": c.get("number"),
            "foil": bool(c.get("foil")), "qty": qty, "paid": c.get("paid"),
            "price": price, "net": net, "value": round(price * qty, 2) if price else None,
            "pl": round((net - c["paid"]) * qty, 2) if net is not None and c.get("paid") else None,
        })
    return out
