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
THINKING_BUDGET = 2048
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
        response = client.messages.create(
            model=PRICING_MODEL,
            max_tokens=1024 + THINKING_BUDGET,
            thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET},
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
    for block in response.content:
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

        # Match against input product names (fuzzy: check if response name is substring)
        matched_key = None
        if prod_name in product_names_lower:
            matched_key = prod_name
        else:
            for input_name in product_names_lower:
                if input_name in prod_name or prod_name in input_name:
                    matched_key = input_name
                    break

        if matched_key:
            results[matched_key] = (price, unit, source_label)
            logger.info("Batch price for '%s': %.0f SEK/%s", matched_key, price, unit)

    # Fallback: try generic price extraction for any products not found in structured format
    for name, unit_hint in products:
        if name.lower() not in results:
            extracted = _extract_price(full_text, unit_hint)
            if extracted and len(products) == 1:
                # Only use unstructured fallback for single-product edge case
                price, unit = extracted
                results[name.lower()] = (price, unit, source_label)

    return results
