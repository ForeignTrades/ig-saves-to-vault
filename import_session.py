#!/usr/bin/env python3
"""
One-time (and on expiry) Instagram session import. Ban-safety cornerstone:
we NEVER log in with a password from a script - we reuse the session your
own browser already has. Three sources, tried in order:

  1. Firefox cookies.sqlite (auto-detected, read-only copy)
  2. A cookies.txt file exported from your browser
     (e.g. the "Get cookies.txt LOCALLY" extension while on instagram.com)
  3. Manually pasted cookie values (sessionid etc. from DevTools)

Usage:  python import_session.py [path\\to\\cookies.txt]
"""

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
WANTED = ("sessionid", "csrftoken", "ds_user_id", "mid", "ig_did")


def from_firefox() -> dict:
    base = Path.home() / "AppData/Roaming/Mozilla/Firefox/Profiles"
    if not base.exists():
        return {}
    for db in sorted(base.glob("*/cookies.sqlite"),
                     key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td) / "c.sqlite"
                shutil.copy2(db, tmp)  # copy: FF may hold a lock
                con = sqlite3.connect(str(tmp))
                rows = con.execute(
                    "SELECT name, value FROM moz_cookies "
                    "WHERE host LIKE '%instagram.com'").fetchall()
                con.close()
            cookies = {n: v for n, v in rows if n in WANTED}
            if cookies.get("sessionid"):
                print(f"Found Instagram session in Firefox profile: {db.parent.name}")
                return cookies
        except Exception as e:  # noqa: BLE001
            print(f"  (skipping {db.parent.name}: {e})")
    return {}


def from_cookies_txt(path: Path) -> dict:
    cookies = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and "instagram.com" in parts[0]:
            name, value = parts[5], parts[6]
            if name in WANTED:
                cookies[name] = value
    return cookies


def from_prompt() -> dict:
    print("\nPaste cookie values from your browser (instagram.com ->"
          " DevTools -> Application -> Cookies):")
    cookies = {}
    for name in ("sessionid", "csrftoken", "ds_user_id"):
        v = input(f"  {name}{' (required)' if name == 'sessionid' else ' (Enter to skip)'}: ").strip()
        if v:
            cookies[name] = v
    return cookies


def main() -> int:
    import instaloader

    cookies = {}
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if not p.exists():
            print(f"File not found: {p}")
            return 1
        cookies = from_cookies_txt(p)
    if not cookies.get("sessionid"):
        cookies = from_firefox()
    if not cookies.get("sessionid"):
        cookies = from_prompt()
    if not cookies.get("sessionid"):
        print("No sessionid found - cannot continue.")
        return 1

    L = instaloader.Instaloader(quiet=True, max_connection_attempts=1)
    L.context._session.cookies.update(cookies)
    try:
        username = L.test_login()
    except Exception as e:  # noqa: BLE001
        print(f"Session check failed: {e}")
        return 1
    if not username:
        print("Session invalid or expired. Log into instagram.com in your "
              "browser first, then re-run this.")
        return 1

    L.context.username = username
    session_file = SCRIPT_DIR / f"session-{username}"
    L.save_session_to_file(str(session_file))

    cfg = {}
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    cfg["instagram_username"] = username
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    print(f"\nOK - session saved for @{username} -> {session_file.name}")
    print("The pipeline will reuse this session; no password is ever stored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
