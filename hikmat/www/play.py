# Copyright (c) 2026, FOSS United and contributors
# For license information, please see license.txt
"""The /play door into the game.

The game is a plain file under /assets/, which the proxy serves `cache-control: immutable,
max-age=31536000`. That is right for Frappe's hash-named bundles and wrong for ours: game.html
keeps its name and changes content every release, so a proxy will happily answer the next
release from a copy it took a year ago. Observed on prod after the L1-L5 deploy — the origin
had the new 935 KB game.html while every visitor got a 914 KB copy from the week before, with
none of the new code in it.

So the redirect carries a release token, giving each release its own cache key. The token is
read out of public/sw.js's CACHE constant rather than repeated here, because the service worker
has to agree with this page about which URL the game lives at, and one of the two WILL be
forgotten at 1am otherwise.
"""
import re

import frappe

_FALLBACK = "v0"
_cached = {}


def get_context(context):
    context.rel = release_token()
    context.no_cache = 1        # this page must never itself go stale, or the token freezes


def release_token():
    """`v9` from `const CACHE = "hikmat-pwa-v9";`. Memoised per process, and re-read when sw.js
    changes on disk so a deploy does not need a restart to pick the new token up."""
    try:
        path = frappe.get_app_path("hikmat", "public", "sw.js")
        import os
        stamp = os.path.getmtime(path)
        if _cached.get("stamp") == stamp and _cached.get("tok"):
            return _cached["tok"]
        with open(path, encoding="utf-8") as f:
            m = re.search(r'CACHE\s*=\s*"hikmat-pwa-(v\d+)"', f.read(4096))
        tok = m.group(1) if m else _FALLBACK
        _cached["stamp"], _cached["tok"] = stamp, tok
        return tok
    except Exception:
        # Never let a missing/odd sw.js stop a child reaching the game.
        return _FALLBACK
