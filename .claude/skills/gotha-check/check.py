#!/usr/bin/env python3
"""Starts a Gotha app server, checks its pages and API, then stops it (see SKILL.md).

    python check.py                 start a server, run the checks, stop the server
    python check.py --url URL       check a server that is already running instead
    python check.py --card NAME     also look a card up on Scryfall (POST /api/card)
    python check.py --cli NAME      also run the terminal tool (main.py NAME --no-chart)
    python check.py --no-news       skip the news feeds (slow or rate-limited at times)

Exits with code 0 when every check passed.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA_JS = ROOT / "web" / "data.js"
PORT = 8790  # not the app's usual 8765, so it doesn't meet a Gotha window you have open
START_TIMEOUT = 900  # the first start of the day downloads the price data (a few minutes)

# Talk to the local server directly, even if an HTTP proxy is set
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
results = []


def request(base, path, body=None):
    """(status, raw body) of a GET, or of a POST when `body` is given."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path.lstrip("/"), data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with OPENER.open(req, timeout=180) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def api(base, path, body=None):
    status, raw = request(base, path, body)
    out = json.loads(raw)
    if status != 200 or (isinstance(out, dict) and out.get("error")):
        raise AssertionError(f"HTTP {status}: {out.get('error') if isinstance(out, dict) else out}")
    return out


def check(name, fn):
    try:
        detail = fn()
        results.append((True, name, detail or ""))
    except Exception as e:  # any failure is reported, the other checks still run
        results.append((False, name, f"{e.__class__.__name__}: {e}"))
    ok, _, detail = results[-1]
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)


