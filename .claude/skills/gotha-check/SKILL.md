---
name: gotha-check
description: Start the Gotha app, check that it works (pages, search, sealed product pages, tracker, news, card lookup) and stop it again. Use after changing the code, or when asked to run, start, launch, test or check the app. The project has no automated tests, so this is how to confirm a change works.
---

# Gotha check

The project has no automated tests. This skill starts the app's server,
calls its pages and API, reports PASS or FAIL for each check, and stops the server.

## Run it

From the project folder (use `python3` on Linux if `python` is missing):

```
python .claude/skills/gotha-check/check.py
```

- It starts `server.py` on port 8790 (or the next free one), so it doesn't clash
  with a Gotha window I have open on 8765.
- The first start of the day downloads fresh price data. That takes a few minutes,
  so run it in the background, or with a long timeout (10 minutes).
- The server's own lines are shown with `server |` in front.

Add options for the part of the code you changed:

| Changed | Add |
|---|---|
| Card lookup: `main.py`, `chart.py`, `web/index.html`, `/api/card` | `--card "Ragavan, Nimble Pilferer"` |
| The terminal tool or the price history: `main.py`, `history.py` | `--cli "Ragavan, Nimble Pilferer"` |
| Nothing about news, and the feeds are failing or there's no internet | `--no-news` |

`--card` rewrites `web/data.js` for the test and puts my last card back afterwards.

`--url http://127.0.0.1:PORT/` checks a server that is already running.
Remember that a server started before the change still runs the old code.

## What it checks

- The app page (`/`) and the chart page (`/index.html`) load.
- `/api/info`: guide date, number of products, days of sealed history.
- Search: "ragavan nimble pilferer" gives that card first, "bloomburow" finds the set BLB,
  the sealed gainers list works, and page 2 of the results is different from page 1.
- A sealed product page: history, outlook and the other products of the set.
- The sealed tracker and the market news.
- A bad request gives 400, and an unknown API path gives 404.
- The server printed no Python errors (`Traceback`).

If a check fails, read its FAIL line and the `server |` lines above it, fix the code, and run it again.

## Changes to the web pages

The script only checks that `app.html` and `index.html` load. It can't see the layout or the JavaScript.
If a browser tool is available, start `python server.py --no-browser` in the background,
open the address it prints, and look. Otherwise tell me the page wasn't checked by eye.

## Careful

- Never run `alerts.py`, not even `--test`. It sends real notifications to my phone.
- Don't call `/api/track` or `/api/untrack`. They change `watchlist.json`, which is my real list.
  If a change touches tracking, copy `watchlist.json` first, test, put it back,
  and make sure it is the same as the copy.
- Starting the server saves today's sealed prices in `data/sealed_history.sqlite`
  and may refresh `cache/`. That is normal, the same as when I open the app.
  Those files are tracked in git: don't commit them unless I ask.
- On Windows, if Gotha is already open, a search index rebuild can fail because the file is in use.
  If the server says so, ask me to close Gotha.

## Report

Tell me the summary line (`N passed, N failed`), the guide date, and any FAIL lines in plain words.
