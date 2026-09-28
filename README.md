# Gotha — MTG Card Price Fetcher

A small Python command-line tool that looks up any **Magic: The Gathering** card and shows its **Cardmarket prices (EUR)** for every printing of that card: today's price, the price 7 and 15 days ago, and an interactive chart of the last 90 days.

Example output:

```
Ragavan, Nimble Pilferer — 12 printings  (Cardmarket prices up to 2026-09-27)

Edition                        Set   No.   │ Normal today     7D ago    15D ago │  Foil today     7D ago    15D ago
───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
Final Fantasy: Through the Ag  FCA   43    │     32.84 €    34.08 €    32.51 € │    176.74 €   158.04 €   158.04 €
Magic Online Promos            PRM   99679 │         -          -          -   │         -          -          -
Modern Horizons 2 Timeshifts   H2R   11    │     45.40 €    45.78 €    42.24 € │     87.84 €    87.74 €    83.86 €
...
```

---

## Features

- **Fuzzy card search**: small typos are tolerated (`"kasmina enigma"` → *Kasmina, Enigma Sage*).
- **All printings**: every edition, promo and special version of the card, in release order.
- **Normal and foil prices**: today, 7 days ago and 15 days ago, side by side.
- **Cheapest / most expensive** non-foil printing and a direct Cardmarket link.
- **Cardmarket price guide** on the chart page: the cheapest listing ("From"), the trend, and the 1/7/30-day average sale prices, for normal and foil.
- **Live chart page**: a single web page (Chart.js) with the 90-day price history per printing, price cards, and a colour-coded table (green = price went up, red = went down). It opens once in Firefox (or the default browser) and then **updates itself** every time you search for another card.
- **Daily phone alerts**: get a push notification when a card on your watchlist drops below (or rises above) a target price.
- **Small local cache**: the large price dataset is downloaded at most once a day and stored in a compressed SQLite database (~22 MB), so later lookups are instant.

---

## Installation & usage

Requires **Python 3.9+** and the `requests` library:

```bash
pip install requests
```

Run it:

```bash
python main.py                              # interactive mode: asks for card names until you press Enter
python main.py "Ragavan, Nimble Pilferer"   # look up one card
python main.py "Ragavan" --no-chart         # skip the chart page
```

The **first run** (and the first run of each day) downloads the price history from MTGJSON, which takes about 2 minutes. After that, lookups only hit the Scryfall API and are fast.

---

## Project structure

```
Gotha (MTG Fetch Prices App)/
├── main.py          # Entry point: CLI, Scryfall API calls, terminal table
├── history.py       # Downloads MTGJSON price history and caches it in SQLite
├── chart.py         # Writes the chart data for the web page and opens it in the browser
├── alerts.py        # Daily price check: sends phone notifications for the watchlist
├── watchlist.json   # Your cards and target prices (+ your private ntfy topic)
├── web/
│   ├── index.html   # The chart page (fixed template, never rewritten)
│   └── data.js      # Generated: data of the last card searched (rewritten on each search)
└── cache/
    ├── prices.sqlite   # Generated: compressed price history + price guide (refreshed daily)
    └── alerts.log      # Generated: output of the scheduled daily checks
```

`cache/` and `web/data.js` are created automatically and can be deleted safely at any time.

---

## How it works

The app combines two free data sources:

