"""Pricing lookup via LLM web search.

Routes through OpenRouter (same key as rest of AIda).
Returns None silently if key is missing or any error occurs.
"""
from __future__ import annotations

import logging
import os
import re

import anthropic

logger = logging.getLogger(__name__)

PRICING_MODEL = "anthropic/claude-sonnet-4.6"
THINKING_BUDGET = 5000  # medium level
MAX_SEARCH_USES = 3
OPENROUTER_BASE_URL = "https://openrouter.ai/api"


def _get_client() -> anthropic.Anthropic | None:
    """Return OpenRouter client for web search, or None if key not configured."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key, base_url=OPENROUTER_BASE_URL)


def _build_prompt(product_name: str, unit_hint: str) -> str:
    unit_phrase = f"per {unit_hint}" if unit_hint and unit_hint not in ("kg", "") else ""
    return (
        f"Vad kostar '{product_name}' på den svenska byggmarknaden {unit_phrase}? "
        f"Sök efter aktuella priser hos svenska bygghandlare (Byggmax, Beijer, XL-Bygg etc). "
        f"Ange ett typiskt marknadspris i SEK exklusive moms. "
        f"Svara med exakt format: PRIS: [tal] SEK/[enhet]. "
        f"Om du hittar ett prisintervall, ange mittpunkten."
    )


def _extract_price(text: str, unit_hint: str) -> tuple[float, str] | None:
    """Extract price from LLM response text."""
    # Try structured format first: PRIS: 250 SEK/m2
    m = re.search(r'PRIS:\s*(\d[\d\s]*(?:[,.]\d+)?)\s*SEK\s*/\s*(\w+[²³]?)', text, re.IGNORECASE)
    if m:
        raw_num, raw_unit = m.group(1), m.group(2)
    else:
        # Fallback: any "N SEK/unit" or "N kr/unit" pattern
        pattern = r'(\d[\d\s]*(?:[,.]\d+)?)\s*(?:SEK|kr|kronor)\s*/\s*(\w+[²³]?)'
        matches = re.findall(pattern, text, re.IGNORECASE)
        if not matches:
            return None
        raw_num, raw_unit = matches[0]

    raw_num = raw_num.replace(" ", "").replace(",", ".")
    try:
        price = float(raw_num)
    except ValueError:
        return None

    unit_map = {"m²": "m2", "m2": "m2", "m³": "m3", "m3": "m3",
                "st": "st", "pcs": "st", "lm": "lm", "kg": "kg"}
    unit = unit_map.get(raw_unit.lower(), unit_hint or raw_unit.lower())

    if price <= 0 or price > 10_000_000:
        return None
    return price, unit


def lookup_price(product_name: str, unit_hint: str = "") -> tuple[float, str, str] | None:
    """Search the web for current Swedish market price of a building material.

    Returns (price_sek, unit, source_description) or None on any failure.
    Never raises.
    """
    client = _get_client()
    if client is None:
        return None

    prompt = _build_prompt(product_name, unit_hint)

    try:
        response = client.messages.create(
            model=PRICING_MODEL,
            max_tokens=512 + THINKING_BUDGET,
            thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET},
            messages=[{"role": "user", "content": prompt}],
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": MAX_SEARCH_USES,
                "user_location": {
                    "type": "approximate",
                    "country": "SE",
                    "timezone": "Europe/Stockholm",
                },
            }],
        )
    except Exception as e:
        logger.warning("Pricing web search failed for '%s': %s", product_name, e)
        return None

    # Extract text and source URL from response
    text_parts = []
    source_url = ""
    for block in response.content:
        if not hasattr(block, "type"):
            continue
        if block.type == "text":
            text_parts.append(block.text)
            if hasattr(block, "citations") and block.citations:
                for cit in block.citations:
                    if hasattr(cit, "url") and cit.url:
                        source_url = cit.url
                        break

    full_text = " ".join(text_parts)
    if not full_text:
        return None

    result = _extract_price(full_text, unit_hint)
    if result is None:
        logger.info("Could not extract price for '%s' from: %s", product_name, full_text[:200])
        return None

    price, unit = result
    source = f"Webbsökning ({source_url})" if source_url else "Webbsökning"
    logger.info("Price found for '%s': %.0f SEK/%s", product_name, price, unit)
    return price, unit, source
