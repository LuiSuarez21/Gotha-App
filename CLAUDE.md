# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Gotha is a small Python app for Magic: The Gathering prices.
It shows Cardmarket prices (in EUR) for cards and sealed products,
keeps a price history, tracks sealed products I own, and shows market news.

The README explains everything in detail. Read it before big changes.

## How to run it

- Python 3.9 or newer. The only extra library is `requests` (`pip install requests`).
- Card lookup in the terminal: `python main.py "Card name"`
- The browser app: `python server.py` (opens http://127.0.0.1:8765)
- No browser available (for example in a cloud session):
  `python main.py "Card name" --no-chart` and `python server.py --no-browser`
- The first run of each day downloads the price data. It takes about 2 minutes.

There are no automated tests and no linter. To check a change, run the app and try it.

## Main files

- Programs you run: `main.py` (terminal), `server.py` (browser app),
  `alerts.py` (daily check, see Rules), `backfill.py` (one-time import of old sealed prices).
- Modules: `history.py` (price data), `catalog.py` (search index),
  `sealed.py` (sealed history, tracker, outlook), `market.py` (news, tournament staples),
  `chart.py` (writes `web/data.js`).
- `watchlist.json` - my cards, my sealed products and my alert targets.
- `web/app.html` - the browser app. `web/index.html` - the chart page.

## How the parts fit together

- There are three databases:
  - `cache/prices.sqlite` (history.py): 90 days of card prices from MTGJSON,
    plus today's Cardmarket price guide.
  - `cache/catalog.sqlite` (catalog.py): the search index. It is built from Cardmarket's
    product lists, MTGJSON's set list, the guide in `prices.sqlite` and the sealed history.
  - `data/sealed_history.sqlite` (sealed.py): sealed prices, one row per product per day.
    Gotha records this itself, because nobody publishes it for free.
- So the start order matters: `History()` first, then `Catalog()`,
  then `sealed.Tracker().record(hist, catalog.sealed_ids())`.
  `server.py` and `alerts.py` both do it in this order.
- A cache database is rebuilt when it is older than 20 hours (`MAX_AGE_HOURS` in history.py).
  It is built in a `.tmp` file and then renamed. If the download fails, the old file is used.
- If you change the tables of `catalog.sqlite`, raise `VERSION` in catalog.py to force a rebuild.
  On Windows the rebuild fails while another `server.py` is running (the file is in use).
- Two kinds of ids:
  - Scryfall id: the key of the card price history (`History.get`).
  - Cardmarket product id (`idProduct`): the key of the price guide (`History.guide`),
    of sealed products and of the sealed history.
  - Scryfall gives each printing a `cardmarket_id`. It is the same number as `idProduct`,
    and it is how a printing is linked to its price guide.
  - In the search index, a card's `ref` is Cardmarket's metacard id (one entry per card,
    with all its printings). A sealed product's `ref` is its product id.
- Prices are stored as whole cents (integers). A missing or 0 price becomes `None`.
- "Today" means the newest date in the data, not the computer's clock
  (`ref` in main.py, `guide_date` in history.py).
- `server.py` answers each request in its own thread and shares the SQLite connections
  (`check_same_thread=False`). `card_lock` allows one Scryfall lookup at a time.
  `write_lock` protects `watchlist.json`.
- The chart page loads `web/data.js` with a `<script>` tag every 1.5 seconds, not with `fetch()`,
  because browsers block `fetch()` on `file://` pages.
  Both `main.py` and the app's `/api/card` rewrite `data.js` through chart.py.

## Rules

- Do not run `alerts.py`. It sends real notifications to my phone.
  Do not turn on the Windows task "Gotha price alerts". I will do it myself when I want.
- Never delete or overwrite `data/sealed_history.sqlite`.
  It holds the sealed price history and it can't be downloaded again.
- `cache/` and `web/data.js` are generated files. They can be deleted safely.
- `web/index.html` is a fixed page. Python only rewrites `web/data.js`.
- Be polite with the free data sources:
  keep the pause between Scryfall requests and the custom User-Agent,
  and download from the backfill archive one file at a time.
- Don't scrape Cardmarket pages. It is against their terms.
  Use only their free daily files (product lists and price guide).
- The `ntfy_topic` in `watchlist.json` works like a password. Don't share it.
- Keep code comments and the README simple and short, like they are now.

## Git

- The main branch is `main`. New work goes on a feature branch.
- Don't commit or push unless I ask.
- The databases in `cache/` and `data/` are tracked in git. Running the app changes them,
  so they show up in `git status`. Don't commit those changes unless I ask.
- After a fresh clone (for example in a cloud session) the cache files look new,
  so Gotha uses the old data inside them for up to 20 hours.
  Delete `cache/` to get fresh data. Never delete `data/`.

## Planned features

Ideas for making money buying and selling cards:

- Portfolio tracker: cards I own, price paid, value today, profit or loss after fees.
- Opportunity scanner: a daily scan of the Cardmarket price guide for price signals
  (for example the 7-day average rising fast compared with the 30-day average).
- Fee calculator: the sell price I need for X% profit after Cardmarket fees and shipping.
- Trade journal: a log of buys and sells, with results per month.