def start_server():
    """Starts server.py and waits for its "Gotha is running at URL" line."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, "-u", "server.py", "--no-browser", "--port", str(PORT)],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", env=env)
    log, found = [], {}

    def read():
        for line in proc.stdout:
            line = line.rstrip("\n")
            log.append(line)
            # Download progress comes as many "file:  42%" lines: show only the 100% ones
            if line.strip() and not re.fullmatch(r"\s*\S+:\s+\d{1,2}%", line):
                print(f"  server | {line}", flush=True)
            if m := re.search(r"Gotha is running at (\S+)", line):
                found["url"] = m.group(1)

    threading.Thread(target=read, daemon=True).start()
    deadline = time.time() + START_TIMEOUT
    while "url" not in found and proc.poll() is None and time.time() < deadline:
        time.sleep(0.5)
    time.sleep(0.5)  # let the reader catch the last lines
    return proc, found.get("url"), log


def stop_server(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ---------- the checks ----------

def run_checks(base, args):
    def page(path):
        def fn():
            status, raw = request(base, path)
            assert status == 200, f"HTTP {status}"
            assert b"<html" in raw.lower(), "not an HTML page"
            return f"{len(raw)} bytes"
        return fn

    check("App page (/)", page("/"))
    check("Chart page (/index.html)", page("/index.html"))

    def info():
        i = api(base, "/api/info")
        assert i["sets"] and i["categories"], "no sets or product types"
        return (f"guide {i['guide_date']}, {i['index'].get('products')} products "
                f"(index built {i['index'].get('built')}), {i['days_recorded']} days of sealed history")
    check("Info", info)

    def search():
        r = api(base, "/api/search?q=" + urllib.parse.quote("ragavan nimble pilferer"))
        first = r["results"][0]["name"] if r["results"] else None
        assert first == "Ragavan, Nimble Pilferer", f"first result is {first!r}"
        return f"{r['total']} results in {r['ms']} ms"
    check("Search", search)

    def typo():
        r = api(base, "/api/search?q=bloomburow")
        codes = [s.get("code") for s in r["sets"]]
        assert "BLB" in codes, f"matching sets: {codes}"
        return f"matching sets: {', '.join(codes)}"
    check("Typo search (bloomburow -> BLB)", typo)

    def gainers():
        r = api(base, "/api/search?kind=sealed&sort=change_desc&extras=hide&min=2&limit=5")
        assert r["results"], "no results"
        assert all(x["kind"] == "sealed" for x in r["results"]), "a result is not sealed"
        top = r["results"][0]
        change = f"{top['change']:+.1%}" if top["change"] is not None else "no change data"
        return f"top: {top['name']} ({change})"
    check("Sealed gainers list", gainers)

    def paging():
        p1 = api(base, "/api/search?q=booster&limit=30")
        assert p1["next_offset"] == 30, f"next_offset is {p1['next_offset']}"
        p2 = api(base, "/api/search?q=booster&limit=30&offset=30")
        ids1, ids2 = {x["id"] for x in p1["results"]}, {x["id"] for x in p2["results"]}
        assert ids2 and not ids1 & ids2, "page 2 repeats page 1"
        return f"{p1['total']} results, pages 1 and 2 differ"
    check("Paging", paging)

    def sealed_page():
        r = api(base, "/api/search?q=" + urllib.parse.quote("collector booster box") + "&kind=sealed&limit=1")
        assert r["results"], "no sealed product found"
        pid = r["results"][0]["ref"]
        s = api(base, f"/api/sealed?id={pid}")
        o = s["outlook"]
        return (f"{s['product']['name']}: {len(s['history'])} days of history, outlook "
                f"{o['label']} ({o['score']:+.2f}), {len(s['siblings'])} products in the set")
    check("Sealed product page", sealed_page)

    def tracked():
        t = api(base, "/api/tracked")
        return f"{len(t['items'])} tracked product(s)"
    check("Sealed tracker", tracked)

    if not args.no_news:
        def news():
            n = api(base, "/api/news")
            assert n["items"], f"no news; feed errors: {n.get('errors')}"
            errors = f"; feeds that failed: {', '.join(n['errors'])}" if n.get("errors") else ""
            return f"{len(n['items'])} items{errors}"
        check("Market news", news)

    def bad_request():
        status, raw = request(base, "/api/sealed?id=abc")
        assert status == 400, f"HTTP {status} instead of 400"
        return json.loads(raw)["error"]
    check("Bad request gives 400", bad_request)

    def not_found():
        status, _ = request(base, "/api/nothing", {})
        assert status == 404, f"HTTP {status} instead of 404"
        return "HTTP 404"
    check("Unknown API path gives 404", not_found)

    if args.card:
        def card():
            before = DATA_JS.read_bytes() if DATA_JS.exists() else None
            try:
                r = api(base, "/api/card", {"name": args.card})
                text = DATA_JS.read_text(encoding="utf-8")
                assert text.startswith("load("), "web/data.js doesn't start with load("
                assert json.dumps(r["name"])[1:-1] in text, "web/data.js doesn't have the card"
                return f"{r['name']}: {r['printings']} printings, web/data.js rewritten"
            finally:
                if before is not None:  # put the user's last card back
                    DATA_JS.write_bytes(before)
        check(f"Card lookup ({args.card})", card)


def run_cli(name):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "main.py", name, "--no-chart"], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=START_TIMEOUT)
    lines = p.stdout.strip().splitlines()
    assert p.returncode == 0, f"exit code {p.returncode}: {(p.stderr or p.stdout)[-500:]}"
    assert any("printings" in l for l in lines), f"no price table in the output: {p.stdout[-300:]}"
    return next(l for l in lines if "printings" in l).strip()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="check this running server instead of starting one")
    ap.add_argument("--card", help="also look this card up (rewrites web/data.js, then puts it back)")
    ap.add_argument("--cli", metavar="NAME", help="also run main.py NAME --no-chart")
    ap.add_argument("--no-news", action="store_true", help="skip the news check")
    args = ap.parse_args()

    proc, log = None, []
    if args.url:
        base = args.url.rstrip("/") + "/"
    else:
        print(f"Starting server.py in {ROOT} ...", flush=True)
        proc, base, log = start_server()
        if not base:
            stop_server(proc)
            print(f"FAIL  Server start: it {'stopped' if proc.returncode else 'did not answer in time'} "
                  "(see the server lines above)")
            sys.exit(1)
    print(f"Checking {base}\n", flush=True)
    try:
        run_checks(base, args)
        if args.cli:
            check(f"Terminal tool (main.py {args.cli})", lambda: run_cli(args.cli))
    finally:
        if proc:
            stop_server(proc)
    if proc:
        def server_log():
            tb = [i for i, line in enumerate(log) if line.startswith("Traceback")]
            assert not tb, "errors in the server output:\n" + "\n".join(log[tb[0]:tb[0] + 30])
            return "no errors"
        check("Server output", server_log)

    failed = [r for r in results if not r[0]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
