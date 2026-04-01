"""Palats API client — fetch reuse listings from Karlstads kommun's internal marketplace.

Palats (palats.app) is the reuse platform used by Karlstads kommun for
building materials and fixtures. This client uses the internal API with
cookie-based authentication.

NOTE: This is an unofficial/internal API — it may change without notice.
Felix (Palats) has given permission to use it for experimentation.

Auth: PALATS_SESSION and PALATS_REMEMBER_ME env vars (browser cookies).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

PALATS_BASE_URL = "https://palats.app/api"

# Cache listings for 10 minutes within a process
_listings_cache: list[dict] | None = None
_listings_cache_time: float = 0
_CACHE_TTL = 600


@dataclass
class PalatsListing:
    """A reuse listing from Palats, normalized for AIda."""

    id: str
    title: str
    description: str
    price: float  # SEK, 0 if free/unknown
    quantity: int
    unit: str
    category: str  # AIda category key (golv, fönster, etc.) or ""
    image_url: str
    url: str  # Direct link to listing on palats.app

    @property
    def display_source(self) -> str:
        return f"[Palats] palats.app — {self.title}"


def _get_cookies() -> dict[str, str] | None:
    """Get Palats auth cookies from environment."""
    session = os.environ.get("PALATS_SESSION")
    if not session:
        return None
    cookies = {"palats_session": session}
    remember = os.environ.get("PALATS_REMEMBER_ME")
    if remember:
        cookies["remember_me"] = remember
    return cookies


def _normalize_to_aida_category(title: str, description: str = "") -> str:
    """Map a Palats listing to an AIda component category using keywords.

    Returns the AIda category key (e.g. 'golv', 'fönster') or '' if no match.
    """
    text = f"{title} {description}".lower()

    # Order matters — more specific matches first
    # Multi-word patterns checked before single-word to avoid false positives
    category_keywords: list[tuple[str, list[str]]] = [
        ("fönster", [
            "fönster", "fönsterbåge", "fönsterkassett", "fönsterbänk",
            "energiglas",
        ]),
        ("dörr", [
            "dörr", "dörrblad", "dörrkarm", "innerdörr", "ytterdörr",
            "branddörr", "skjutdörr", "entrédörr",
        ]),
        ("golv", [
            "golv", "parkett", "klinker", "kakel", "vinylgolv", "vinylmatta",
            "laminat", "trägolv", "golvplatta", "matta", "linoleum",
        ]),
        ("tak", [
            "takpann", "takplåt", "takskiva", "yttertak", "undertak",
            "undertaksplatt", "takbrygga",
        ]),
        ("belysning", [
            "lampa", "armatur", "belysning", "spotlight",
            "taklampa", "takbelysning", "vägglampa", "led lampa",
            "skrivbordsbelysning",
        ]),
        ("isolering", [
            "isolering", "mineralull", "glasull", "stenull", "cellplast",
            "eps", "xps", "cellulosa", "ljudisolerande",
        ]),
        ("innervägg", [
            "gipsskiva", "gips", "väggskiva", "byggskiva",
            "reglar", "innervägg",
        ]),
        ("yttervägg", ["fasadskiva", "fasadplatta", "puts", "fasad"]),
        ("ventilation", [
            "ventilation", "fläkt", "kanal", "ventilationskanal",
            "don", "tilluftsdon", "frånluftsdon",
        ]),
        ("vvs", ["panelradiator", "radiator", "avloppsrör"]),
        ("hiss", ["hiss", "elevator"]),
        ("diskmaskin", ["diskmaskin"]),
        ("kylanläggning", ["kyl", "frys", "kylskåp", "kylanläggning"]),
    ]

    for category, keywords in category_keywords:
        for kw in keywords:
            if kw in text:
                return category

    return ""


def fetch_listings(force_refresh: bool = False) -> list[dict]:
    """Fetch all published listings from Palats.

    Returns raw API response (only PUBLISHED), cached for 10 minutes.
    Returns empty list if no credentials or API error.
    """
    global _listings_cache, _listings_cache_time

    if (
        not force_refresh
        and _listings_cache is not None
        and (time.time() - _listings_cache_time) < _CACHE_TTL
    ):
        return _listings_cache

    cookies = _get_cookies()
    if not cookies:
        logger.debug("No Palats credentials — skipping reuse search")
        return []

    try:
        resp = requests.get(
            f"{PALATS_BASE_URL}/v2/listings",
            cookies=cookies,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        # Handle both list and wrapped response formats
        if isinstance(data, list):
            listings = data
        elif isinstance(data, dict):
            listings = data.get("listings", data.get("data", data.get("items", [])))
        else:
            listings = []

        # Only keep published listings with available articles
        listings = [
            l for l in listings
            if l.get("listingStatus") == "PUBLISHED"
            and l.get("availableArticlesCount", 0) > 0
        ]

        _listings_cache = listings
        _listings_cache_time = time.time()
        logger.info("Fetched %d published listings from Palats", len(listings))
        return listings

    except requests.RequestException as e:
        logger.warning("Palats API error: %s", e)
        return []


def _extract_listing(raw: dict) -> PalatsListing:
    """Extract a PalatsListing from raw API response.

    Mapped to actual Palats API v2 field names (verified 2026-03-31).
    """
    listing_id = str(raw.get("id", ""))
    title = raw.get("title", "")
    description = raw.get("articleConditionComment", "") or ""
    price = float(raw.get("price", 0) or 0)
    quantity = int(raw.get("availableArticlesCount", 0))
    unit = "st"

    # Thumbnail — use fullSizePath for best quality
    thumbnail = raw.get("thumbnail")
    image_url = ""
    if isinstance(thumbnail, dict):
        image_url = thumbnail.get("fullSizePath", thumbnail.get("path", ""))

    # Owner info for context
    owner = raw.get("owner", {})
    owner_name = owner.get("name", "") if isinstance(owner, dict) else ""

    category = _normalize_to_aida_category(title, description)

    return PalatsListing(
        id=listing_id,
        title=title,
        description=f"{description} (kontakt: {owner_name})" if owner_name else description,
        price=price,
        quantity=quantity,
        unit=unit,
        category=category,
        image_url=image_url,
        url=f"https://palats.app/web/listing/{listing_id}" if listing_id else "",
    )


def search_listings_for_component(
    component_name: str,
    all_listings: list[dict] | None = None,
) -> list[PalatsListing]:
    """Find Palats listings matching an AIda component.

    Args:
        component_name: AIda component name (e.g. 'Fönster', 'Golv vinyl')
        all_listings: Pre-fetched raw listings (avoids re-fetching per component)

    Returns:
        Matched listings, sorted by relevance (category match first).
    """
    from aida.data.climate_data import normalize_component_name

    target_category = normalize_component_name(component_name)
    if not target_category:
        return []

    if all_listings is None:
        all_listings = fetch_listings()

    if not all_listings:
        return []

    matched = []
    for raw in all_listings:
        listing = _extract_listing(raw)
        if listing.category == target_category:
            matched.append(listing)

    return matched


# Reuse CO2e assumptions (kg CO2e per unit) — transport and minor refurbishment only
REUSE_CO2E_PER_UNIT: dict[str, float] = {
    "golv": 0.5,      # m2
    "innervägg": 1.5,  # m2
    "yttervägg": 2.0,  # m2
    "fönster": 10.0,   # st — heavier, more transport impact
    "dörr": 3.0,       # st
    "tak": 1.0,        # m2
    "isolering": 0.5,  # m2
    "belysning": 1.0,  # st
    "ventilation": 0.5,  # lm
    "diskmaskin": 15.0,  # st
    "kylanläggning": 25.0,  # st
    "hiss": 500.0,     # st
}

# Default if category not in the dict above
_DEFAULT_REUSE_CO2E = 2.0
