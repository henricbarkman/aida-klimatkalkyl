"""Pricing lookup via LLM web search.

Routes through OpenRouter (same key as rest of AIda).
Returns None silently if key is missing or any error occurs.
"""
from __future__ import annotations

import logging
import os
import re

import anthropic

from aida.api_client import call_model

logger = logging.getLogger(__name__)

PRICING_MODEL = "anthropic/claude-sonnet-4-6"
# Pricing stays on Sonnet 4.6 (cheap, web-search heavy, not the CO2 correctness
# path). Adaptive thinking + effort replaces the deprecated budget_tokens.
PRICING_EFFORT = "medium"
PRICING_MAX_TOKENS = 8000  # room for adaptive thinking + a short price answer
MAX_SEARCH_USES = 3
OPENROUTER_BASE_URL = "https://openrouter.ai/api"


# Non-streaming; web search + adaptive thinking can run long, so allow headroom.
LLM_CALL_TIMEOUT = 300.0


def _get_client() -> anthropic.Anthropic | None:
    """Return OpenRouter client for web search, or None if key not configured."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(
        api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=LLM_CALL_TIMEOUT,
    )


def _build_prompt(product_name: str, unit_hint: str) -> str:
    unit_phrase = f"per {unit_hint}" if unit_hint and unit_hint not in ("kg", "") else ""
    return (
        f"Vad kostar '{product_name}' installerat (material + arbete) "
        f"på den svenska byggmarknaden {unit_phrase}? "
        f"Sök efter aktuella priser hos svenska bygghandlare och entreprenörer. "
        f"Ange typiskt installerat pris i SEK exklusive moms. "
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


def _estimate_price_without_search(product_name: str, unit_hint: str) -> tuple[float, str, str] | None:
    """LLM estimate without web search — fallback when web search fails."""
    client = _get_client()
    if client is None:
        return None

    unit_phrase = f"per {unit_hint}" if unit_hint and unit_hint not in ("kg", "") else ""
    prompt = (
        f"Uppskatta vad '{product_name}' kostar installerat (material + arbete) "
        f"på den svenska byggmarknaden {unit_phrase}. "
        f"Basera din uppskattning på din kunskap om svenska byggpriser. "
        f"Svara med exakt format: PRIS: [tal] SEK/[enhet]"
    )

    try:
        response = call_model(
            client,
            model=PRICING_MODEL,
            max_tokens=PRICING_MAX_TOKENS,
            effort=PRICING_EFFORT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.warning("Price estimation failed for '%s': %s", product_name, e)
        return None

    text_parts = [b.text for b in (response.content or []) if hasattr(b, "type") and b.type == "text"]
    full_text = " ".join(text_parts)
    if not full_text:
        return None

    result = _extract_price(full_text, unit_hint)
    if result is None:
        return None

    price, unit = result
    logger.info("Price estimated for '%s': %.0f SEK/%s (no web search)", product_name, price, unit)
    return price, unit, "LLM-uppskattning"


def lookup_price(product_name: str, unit_hint: str = "") -> tuple[float, str, str] | None:
    """Search the web for current Swedish market price of a building material.

    Returns (price_sek, unit, source_description) or None on any failure.
    Falls back to LLM estimate without web search if web search fails.
    Never raises.
    """
    client = _get_client()
    if client is None:
        return None

    prompt = _build_prompt(product_name, unit_hint)

    try:
        response = call_model(
            client,
            model=PRICING_MODEL,
            max_tokens=PRICING_MAX_TOKENS,
            effort=PRICING_EFFORT,
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
        return _estimate_price_without_search(product_name, unit_hint)

    # Extract text and source URL from response
    text_parts = []
    source_url = ""
    for block in (response.content or []):
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
        return _estimate_price_without_search(product_name, unit_hint)

    result = _extract_price(full_text, unit_hint)
    if result is None:
        logger.info("Could not extract price for '%s' from web search, trying estimate", product_name)
        return _estimate_price_without_search(product_name, unit_hint)

    price, unit = result
    source = f"Webbsökning ({source_url})" if source_url else "Webbsökning"
    logger.info("Price found for '%s': %.0f SEK/%s", product_name, price, unit)
    return price, unit, source


def lookup_prices_batch(
    products: list[tuple[str, str]],
) -> dict[str, tuple[float, str, str]]:
    """Look up prices for multiple products in a single LLM web search call.

    Args:
        products: list of (product_name, unit_hint) tuples

    Returns:
        dict mapping lowercase product_name -> (price_per_unit, unit, source)
    """
    if not products:
        return {}
    if len(products) == 1:
        name, unit = products[0]
        result = lookup_price(name, unit)
        return {name.lower(): result} if result else {}

    client = _get_client()
    if client is None:
        return {}

    product_lines = "\n".join(
        f"- {name} (enhet: {unit})" if unit and unit not in ("kg", "")
        else f"- {name}"
        for name, unit in products
    )

    prompt = (
        f"Sök efter aktuella installerade priser (material + arbete) på den svenska "
        f"byggmarknaden för följande produkter:\n\n"
        f"{product_lines}\n\n"
        f"Sök hos svenska bygghandlare och entreprenörer.\n"
        f"Ange typiska installerade priser i SEK exklusive moms.\n"
        f"Svara med exakt format för VARJE produkt på egen rad:\n"
        f"PRODUKT: [produktnamn] | PRIS: [tal] SEK/[enhet]\n"
        f"Om du hittar ett prisintervall, ange mittpunkten."
    )

    try:
        response = call_model(
            client,
            model=PRICING_MODEL,
            max_tokens=PRICING_MAX_TOKENS,
            effort=PRICING_EFFORT,
            messages=[{"role": "user", "content": prompt}],
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": min(MAX_SEARCH_USES + len(products), 8),
                "user_location": {
                    "type": "approximate",
                    "country": "SE",
                    "timezone": "Europe/Stockholm",
                },
            }],
        )
    except Exception as e:
        logger.warning("Batch pricing web search failed: %s", e)
        return {}

    text_parts = []
    source_url = ""
    for block in (response.content or []):
        if not hasattr(block, "type"):
            continue
        if block.type == "text":
            text_parts.append(block.text)
            if hasattr(block, "citations") and block.citations:
                for cit in block.citations:
                    if hasattr(cit, "url") and cit.url and not source_url:
                        source_url = cit.url

    full_text = "\n".join(text_parts)
    if not full_text:
        return {}

    source_label = f"Webbsökning ({source_url})" if source_url else "Webbsökning"

    # Parse structured lines: PRODUKT: name | PRIS: 250 SEK/m2
    results: dict[str, tuple[float, str, str]] = {}
    product_names_lower = {name.lower(): unit for name, unit in products}

    for line in full_text.split("\n"):
        pm = re.search(
            r'PRODUKT:\s*(.+?)\s*\|\s*PRIS:\s*(\d[\d\s]*(?:[,.]\d+)?)\s*SEK\s*/\s*(\w+[²³]?)',
            line, re.IGNORECASE,
        )
        if not pm:
            continue

        prod_name = pm.group(1).strip().lower()
        raw_num = pm.group(2).replace(" ", "").replace(",", ".")
        raw_unit = pm.group(3)

        try:
            price = float(raw_num)
        except ValueError:
            continue

        if price <= 0 or price > 10_000_000:
            continue

        unit_map = {"m²": "m2", "m2": "m2", "m³": "m3", "m3": "m3",
                    "st": "st", "pcs": "st", "lm": "lm", "kg": "kg"}
        unit = unit_map.get(raw_unit.lower(), raw_unit.lower())

        # Match against input product names. On overlap prefer the LONGEST
        # matching input name, so "Innerdörr vit" maps to "innerdörr", not the
        # shorter "dörr" that also substring-matches.
        matched_key = None
        if prod_name in product_names_lower:
            matched_key = prod_name
        else:
            candidates = [
                input_name for input_name in product_names_lower
                if input_name in prod_name or prod_name in input_name
            ]
            if candidates:
                matched_key = max(candidates, key=len)

        if matched_key:
            results[matched_key] = (price, unit, source_label)
            logger.info("Batch price for '%s': %.0f SEK/%s", matched_key, price, unit)

    # Note: no unstructured single-price fallback here. Applying one
    # text-extracted price to several unmatched products would assign wrong
    # prices; products the LLM didn't return in the structured format are left
    # unpriced (and later filtered) rather than guessed.
    return results
