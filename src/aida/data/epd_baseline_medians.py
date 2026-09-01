"""EPD-based baseline typvärden per AIda category.

Boverket's climate database is organized by material composition (~200 generic
products), not by building component. For component categories that Boverket
lacks (notably golv and sanitet), the baseline agent falls back to LLM
estimation — which is often unreliable.

This module provides a middle tier: a category-aggregated "typvärde" derived
from the upper half of Environdec EPDs (by climate impact) in each AIda
category. It approximates "what conventional standard materials cost
climate-wise" — matching the NollCO2 methodology's "Typical" framing.

Why upper-half, not all-EPD median?
EPD databases skew toward climate-conscious producers — getting an EPD is
voluntary, and product manufacturers who care about climate document their
products. The median across ALL EPDs therefore underestimates what a user
who isn't actively climate-optimizing would actually choose. The upper half
(by GWP) is a better proxy for "default conventional choice".

Statistically: median of the upper 50% of values (sorted by GWP). For large
samples this approximates the 75th percentile but with less sensitivity to
single outliers in small samples — important since our category sample
sizes are often 5-15.

Source labels in the pipeline:
- "Boverkets klimatdatabas" → Tier 1: genuine same-material Boverket hit
- "Environdec EPD-typvärde" → Tier 2: this module
- "Uppskattning"            → Tier 3: LLM fallback when nothing else works

We publish a typvärde for (category[, subcategory], unit) keys with enough
samples. Heterogeneous categories (sanitet, belysning, vitvaror) — a toilet,
a cistern and a sink in one bucket — are split PER SUBCATEGORY using the
Palats subcategory taxonomy, so each gets its own typvärde; items that don't
classify into a subcategory stay "Uppskattning".
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from statistics import median

logger = logging.getLogger(__name__)

EPD_DATA_PATH = Path(__file__).parent / "epd_alternatives.json"

# Minimum samples to publish a median. Below this, the value is too noisy
# to be a useful default — fall back to LLM estimation.
_MIN_SAMPLES = 5

# Per-(category, subcategory) floor overrides. Some genuine product types are
# sparsely represented in Environdec (toilets: the index has only 4 real
# building toilets — the rest are portable bajamajor, rejected at build time).
# For those, 4 verified EPDs beat an LLM guess, so we publish at n=4 rather
# than falling back to "Uppskattning". Kept targeted (not a global drop to 4)
# so each sparse category is opted in only after its rows are inspected — a
# global drop would also publish e.g. betongvägg/m2, whose 4-row bucket still
# holds a misclassified steel sheet. Default stays 5 for everything else.
_MIN_SAMPLES_OVERRIDE: dict[tuple[str, str], int] = {
    ("sanitet", "toalett"): 4,
}

# Categories that mix structurally different product types in the same bucket
# (sanitet covers toilets, sinks, taps — wildly different CO2e). A flat
# category aggregate is misleading, so we aggregate PER SUBCATEGORY instead
# (toalett, handfat, blandare...), reusing the Palats subcategory taxonomy.
# Each (category, subcategory, unit) gets its own typvärde; items that don't
# classify into a subcategory get no typvärde (stay LLM-uppskattning).
_SUBCATEGORIZED_CATEGORIES = {"sanitet", "belysning", "vitvaror"}

# Categories where a subtype is PREFERRED but the category aggregate is still a
# legitimate answer. Different from _SUBCATEGORIZED_CATEGORIES above: there, a
# flat aggregate over toilets and taps is meaningless, so an unclassified item
# gets nothing. Here the category mean is coherent enough to fall back on.
#
# The reason golv is split is not incoherence, it is namability. NollCO2 models
# a typical building part for a given building type ("ett idag byggnadstypiskt
# sätt"), and §5.3 puts a proxy for a merely similar product at the LOWEST data
# priority. A baseline that says "11.3 kg CO2e/m2, the average of linoleum,
# carpet, parquet, terrazzo and vinyl" cannot answer "which floor did you
# cost?", because the answer is "none that exists". The subtypes span 5.4 to
# 17.4, a factor of three.
#
# Fallback is mandatory, not a nicety: only textil (30), vinyl (29) and trä (18)
# clear _MIN_SAMPLES. linoleum (3), laminat (3), keramik (4) and gummi (1) do
# not, and a thin bucket is a worse answer than an honest category mean. Which
# level was used is reported back in the result so the UI can say so.
_SUBTYPE_PREFERRED_CATEGORIES = {"golv"}

# Still excluded wholesale: no subcategory taxonomy defined, too heterogeneous
# to aggregate meaningfully.
_HETEROGENEOUS_CATEGORIES = {"storköksutrustning"}


def _load_epd_data() -> list[dict]:
    if not EPD_DATA_PATH.exists():
        return []
    try:
        with open(EPD_DATA_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load EPD data: %s", e)
        return []


def _upper_half_median(values: list[float]) -> float:
    """Median of the upper 50% of values (sorted ascending).

    Approximates the 75th percentile but more robust to single outliers in
    small samples. For odd N, includes the middle element in the upper half
    (slicing v[N//2:] gives ceil(N/2) elements).
    """
    s = sorted(values)
    upper = s[len(s) // 2:]
    return float(median(upper))


def _epd_subcategory(cat: str, e: dict) -> str:
    """Subcategory for an EPD row. Reads the stored field, falling back to
    deriving it from the name so an older catalog without the field still works.
    """
    sub = e.get("subcategory")
    if sub:
        return sub
    if cat in _SUBCATEGORIZED_CATEGORIES:
        from aida.data.palats_client import _normalize_to_aida_subcategory
        return _normalize_to_aida_subcategory(cat, e.get("name", ""))
    return ""


def _compute_typvärden() -> dict[tuple[str, str, str], dict]:
    """Compute upper-half median GWP per (category, subcategory, unit).

    For most categories subcategory is "" (flat aggregate). For the
    heterogeneous-but-subcategorized ones (sanitet, belysning, vitvaror) the
    median is computed per subcategory; rows that don't classify are skipped.

    Each entry has: baseline_co2e_per_unit, sample_size, full_median, min, max.
    """
    epds = _load_epd_data()
    if not epds:
        return {}

    grouped: dict[tuple[str, str, str], list[float]] = {}
    for e in epds:
        cat = e.get("category", "")
        unit = e.get("unit", "")
        gwp = e.get("gwp_a1a3")
        # Volume-declared products count in the unit they convert to. Timber
        # cladding is declared per m3 while the component is measured in m2, so
        # without this the eighteen wood entries sat in an m3 bucket nobody
        # queries and the m2 typvärde was decided by six entries, two of them
        # aluminium.
        #
        # Scoped to m3 on purpose. kg-declared EPDs also carry a functional-unit
        # figure, but that one leans on a category-wide density assumption and
        # has never fed the medians; folding it in here moved yttervägg/m2 from
        # 39.4 to 207.9 and its ceiling to 6548 kg/m2. The thickness bridge is a
        # geometric fact about the product, the density bridge is an estimate
        # about the category, and only the first belongs in a baseline.
        if unit == "m3":
            fu_gwp = e.get("gwp_per_functional_unit")
            fu_unit = e.get("functional_unit")
            if isinstance(fu_gwp, (int, float)) and fu_unit:
                gwp, unit = fu_gwp, fu_unit
        if not cat or not unit or not isinstance(gwp, (int, float)) or gwp <= 0:
            continue
        if cat in _HETEROGENEOUS_CATEGORIES:
            continue
        if cat in _SUBCATEGORIZED_CATEGORIES:
            # Fixtures (toilets, taps, luminaires, appliances) are counted or
            # weighed — never area/volume. An m2/m3-declared EPD here is a
            # misclassification (produced absurd phantoms like blandare/m2 =
            # 1660), so only keep st and kg.
            if unit not in ("st", "kg"):
                continue
            sub = _epd_subcategory(cat, e)
            if not sub:
                continue  # unclassified item in a heterogeneous category
        elif cat in _SUBTYPE_PREFERRED_CATEGORIES:
            # Counted TWICE on purpose, once in its own subtype bucket and once
            # in the category bucket. The category aggregate has to stay
            # complete, because it is the fallback for every subtype too thin to
            # publish — building it from the leftovers instead would make it the
            # mean of exactly the floors nobody could name.
            sub = _epd_subcategory(cat, e)
            if sub:
                grouped.setdefault((cat, sub, unit), []).append(float(gwp))
            sub = ""
        else:
            sub = ""
        grouped.setdefault((cat, sub, unit), []).append(float(gwp))

    result: dict[tuple[str, str, str], dict] = {}
    for key, values in grouped.items():
        cat, sub, _unit = key
        min_required = _MIN_SAMPLES_OVERRIDE.get((cat, sub), _MIN_SAMPLES)
        if len(values) < min_required:
            continue
        result[key] = {
            "baseline_co2e_per_unit": round(_upper_half_median(values), 2),
            "sample_size": len(values),
            "full_median": round(median(values), 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "subcategory": sub,
            # "subtype" | "category" — which level the number actually came
            # from. Carried in the payload rather than inferred by the caller
            # from a non-empty subcategory, because a subtype request that fell
            # back to the category must not read as a subtype answer.
            "level": "subtype" if sub else "category",
        }
    return result


_TYPVÄRDEN: dict[tuple[str, str, str], dict] | None = None


# Swedish material names -> EPD subtype key, for the subtype-preferred
# categories. Kept separate from palats_client's SUBCATEGORY_KEYWORDS on
# purpose: that taxonomy classifies second-hand LISTINGS and is tuned against
# 701 live ads, this one reads the standard material the baseline agent named
# ("Homogen vinylmatta (PVC)"). Same words, different job, and coupling them
# would mean a baseline tweak could silently move the reuse search.
#
# Order matters, substring match, most specific first. "linoleum" before
# "matta" because a linoleum floor is often written "linoleummatta", and
# "plastmatta" before the bare "matta" that textilgolv shares.
_MATERIAL_SUBTYPE_KEYWORDS: dict[str, list[tuple[str, list[str]]]] = {
    "golv": [
        ("linoleum", ["linoleum", "marmoleum"]),
        ("laminat", ["laminat"]),
        ("keramik", ["klinker", "kakel", "keramik", "terrazzo", "granitkeramik"]),
        ("trä", ["parkett", "trägolv", "massivt trä", "ekgolv", "furugolv",
                 "bambu", "kork"]),
        ("gummi", ["gummi"]),
        ("epoxi", ["epoxi", "härdplast", "akrylat", "polyuretangolv"]),
        ("vinyl", ["vinyl", "plastmatta", "pvc", "homogen matta",
                   "heterogen matta", "plastgolv"]),
        ("textil", ["textil", "heltäckningsmatta", "nålfilt", "mattplatt",
                    "matta"]),
    ],
}


def subtype_from_material(category: str, material: str) -> str:
    """Infer the EPD subtype key from a named standard material.

    Returns "" when the category has no subtype taxonomy or nothing matched —
    the caller then gets the category aggregate, which is a valid answer here
    (unlike the sanitet/belysning/vitvaror split, where a miss means no number).
    """
    subs = _MATERIAL_SUBTYPE_KEYWORDS.get(category)
    if not subs or not material:
        return ""
    text = material.lower()
    for subtype, keywords in subs:
        if any(kw in text for kw in keywords):
            return subtype
    return ""


def get_baseline_typvärde(category: str, unit: str, subcategory: str = "") -> dict | None:
    """Look up EPD-baseline typvärde for a (category, unit[, subcategory]).

    Three behaviours, by category:

    - subcategorized (sanitet, belysning, vitvaror): a subcategory is REQUIRED.
      A flat mean over toilets and taps is meaningless, so a miss returns None
      and the caller falls through to an LLM estimate.
    - subtype-preferred (golv): the subtype is tried first and the category
      aggregate is the fallback, so a floor the catalog has too few of still
      gets a number. Read ``level`` to tell the two apart.
    - everything else: subcategory is ignored.

    Returns a dict with baseline_co2e_per_unit, sample_size, full_median, min,
    max, subcategory and level — or None if no usable typvärde exists. Cached
    lazily on first call.
    """
    global _TYPVÄRDEN
    if _TYPVÄRDEN is None:
        _TYPVÄRDEN = _compute_typvärden()

    if category in _SUBTYPE_PREFERRED_CATEGORIES and subcategory:
        hit = _TYPVÄRDEN.get((category, subcategory, unit))
        if hit:
            return hit
        # Fall through to the category aggregate below. Deliberately not
        # returning None: an unfamiliar or thin floor subtype should still get a
        # baseline, just an honestly labelled one.

    sub = subcategory if category in _SUBCATEGORIZED_CATEGORIES else ""
    return _TYPVÄRDEN.get((category, sub, unit))


# Back-compat alias — old call sites used "median" terminology before we
# switched to upper-half methodology. Same value, clearer name.
def get_baseline_median(category: str, unit: str, subcategory: str = "") -> dict | None:
    """Deprecated — use get_baseline_typvärde. Kept for back-compat."""
    data = get_baseline_typvärde(category, unit, subcategory)
    if data is None:
        return None
    # Synthesize the old key name from the new structure
    return {
        **data,
        "median_co2e_per_unit": data["baseline_co2e_per_unit"],
    }


def list_available_categories() -> list[tuple[str, str, str, int]]:
    """List all (category, subcategory, unit, sample_size) with a typvärde."""
    global _TYPVÄRDEN
    if _TYPVÄRDEN is None:
        _TYPVÄRDEN = _compute_typvärden()
    return sorted(
        [(cat, sub, unit, data["sample_size"])
         for (cat, sub, unit), data in _TYPVÄRDEN.items()],
        key=lambda x: (x[0], x[1], x[2]),
    )


def main():
    """CLI: print the typvärde table for inspection."""
    print(f"{'Kategori':<18} {'Subkat':<14} {'Unit':<5} {'n':>3} "
          f"{'min':>8} {'med':>8} {'typvärde':>10} {'max':>8}")
    print("-" * 84)
    for (cat, sub, unit), data in sorted(_compute_typvärden().items()):
        print(
            f"{cat:<18} {sub:<14} {unit:<5} {data['sample_size']:>3} "
            f"{data['min']:>8.2f} {data['full_median']:>8.2f} "
            f"{data['baseline_co2e_per_unit']:>10.2f} {data['max']:>8.2f}"
        )


if __name__ == "__main__":
    main()
