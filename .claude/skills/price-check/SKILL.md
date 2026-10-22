---
name: price-check
description: Answer price questions about MTG cards and sealed products from Gotha's local data - the price of a card or booster box, whether something is a good buy or sell, which products are rising or falling this week, or how a product's price moved over time. Also use it to try opportunity-scanner rules on the real data before coding them.
---

# Price check

Answers price questions from Gotha's local databases.
It doesn't start the app, so answers are instant and nothing is downloaded or changed.

All commands run from the project folder.
Use `python` (on Linux, `python3` if `python` is missing).

## 1. Always check how fresh the data is

```
python .claude/skills/price-check/query.py status
```

Tell me the date of the prices in your answer.
If the prices are older than yesterday, say so. Only refresh them if I agree:
starting the app downloads new data (~2-3 minutes) and changes the files in `cache/` and `data/`.

## 2. Look things up

| Question | Command |
|---|---|
| Find a card or product and its id | `query.py find TEXT [--kind card or sealed]` |
| Prices of every printing of a card | `query.py card NAME` (or `--id METACARD`) |
| ...plus the trend price 7, 30 and 60 days ago | add `--history` (asks Scryfall, a few seconds) |
| A sealed product: today and 7 / 30 / 90 / 180 / 365 days ago | `query.py sealed NAME` (or `--id PRODUCT`) |
| Biggest gainers of the week | `query.py movers [--kind card or sealed] [--min EUR]` |
| Biggest droppers of the week | `query.py movers --down` |

`card` and `sealed` take the best match by name and list the other matches with their ids.
If the match is wrong, run it again with `--id`.

## 3. How to read the numbers

- **From**: the cheapest listing right now, in any condition or language.
  It can be a damaged or foreign copy, so it is not the normal buy price.
- **Trend**: Cardmarket's trend price. Gotha uses it as "the price".
- **Avg1 / Avg7 / Avg30**: the average sale price over the last 1, 7 and 30 days.
- **7v30**: Avg7 compared with Avg30. The app's outlook counts ±15% as a full "rising" or "falling" signal.
- **Week** (in `find` and `movers`):
  - cards: Avg7 vs Avg30 of the cheapest printing;
  - sealed: today's price vs the price Gotha recorded at least 7 days ago.
  It is computed when the search index is built, so it is as old as `status` says.
- Sealed products have no Avg1/7/30 on Cardmarket. Use their recorded history instead.

## 4. Be careful with

- **Thin markets.** One sale can move the numbers a lot.
  Big jumps on rare or odd products (full sets, sample packs, +100% in a week) are often noise.
  Check the history (`sealed`, or `card --history`) before trusting a jump.
- **Silly listings.** A From price like 19999.99 with no Trend is a joke listing. Ignore it.
- **Fees.** When talking about profit, remember Cardmarket's fees and shipping.
  Ask me for my numbers. Don't guess them.
- **No promises.** Give the numbers and what they suggest, in plain words.
  It's not financial advice, and the answer should say how sure it is.

For a sealed product's full outlook (momentum, product age, reprint risk, tournament play, news),
start the app with the gotha-check skill and read `/api/sealed?id=PRODUCT` → `outlook`.

## 5. Your own queries (for example to try scanner rules)

Open the databases **read-only**:

```python
import sqlite3
con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
```

- `cache/prices.sqlite`
  - `guide(id_product, low, trend, avg1, avg7, avg30, avg, low_foil, trend_foil, ...)`: today's Cardmarket price guide, **in cents**
  - `meta(key, value)`: `guide_date`
  - `prices(scryfall_id, data)`: 90-day history per printing, keyed by the Scryfall id as 16 bytes; decode `data` with `history._unpack`
- `cache/catalog.sqlite`
  - `entries(kind, ref, name, norm, category, id_expansion, printings, price, released, change)`:
    `kind` is `card` or `sealed`; `ref` is the metacard id for a card and the product id for a sealed product; `price` is **in euros**
  - `singles(id_product, id_metacard, id_expansion, name)`: every card printing
  - `expansions(id, code, name, released, type)`
- `data/sealed_history.sqlite`
  - `history(id_product, day, low, trend, avg1, avg7, avg30, avg)`: one row per sealed product per day, **in cents**

Joins: `guide.id_product` = `singles.id_product` (card printings) = `entries.ref` (sealed products).

Never write to these files. `data/sealed_history.sqlite` can't be rebuilt.
