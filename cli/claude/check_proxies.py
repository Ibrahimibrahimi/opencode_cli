#!/usr/bin/env python3
"""
check_proxies.py — verify which proxies in ~/UserDirs/proxies.json actually
work, using the same POST request the pickle CLI makes to the Big Pickle API.

  python check_proxies.py

Reads every entry from the proxy JSON file, tests them concurrently with
asyncio (real POST to https://opencode.ai/zen/v1/chat/completions through
each proxy), then rewrites the file with only the working ones.

A proxy counts as working when the API answers HTTP 200. Proxies that
answer 429 (rate-limited but the tunnel + POST worked) are reported but
dropped, because the CLI treats 429 as a failure and rotates away from
them. If nothing passes the file is left untouched.
"""

import asyncio
import json
import sys
from pathlib import Path

import httpx

PROXY_FILE   = Path.home() / "UserDirs" / "proxies.json"
API_URL      = "https://opencode.ai/zen/v1/chat/completions"
TIMEOUT      = 8.0          # seconds per proxy
CONCURRENCY  = 50           # parallel checks

HEADERS = {
    "Authorization":      "Bearer public",
    "Content-Type":       "application/json",
    "x-opencode-client":  "cli",
    "x-opencode-project": "global",
    "User-Agent":         "opencode/1.15.0",
}
# the same chat-completion body the CLI sends (minimal, to save quota)
PAYLOAD = {
    "model":    "big-pickle",
    "messages": [{"role": "user", "content": "ping"}],
}


def build_mounts(entry: dict) -> dict:
    """Mirror the CLI's _build_httpx_mounts: per-scheme transports."""
    mounts = {}
    for scheme in ("http", "https"):
        raw = entry.get(scheme)
        if raw is None:
            continue
        url = str(raw).strip()
        if not url:
            continue
        if "://" not in url:
            url = "http://" + url
        try:
            mounts[f"{scheme}://"] = httpx.AsyncHTTPTransport(proxy=httpx.Proxy(url))
        except Exception:
            continue
    return mounts


async def test_entry(sem: asyncio.Semaphore, entry: dict) -> tuple:
    """Return (entry, ok, status) for one proxy entry."""
    async with sem:
        mounts = build_mounts(entry)
        if not mounts:
            return entry, False, "no usable http/https"
        try:
            async with httpx.AsyncClient(mounts=mounts, timeout=TIMEOUT,
                                         follow_redirects=True) as client:
                resp = await client.post(API_URL, headers=HEADERS, json=PAYLOAD)
        except Exception as e:
            return entry, False, type(e).__name__
        return entry, resp.status_code == 200, resp.status_code


async def main() -> int:
    if not PROXY_FILE.is_file():
        print(f"proxy file not found: {PROXY_FILE}")
        return 1
    try:
        entries = json.loads(PROXY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"cannot read proxy file: {e}")
        return 1
    if not isinstance(entries, list) or not entries:
        print(f"no proxies to verify in {PROXY_FILE}")
        return 1

    total = len(entries)
    print(f"verifying {total} proxies from {PROXY_FILE}")
    print(f"POST {API_URL}  (concurrency={CONCURRENCY}, timeout={TIMEOUT}s)\n")

    sem    = asyncio.Semaphore(CONCURRENCY)
    tasks  = [asyncio.ensure_future(test_entry(sem, e)) for e in entries]
    stats  = {"ok": 0, "429": 0, "fail": 0}
    kept   = []
    done   = 0

    for fut in asyncio.as_completed(tasks):
        entry, ok, status = await fut
        done += 1
        if ok:
            stats["ok"] += 1
            kept.append(entry)
        elif status == 429:
            stats["429"] += 1
        else:
            stats["fail"] += 1
        print(f"  [{done:>3}/{total}] ok={stats['ok']}  "
              f"429={stats['429']}  failed={stats['fail']}")

    print()
    print(f"results: {stats['ok']} working, {stats['429']} rate-limited (429), "
          f"{stats['fail']} dead/unreachable")

    if not kept:
        print(f"no working proxies — {PROXY_FILE} left untouched")
        return 1

    PROXY_FILE.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")
    print(f"updated {PROXY_FILE}: {len(kept)} working proxies")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
