"""Updates the web/index.html page with a card's price history.

The page is fixed; only web/data.js is rewritten for each card. The page
re-reads that file every 1.5 s, so an open tab updates itself.
"""
import json
import os
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent / "web"
PAGE = WEB_DIR / "index.html"
DATA = WEB_DIR / "data.js"

_opened = False  # has the page been opened in this session?


def _data(printings):
    """Reduces the printings to what the page needs."""
    rows = []
    for c in printings:
        imgs = c.get("image_uris") or (c.get("card_faces") or [{}])[0].get("image_uris") or {}
        rows.append({
            "set": c["set"].upper(),
            "set_name": c["set_name"],
            "num": c["collector_number"],
            "img": imgs.get("normal"),
            "link": (c.get("purchase_uris") or {}).get("cardmarket"),
            "normal": {str(k): v for k, v in c["normal"].items()},
            "foil": {str(k): v for k, v in c["foil"].items()},
            "hist": c["hist"],
            "guide": c["guide"],
        })
    return rows


def generate(name, printings, ref, days, guide_date):
    data = json.dumps({
        "name": name, "ref": ref, "days": list(days), "guide_date": guide_date,
        "generated": time.time(),  # the page only redraws when this changes
        "printings": _data(printings),
    }, ensure_ascii=False)
    DATA.write_text(f"load({data});\n", encoding="utf-8")
    return PAGE


def _firefox():
    """Path to firefox.exe (or None if it isn't installed)."""
    places = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", "")),
    ]
    for base in places:
        exe = base / "Mozilla Firefox" / "firefox.exe"
        if exe.is_file():
            return str(exe)
    return shutil.which("firefox")


def open_page(page):
    """Opens the chart in Firefox (or the default browser), only once per session.

    On later searches the already-open tab updates itself.
    """
    global _opened
    if _opened:
        return
    _opened = True
    firefox = _firefox()
    if firefox:
        subprocess.Popen([firefox, "-new-tab", page.as_uri()])
    else:
        webbrowser.open(page.as_uri())
