"""All the Gotha commands in one place, like the "scripts" of a package.json.

    python run.py                    show the commands
    python run.py app                start the browser app
    python run.py card Kasmina       words after the command are passed on
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# name: (what Python runs, what it does)
COMMANDS = {
    "install":       ("-m pip install requests", "Install the only library Gotha needs"),
    "app":           ("server.py", "Start the browser app at http://127.0.0.1:8765"),
    "app:nobrowser": ("server.py --no-browser", "Start the app without opening the browser"),
    "card":          ("main.py", "Look up a card (no name: asks for names until Enter)"),
    "card:text":     ("main.py --no-chart", "Look up a card in the terminal only, no chart page"),
    "portfolio":     ("portfolio.py", "Value of the cards you own and profit after fees"),
    "status":        (".claude/skills/price-check/query.py status", "How fresh the local price data is"),
    "movers":        (".claude/skills/price-check/query.py movers", "Biggest risers this week (--down for fallers)"),
    "check":         (".claude/skills/gotha-check/check.py", "Start the app, test its pages, stop it"),
    "backfill:list": ("backfill.py --list", "Show what backfill would download"),
    "backfill":      ("backfill.py", "Import past sealed prices (one time, ~680 MB)"),
    "alerts:test":   ("alerts.py --test", "Send a test notification to your phone"),
    "alerts":        ("alerts.py", "Check the watchlist and send real phone notifications"),
}


def show():
    print("Usage: python run.py COMMAND [more words]\n")
    for name, (_, what) in COMMANDS.items():
        print(f"  {name:15} {what}")


def main():
    if len(sys.argv) < 2:
        show()
        return
    name = sys.argv[1]
    if name not in COMMANDS:
        print(f"Unknown command: {name}\n")
        show()
        sys.exit(1)

    cmd = COMMANDS[name][0].split() + sys.argv[2:]
    print("> python " + " ".join(cmd) + "\n", flush=True)
    proc = subprocess.Popen([sys.executable, *cmd], cwd=HERE)
    while True:
        try:
            sys.exit(proc.wait())
        except KeyboardInterrupt:
            pass  # Ctrl+C reaches the program too; let it stop by itself


if __name__ == "__main__":
    main()
