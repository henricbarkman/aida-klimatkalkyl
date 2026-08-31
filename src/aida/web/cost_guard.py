"""Global daily spend brake for the endpoints that call models.

The per-caller rate limit in app.py counts requests per user, in process. That
is the wrong unit for the thing we are actually afraid of. On Vercel each warm
instance keeps its own counters, so a caller spread across instances multiplies
their quota, and a request is not a cost anyway: one long analysis can cost more
than fifty short chats.

This module counts money instead, and counts it globally. The number comes from
OpenRouter, which is the party that actually bills us, so there is no local
estimate to drift out of sync with reality. One reading covers every instance at
once, which is exactly the property the in-process rate limiter lacks.

Two deliberate choices worth stating, because both look like bugs otherwise:

FAIL OPEN. If the usage reading is unavailable, calls are allowed. A brake that
fails closed would turn a brief OpenRouter hiccup into a total AIda outage, and
the account already carries a hard monthly credit limit that stops spending for
real. This guard buys reaction time; it is not the last line of defence, so it
should not be the thing that takes the app down.

THE READING IS SHARED. The key is shared with Demi's other systems, so
usage_daily includes their spend, not only AIda's. That makes the brake slightly
conservative: AIda can be stopped by a day that someone else made expensive.
That is the correct behaviour while the wallet is shared, because the thing being
protected is the shared monthly pot. It stops being correct the day AIda gets its
own key, and then this comment is the thing to reread.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

KEY_URL = "https://openrouter.ai/api/v1/key"

DAILY_CAP_USD = float(os.environ.get("AIDA_DAILY_COST_CAP_USD", "100"))
"""Henric's number, 2026-08-31. Normal usage is under five dollars a day, so
this only bites on a genuine runaway. Kept in env so it can be tightened without
a deploy, and so scripts/aida_budget_watch.py can read the same value."""

CACHE_TTL_S = 60.0
"""How long a usage reading is reused. A minute of staleness is worth at most a
minute of overspend against a hundred dollar cap, and it keeps a burst of
requests from turning into a burst of OpenRouter calls."""

FETCH_TIMEOUT_S = 5.0
"""Short on purpose. This runs before the user's analysis, so a slow reading
would be felt as AIda being slow. On timeout we fail open and move on."""

_lock = threading.Lock()
_cache: dict = {"fetched_at": 0.0, "daily": None, "ok": False}


def _api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def _fetch_daily_usage(api_key: str) -> float:
    req = Request(
        KEY_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "aida-cost-guard/1.0",
        },
    )
    with urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode())["data"]
    return float(data.get("usage_daily") or 0.0)


def _read_usage(force: bool = False) -> tuple[float | None, bool]:
    """Current daily spend in USD, and whether the reading is trustworthy.

    Returns (None, False) when usage cannot be read, which callers must treat
    as "allow" rather than "block". See the module docstring on failing open.
    """
    api_key = _api_key()
    if not api_key:
        return None, False

    now = time.monotonic()
    with _lock:
        fresh = now - _cache["fetched_at"] < CACHE_TTL_S
        if fresh and not force:
            return _cache["daily"], _cache["ok"]

    try:
        daily = _fetch_daily_usage(api_key)
        ok = True
    except (HTTPError, URLError, KeyError, ValueError, TimeoutError) as e:
        logger.warning("cost guard could not read usage, failing open: %s", e)
        daily, ok = None, False

    with _lock:
        # A failed read must not overwrite a good recent value with None, or a
        # single blip would blind the brake for a full TTL.
        if ok or _cache["daily"] is None:
            _cache.update({"fetched_at": now, "daily": daily, "ok": ok})
        else:
            _cache["fetched_at"] = now
        return _cache["daily"], _cache["ok"]


def over_daily_cap() -> bool:
    """Whether model-spending endpoints should refuse right now."""
    daily, ok = _read_usage()
    if not ok or daily is None:
        return False
    return daily >= DAILY_CAP_USD


def status() -> dict:
    """Non-sensitive view of the guard, for /api/cost-guard.

    Deliberately excludes spend figures. It exists to answer "is the brake
    actually wired up in production, and against which key", which was an open
    question when it was written because the deployment's environment could not
    be inspected from outside. Booleans answer that; amounts would only leak.
    """
    daily, ok = _read_usage()
    return {
        "active": bool(_api_key()) and ok,
        "source": "openrouter" if _api_key() else "none",
        "reading_ok": ok,
        "blocking": bool(ok and daily is not None and daily >= DAILY_CAP_USD),
        "cap_usd": DAILY_CAP_USD,
    }


def reset_cache_for_tests() -> None:
    with _lock:
        _cache.update({"fetched_at": 0.0, "daily": None, "ok": False})