| Source | Used for |
|---|---|
| [Scryfall API](https://scryfall.com/docs/api) | Card name resolution, list of printings, images, current prices, Cardmarket links |
| [MTGJSON](https://mtgjson.com/) | Daily Cardmarket price history (last ~90 days) for every card |
| [Cardmarket price guide](https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_1.json) | Today's lowest price, trend and 1/7/30-day averages for every product (free daily file, no login) |

### Flow of a lookup

```
 user types a name
        │
        ▼
 resolve_name()       ── Scryfall /cards/named?fuzzy=…   → official card name
        │
        ▼
 get_printings()      ── Scryfall /cards/search (unique=prints, paginated) → all printings
        │
        ▼
 History.get(id)      ── local SQLite lookup by Scryfall id → {normal: {date: price}, foil: {...}}
        │
        ▼
 price_on(series, 7/15) ── price on (latest date − N days)
        │
        ├──► terminal table  (main.py)
        └──► web/data.js     (chart.py) ──► open tab of web/index.html redraws itself
```

### `main.py`: the CLI

- **`resolve_name(name)`** calls Scryfall's fuzzy search to get the exact card name, or `None` if nothing matches.
- **`get_printings(exact_name)`** searches for `!"<exact name>"` with `unique=prints`, following `next_page` until all results are collected. If the search returns 404 (cards that only exist as promos/digital), it retries with `include_extras=true`.
- **`show_card(...)`** ties it together:
  1. attaches the price history to each printing;
  2. finds the **reference date** (`ref`): the most recent date present in any history, so "7 days ago" is relative to the latest data, not the computer's clock;
  3. computes today / 7D / 15D prices for normal and foil;
  4. falls back to Scryfall's current `eur` / `eur_foil` price if there is no history;
  5. prints the table, min/max, Cardmarket link, and updates the chart page.
- A short pause (`PAUSE = 0.1 s`) after every request respects Scryfall's rate-limit guidelines, and a custom `User-Agent` is sent as they request.

### `history.py`: price history & cache

MTGJSON's `AllPrices.json.xz` contains 90 days of prices for *every* card from *every* store, which is hundreds of MB once decompressed. To keep this manageable:

- **Daily refresh:** `History()` checks the age of `cache/prices.sqlite`. If it's missing or older than 20 hours (`MAX_AGE_HOURS`), it rebuilds it. If the download fails but an old cache exists, it prints a warning and uses the old data.
- **Streaming parser:** `_read_prices()` reads the compressed JSON in chunks and uses `json.JSONDecoder.raw_decode` to decode **one card at a time**, instead of loading the whole file into memory. It keeps only `paper → cardmarket → retail` prices.
- **ID mapping:** MTGJSON uses its own UUIDs, while Scryfall uses different ids. `cardIdentifiers.csv.xz` provides the `uuid → scryfallId` mapping so the two sources can be joined. Several MTGJSON entries can point to the same Scryfall card, so their prices are merged.
- **Safe write:** the database is built in a `.tmp` file and only then renamed over the real one, so an interrupted update never leaves a broken cache. The downloaded raw files are deleted afterwards.
- **Price guide:** the same daily rebuild also downloads Cardmarket's price guide (~26 MB JSON) and saves it in a `guide` table, keyed by Cardmarket product id. Scryfall gives each printing a `cardmarket_id`, which is the same id, so `History.guide(cardmarket_id)` finds a printing's numbers directly. Prices are stored as whole cents; a missing or `0` value becomes `None`. The guide's date is kept in a small `meta` table. If an older cache has no `guide` table, it is rebuilt automatically.
- **`price_on(series, days_ago, reference)`** returns the price on the target date, or the last known price **before** it (handles missing days in the data).

#### Compact storage format

The price history table:

```sql
CREATE TABLE prices (scryfall_id BLOB PRIMARY KEY, data BLOB) WITHOUT ROWID
```

Each printing's 90-day history is packed by `_pack()` and unpacked by `_unpack()`:

1. **Dates become positions.** Instead of repeating `"2026-06-25": 3.07` for every day, it stores a single `start` date and a plain list with one price per day. Days with no data are `null`.
2. **Prices become whole cents** (`3.07` → `307`), which are shorter and exact.
3. **The JSON is compressed with zlib** (level 9). Prices often stay the same for days, and zlib compresses repeated values very well.
4. **The key is 16 raw bytes.** The Scryfall id (`f6555d1f-d4cf-…`, 36 characters) is stored as its 16-byte binary form using `uuid.UUID(...).bytes`.
5. **`WITHOUT ROWID`** makes SQLite store rows directly ordered by `scryfall_id`, instead of a hidden row table plus a separate index on the key.

```
before:  {"normal": {"2026-06-25": 3.07, "2026-06-26": 3.07, ...}, "foil": {...}}   ~2.7 KB per printing
after:   zlib({"start":"2026-06-25","normal":[307,307,...],"foil":[...]})           ~150 bytes per printing
```

The price history went from **355 MB to about 18 MB** (the price guide adds ~4 MB). `History.get()` unpacks the data back into the same `{date: price}` shape, so the rest of the code didn't need to change.

### `chart.py`: the chart page

The page `web/index.html` is a **fixed template**: Python never rewrites it. Only the data changes:

- **`generate(...)`** reduces each printing to only what the page needs (set, number, image, link, prices, history), serialises it to JSON and writes it to `web/data.js` as a function call: `load({...});`. It also stores a timestamp (`generated`) so the page knows when the data is new.
- **Live update:** every 1.5 s the page reloads `data.js` by adding a `<script src="data.js?t=…">` tag (the `?t=` stops the browser from using a cached copy). The script calls `load(data)`, which redraws everything only if the timestamp changed. A `<script>` tag is used instead of `fetch()` because browsers block `fetch()` on local `file://` pages, but still allow loading scripts.
- The page loads Chart.js from a CDN. It includes:
  - card image, printing selector, and price cards (today / 7 / 15 days ago);
  - a **Cardmarket price guide** table for the selected printing: From (cheapest listing), Trend, 1-day, 7-day and 30-day average sale price, for normal and foil;
  - a line chart of normal vs. foil prices, with markers on the dates used in the table;
  - a table of all printings (click a row to chart that printing);
  - automatic light/dark theme;
  - a "waiting for a card" message if no card has been searched yet.
  - By default it shows the **cheapest non-foil printing**.
- **`open_page(page)`** looks for Firefox in the usual Windows install folders and opens the page in a new tab; otherwise it uses Python's `webbrowser` module. It only opens the page **once per session**; later searches just update the open tab. (If you close the tab, open `web/index.html` again and it will show the last card searched.)

---

## Daily price alerts (phone notifications)

`alerts.py` checks every card in `watchlist.json` against today's Cardmarket price guide and sends **one** push notification listing the cards that hit their target. If no card hits its target, nothing is sent.

### 1. Get the notifications on your phone

Notifications use [ntfy](https://ntfy.sh), which is free and needs no account:

1. Install the **ntfy** app ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iPhone](https://apps.apple.com/app/ntfy/id1625396347)).
2. Tap **+** and subscribe to the topic in `watchlist.json` (`ntfy_topic`, e.g. `gotha-ede464547619`).
3. Test it: `python alerts.py --test`

The topic name works like a password: anyone who knows it can read your alerts, so keep it random.

### 2. Choose your cards

Edit `watchlist.json`:

```json
{
  "ntfy_topic": "gotha-ede464547619",
  "cards": [
    { "name": "Kasmina, Enigma Sage", "below": 3.00 },
    { "name": "Ragavan, Nimble Pilferer", "below": 30.00, "set": "MH2" },
    { "name": "Ragavan, Nimble Pilferer", "above": 150.00, "foil": true, "price": "trend" }
  ]
}
```

| Field | Meaning |
|---|---|
| `name` | Card name (fuzzy search, like in `main.py`) |
| `below` / `above` | Notify when the price is **≤** / **≥** this value (EUR) |
| `foil` | `true` to check foil prices (default: non-foil) |
| `set` | Only check this set code (e.g. `MH2`); otherwise the **cheapest** printing is used |
| `price` | Which price-guide value: `low` (cheapest listing, default), `trend`, `avg1`, `avg7` or `avg30` |

Because the cheapest printing is used by default, `above` alerts work best together with `set`: you want to know when *your* printing goes up.

Example notification:

```
Gotha: 2 card(s) hit your price
↓ Kasmina, Enigma Sage: 2.10 € (STX #196), target ≤ 3.00 €
↓ Ragavan, Nimble Pilferer: 25.75 € (FCA #43), target ≤ 30.00 €
```

If only one card hits its target, tapping the notification opens it on Cardmarket. If the check itself fails (e.g. no internet), you get a "price check failed" notification instead.

### 3. Run it every day

Alerts are **off by default**. A Windows Task Scheduler task called **Gotha price alerts** (currently disabled; turn it on with `Enable-ScheduledTask -TaskName "Gotha price alerts"`) runs `alerts.py` every day at **9:00** with `pythonw.exe` (no console window). Its output goes to `cache/alerts.log`. If the PC is off at 9:00, it runs as soon as the PC is on and has internet.

To create it (PowerShell):

```powershell
$dir = "C:\path\to\Gotha (MTG Fetch Prices App)"
$action = New-ScheduledTaskAction -Execute "C:\Python314\pythonw.exe" -Argument "`"$dir\alerts.py`"" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At 9:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable
Register-ScheduledTask -TaskName "Gotha price alerts" -Action $action -Trigger $trigger -Settings $settings
```

- Change the time: open **Task Scheduler** → *Gotha price alerts* → *Triggers*.
- Run it now: right-click the task → **Run**, then check `cache/alerts.log`.
- Remove it: `Unregister-ScheduledTask -TaskName "Gotha price alerts"`

The PC has to be on for the check to run. The daily check also refreshes the price cache, so the first search of the day in `main.py` will be fast.

---

## Notes & limitations

- Prices are **Cardmarket prices in EUR** only. The terminal table and the 90-day chart use the **trend** price; the lowest price and the averages only appear in the price guide panel, and only for today (the guide has no history).
- History depends on MTGJSON, which updates once per day, so "today" means the latest day available in the dataset.
- Some printings (e.g. digital-only or very obscure promos) have no Cardmarket data and are shown as `-`.
- Fuzzy search picks Scryfall's best match, which isn't always the card you meant: `"ragavan"` matches the *Ragavan* token, not *Ragavan, Nimble Pilferer*. Use the full name when in doubt.
- The chart page shows **one card at a time** (the last one searched).
- In interactive mode the browser opens only once. Running `python main.py "Card"` once per card opens a new tab on each run, because each run is a separate program. Tabs that are already open still update.

## Credits

Card data and images from [Scryfall](https://scryfall.com). Price history from [MTGJSON](https://mtgjson.com). Charts by [Chart.js](https://www.chartjs.org).
This project is not affiliated with Wizards of the Coast, Scryfall, MTGJSON or Cardmarket.
