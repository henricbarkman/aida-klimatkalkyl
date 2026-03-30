"""Price reasonableness validation for AIda.

Validates prices against expected ranges per building component category.
Flags unreasonable values without removing them — transparency over silence.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Reasonable price ranges per category in SEK per unit.
# Keys match normalize_component_name() output from climate_data.py.
# Format: (min_sek_per_unit, max_sek_per_unit, unit)
PRICE_RANGES: dict[str, tuple[float, float, str]] = {
    "fönster":       (5_000, 25_000, "st"),
    "tak":           (300, 2_000, "m2"),
    "yttervägg":     (500, 3_000, "m2"),    # facade insulation / cladding
    "isolering":     (50, 1_500, "m2"),
    "golv":          (200, 1_500, "m2"),
    "innervägg":     (300, 2_000, "m2"),
    "betongvägg":    (500, 3_000, "m2"),
    "belysning":     (500, 15_000, "st"),
    "ventilation":   (200, 5_000, "m2"),
    "dörr":          (3_000, 30_000, "st"),
    "hiss":          (300_000, 2_000_000, "st"),
    "diskmaskin":    (15_000, 150_000, "st"),
    "kylanläggning": (30_000, 500_000, "st"),
}

# Catch-all for categories not listed above.
FALLBACK_MIN = 10       # SEK per unit — below this is nonsense
FALLBACK_MAX = 50_000   # SEK per unit — above this needs verification (except known big items)


def validate_unit_price(
    price_per_unit: float,
    category: str,
    *,
    is_estimate: bool = False,
) -> tuple[float, str]:
    """Validate a per-unit price and return (price, note).

    Args:
        price_per_unit: Cost in SEK per unit (m2, st, etc.).
        category: Normalized component category key.
        is_estimate: True if the price came from LLM estimation rather than
                     a verified source (web search, EPD, database).

    Returns:
        (price, note) where note is empty if OK, or a flag string to append.
    """
    if price_per_unit <= 0:
        return 0, "Pris ej tillgängligt"

    note = ""

    # Tag LLM estimates regardless of range
    if is_estimate:
        note = "Approximerat pris"

    # Check category-specific range
    cat_key = category.lower().strip()
    bounds = PRICE_RANGES.get(cat_key)

    if bounds:
        range_min, range_max, _unit = bounds
        if price_per_unit < range_min or price_per_unit > range_max:
            note = "Oväntat pris — verifiera"
            logger.info(
                "Price outside range for %s: %.0f SEK (expected %s–%s)",
                cat_key, price_per_unit, range_min, range_max,
            )
    else:
        # Fallback range for unknown categories
        if price_per_unit < FALLBACK_MIN or price_per_unit > FALLBACK_MAX:
            note = "Oväntat pris — verifiera"
            logger.info(
                "Price outside fallback range: %.0f SEK for '%s'",
                price_per_unit, cat_key,
            )

    return price_per_unit, note


def validate_total_price(
    total_cost: float,
    quantity: float,
    category: str,
    *,
    is_estimate: bool = False,
) -> tuple[float, str]:
    """Validate a total price by deriving per-unit and checking range.

    Convenience wrapper when you have total cost + quantity.
    Returns (total_cost, note).
    """
    if total_cost <= 0 or quantity <= 0:
        return total_cost, "Pris ej tillgängligt" if total_cost <= 0 else ""

    per_unit = total_cost / quantity
    _price, note = validate_unit_price(per_unit, category, is_estimate=is_estimate)
    return total_cost, note
