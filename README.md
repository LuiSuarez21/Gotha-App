# Gotha — MTG Price Tracker

Gotha shows **Cardmarket prices (EUR)** for Magic: The Gathering cards and sealed products, keeps a price history and tracks what you own. It runs on your own computer and uses only free data.

## What it does

- **Search** every Magic product on Cardmarket (~128,000 cards and sealed products) as you type, in a browser app.
- **Card prices**: every printing of a card, normal and foil, today and 7 / 15 days ago, with a 90-day chart.
- **Sealed prices**: booster boxes, bundles, decks… Gotha saves their prices every day, because nobody publishes this history for free.
- **Tracker and portfolio**: what you own, what you paid, its value today and your profit or loss.
- **Price outlook** for sealed products: a simple score (price trend, product age, reprint risk, tournament play, news) that shows its reasons. It is not financial advice.
- **Market news** from MTGGoldfish, Google News and Reddit.
- **Phone alerts** when a price hits your target (off by default).

## Getting started

You need **Python 3.9+**.

```bash
python run.py install                        # installs requests, the only library needed
copy watchlist.example.json watchlist.json   # your cards and products (cp on Mac/Linux)
python run.py app                            # opens the app at http://127.0.0.1:8765
```

The first run of each day downloads the prices. It takes about 2 minutes. Stop the app with Ctrl+C.

## Commands

All commands are in [`run.py`](run.py), like the scripts of a `package.json`. Run `python run.py` to see the list.

| Command | What it does |
|---|---|
| `python run.py app` | Start the browser app (`app:nobrowser` doesn't open the browser) |
| `python run.py card "Ragavan, Nimble Pilferer"` | Look up a card in the terminal and open its chart (`card:text` for the terminal only) |
| `python run.py portfolio` | Value of the cards you own and profit after fees |
| `python run.py status` | How fresh the local price data is |
| `python run.py movers` | Biggest price risers this week (add `--down` for fallers) |
| `python run.py check` | Start the app, test its pages and stop it |
| `python run.py backfill` | One-time import of past sealed prices (~680 MB; `backfill:list` shows what) |
| `python run.py alerts:test` | Send a test notification to your phone |
| `python run.py alerts` | Check your targets and send the phone notifications |

## Your data

Your cards, sealed products and alert targets are in `watchlist.json`:

```json
{
  "cards":     [{ "name": "Kasmina, Enigma Sage", "below": 3.0 }],
  "sealed":    [{ "id": 758483, "name": "Modern Horizons 3 Collector Booster Box", "qty": 2, "paid": 650.0 }],
  "portfolio": [{ "name": "Ragavan, Nimble Pilferer", "qty": 1, "paid": 40.0, "set": "MH2" }]
}
```

- `cards`: alert when the price is `below` or `above` a value. Optional: `set`, `foil` and `price` (`low`, `trend`, `avg1`, `avg7` or `avg30`).
- `sealed`: added with the **Track** button in the app.
- `portfolio`: cards you own. `paid` is the price of one card.

**Phone alerts** use [ntfy](https://ntfy.sh) (free, no account): install the app, put a random topic name in `ntfy_topic.txt`, subscribe to it in the app and run `python run.py alerts:test`. The topic works like a password, so keep it secret. A Windows task, *Gotha price alerts*, can run the check every day at 9:00. It is off by default.

Your data is **not** in git: `watchlist.json`, `ntfy_topic.txt`, `data/`, `cache/` and `web/data.js`. `cache/` can be deleted at any time; it is downloaded again. **Back up `data/`**: it holds the sealed price history and can't be downloaded again.

## How it works

| Source | Gives |
|---|---|
| [Scryfall](https://scryfall.com) | Card names, printings and pictures |
| [MTGJSON](https://mtgjson.com) | 90 days of Cardmarket card prices, and set data |
| [Cardmarket](https://www.cardmarket.com) | Free daily product lists and price guide |
| [MTGGoldfish](https://www.mtggoldfish.com), Google News, Reddit | Tournament staples and news |

The data is saved in small local SQLite databases and refreshed once a day. "Today" means the newest day in the data, not your computer's clock.

| File | Does |
|---|---|
| `server.py` | The browser app (`web/app.html`) and its API |
| `main.py`, `chart.py` | Card lookup in the terminal and its chart page (`web/index.html`) |
| `history.py` | Card price history and price guide (`cache/prices.sqlite`) |
| `catalog.py` | Search index (`cache/catalog.sqlite`) |
| `sealed.py` | Sealed price history, tracker and outlook (`data/sealed_history.sqlite`) |
| `market.py` | News and tournament staples |
| `portfolio.py`, `alerts.py`, `backfill.py` | Portfolio, phone alerts and the import of past sealed prices |
| `run.py` | All the commands |

Prices are Cardmarket prices in EUR only. Charts by [Chart.js](https://www.chartjs.org).
This project is not affiliated with Wizards of the Coast, Scryfall, MTGJSON or Cardmarket.
