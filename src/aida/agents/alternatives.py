"""Alternatives agent: finds climate-optimized and reuse alternatives per component.

Uses pre-categorized Environdec EPD data to give the LLM real product-specific
GWP values. The LLM acts as expert, selecting and reasoning about the best
alternatives from the EPD data it receives.
"""

from __future__ import annotations

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger(__name__)

from aida.api_client import (
    DEFAULT_MODEL,
    EFFORT_HIGH,
    EFFORT_MEDIUM,
    REASONING_MAX_TOKENS,
    call_model,
    extract_text,
    get_client,
)
from aida.data.climate_data import (
    normalize_component_name,
    resolve_category,
)
from aida.llm_json import extract_json_object, extract_json_value
from aida.models import (
    Alternative,
    AlternativesResult,
    Baseline,
    ComponentAlternatives,
    NeedsAnalysis,
    Project,
)

EPD_ALTERNATIVES_PATH = Path(__file__).parent.parent / "data" / "epd_alternatives.json"

# The catalog stores every validated EPD per category (so baseline tier 2 can
# compute an honest upper-half median over the full GWP distribution). For the
# alternatives prompt we only want the best candidates to suggest, so we slice
# the N lowest-GWP per category at load time. This keeps the per-component
# prompt bounded and reproduces the pre-decoupling behaviour, where the catalog
# was itself capped to the lowest-GWP N.
_MAX_ALTERNATIVES_PER_CATEGORY = 80

# Heterogeneous categories whose candidates are capped and filtered PER
# subcategory (a toilet, a tap and a basin live in "sanitet" but are not
# interchangeable alternatives). Mirrors epd_baseline_medians.
_SUBCATEGORIZED_CATEGORIES = {"sanitet", "belysning", "vitvaror"}

# Cheap, fast model for the retrieval router (same as the orchestrator's
# intent classifier). Routing is a short structured-classification task.
# Material routing is a correctness step (the toalett/kakel mis-routing lived
# here) — runs on the default Opus 4.8 model now, not the old Haiku.
_ROUTER_MODEL = DEFAULT_MODEL

SYSTEM_PROMPT = """Du är AIda:s alternativanalys-agent — en byggnadsexpert som hittar klimatsmartare alternativ till konventionella byggmaterial.

UPPDRAG:
Hjälpa förvaltare och byggledare att hitta renoveringslösningar som kraftigt minskar klimatpåverkan utan att ge avkall på praktiska behov. Varje procentenhet reduktion räknas.

Du får:
1. En komponent med baslinjevärde (Boverket Typical, konventionellt standardmaterial)
2. En lista med FAKTISKA EPD:er (Environmental Product Declarations) från Environdec-databasen, med verifierade GWP-värden

Din uppgift:
1. Analysera EPD-listan och välj de 2-4 mest relevanta alternativen med lägre klimatpåverkan
2. Beräkna total CO2e baserat på EPD-värdet × antal enheter
3. Resonera om varför alternativet är bättre — beskriv BÅDE klimatvinsten och hur det uppfyller praktiska behov

PRINCIPER FÖR ALTERNATIV:
- Användaren optimerar TOTALEN över hela projektet, inte per komponent. En komponent kan välja ett dyrare eller högre CO2e-alternativ om totalen blir bättre tack vare stora vinster på andra komponenter. Filtrera därför INTE bort alternativ enbart för att deras CO2e råkar vara högre än baslinjen — visa relevanta valmöjligheter med tydlig +/- jämförelse i reasoning. Rangordna gärna med lägst CO2e först så användaren ser besparingen, men inkludera även likvärdiga eller marginellt högre alternativ när de är funktionellt relevanta.
- Uttryckta behov är oförhandlingsbara — inget alternativ som inte uppfyller dem.
- Resonera om hur alternativen möter behov: både uttryckta och antagna (ljudmiljö, inomhusklimat, underhåll, estetik, arbetsmiljö vid installation).
- Presentera spridning i pris — det är användarens beslut att väga ekonomi mot klimat.
- Var innovativ — föreslå kombinationer som löser flera behov samtidigt.
- Förklara installationsaspekter som påverkar totalkostnaden (enklare montering kan kompensera dyrare material).

TEKNISKA REGLER:
- VÄLJ BARA alternativ från EPD-listan du får. Fabricera INGA egna alternativ.
- Om ingen EPD i listan passar komponenten, returnera en tom array [].
- Använd GWP-värdena från EPD-listan — de är GWP-fossil A1-A3 (samma metod som Boverket-baslinjen), verifierade och direkt jämförbara.
- Om ett omräknat värde visas (efter →), använd det omräknade värdet för beräkningar.
- Ange EPD-registreringsnummer i source-fältet.
- Prioritera svenska/nordiska produkter (SE, NORD, RER).
- Om EPD-värdet är i en annan enhet (kg) än projektets enhet (m2, st), gör en rimlig omräkning och notera det.
- co2e_kg MÅSTE vara > 0 — alla byggmaterial har klimatpåverkan. Returnera aldrig 0.
- Föreslå KOMPLETTA system, inte enskilda komponenter.
- Föreslå INTE återbruksprodukter — dessa hanteras separat via Palats marknadsplats.
- alternative_type ska ALLTID vara "climate_optimized" (aldrig "reuse").

PRISER:
- Alla priser avser installerat pris (material + arbete) i SEK exklusive moms.
- Sätt cost_sek till 0 om du inte vet — priser hämtas automatiskt via webbsökning efteråt.

Svara med giltig JSON-array:
[
  {
    "name": "Produktnamn (Tillverkare)",
    "co2e_kg": <total CO2e i kg>,
    "cost_sek": <uppskattad kostnad i SEK, 0 om okänt>,
    "source": "[EPD] Environdec <registreringsnummer>",
    "reasoning": "Varför detta alternativ är bättre (klimat + praktiska behov)",
    "alternative_type": "climate_optimized"
  }
]"""


def _load_epd_alternatives() -> dict[str, list[dict]]:
    """Load pre-categorized EPD alternatives, grouped by AIda category.

    The best-N (lowest GWP) are kept for the alternatives prompt; the full
    distribution stays in the catalog for baseline tier 2. For heterogeneous
    categories (sanitet/belysning/vitvaror) the best-N are kept PER SUBCATEGORY
    — otherwise low-GWP taps fill the category-wide cap and starve a WC-stol
    component of toilets (it then gets offered a tap or a toilet seat).
    """
    if not EPD_ALTERNATIVES_PATH.exists():
        return {}
    try:
        with open(EPD_ALTERNATIVES_PATH) as f:
            data = json.load(f)
        result: dict[str, list[dict]] = {}
        for epd in data:
            cat = epd.get("category", "")
            if cat and epd.get("gwp_a1a3", 0) > 0:
                result.setdefault(cat, []).append(epd)
        for cat, epds in result.items():
            if cat in _SUBCATEGORIZED_CATEGORIES:
                by_sub: dict[str, list[dict]] = {}
                for e in epds:
                    by_sub.setdefault(e.get("subcategory", ""), []).append(e)
                flat: list[dict] = []
                for sub_epds in by_sub.values():
                    sub_epds.sort(key=lambda e: abs(e.get("gwp_a1a3", 0)))
                    flat.extend(sub_epds[:_MAX_ALTERNATIVES_PER_CATEGORY])
                result[cat] = flat
            else:
                epds.sort(key=lambda e: abs(e.get("gwp_a1a3", 0)))
                result[cat] = epds[:_MAX_ALTERNATIVES_PER_CATEGORY]
        return result
    except (json.JSONDecodeError, OSError):
        return {}


def _match_key(name: str) -> str:
    """Normalise a product name for comparison.

    The model retypes names rather than copying them, and the differences are
    invisible: it wrote "Outdoor panel spruce – black (Sveden Trä)" for a
    catalog entry called "Outdoor panel spruce - black". An en dash instead of
    a hyphen was enough to lose the match, and with it the GWP-GHG marking that
    is the whole condition for using that fallback. Caught on a live run
    2026-08-17, where three spruce panels surfaced unmarked.
    """
    lowered = name.strip().lower().rstrip("*").strip()
    for dash in ("‐", "‑", "‒", "–", "—", "―", "−"):
        lowered = lowered.replace(dash, "-")
    # Trademark marks disappear just as invisibly: the model writes "BCLarch
    # profiled cladding" for a catalog entry named "BCLarch® profiled cladding".
    for mark in ("®", "™", "©"):
        lowered = lowered.replace(mark, "")
    lowered = lowered.replace(" ", " ")
    return " ".join(lowered.split())


def _tokens(key: str) -> set[str]:
    """Distinctive words in a normalised name, for when containment fails."""
    return {
        "".join(ch for ch in word if ch.isalnum())
        for word in key.split()
        if len(word) >= 3
    } - {""}


def match_epd_by_name(name: str, epds: list[dict]) -> dict | None:
    """Find which candidate EPD an LLM-written alternative name refers to.

    The model paraphrases and truncates product names, so exact equality is
    useless. Containment either way first, longest match wins; then a token
    overlap for the cases containment cannot reach, such as
    "Fibre cement cladding HardiePanel® / Hardie® Architectural Panel" for a
    catalog entry called "S-P-10857 Fibre cement cladding: HardiePanel®,
    Hardie® Architectural Panel" — same product, reordered and re-punctuated.

    Used to carry facts the model cannot be trusted to relay (which GWP
    indicator a figure rests on) from the catalog onto the alternative.
    """
    if not name:
        return None
    needle = _match_key(name)
    if not needle:
        return None
    best = None
    best_len = 0
    tied: list[dict] = []
    for epd in epds:
        epd_name = _match_key(epd.get("name") or "")
        if not epd_name:
            continue
        if epd_name == needle:
            return epd
        if epd_name in needle or needle in epd_name:
            overlap = min(len(epd_name), len(needle))
            if overlap > best_len:
                best, best_len, tied = epd, overlap, [epd]
            elif overlap == best_len:
                tied.append(epd)
    if best is not None:
        # A tie is only a problem when the tied entries disagree about the
        # thing we are carrying across. Two equally-matching fossil products
        # give the same answer either way; one fossil and one GHG do not, and
        # guessing there would put a label on a product that may not deserve it.
        if len({e.get("gwp_basis", "") for e in tied}) > 1:
            return None
        return best

    # Token overlap, for names the model reordered or re-punctuated past what
    # containment can follow. Deliberately strict: at least three quarters of
    # the catalog entry's distinctive words must appear, and the winner has to
    # be clearly ahead of the runner-up. A wrong match here would put a
    # GWP-GHG label on a product that does not deserve one, which is worse than
    # leaving a fossil figure unlabelled.
    needle_tokens = _tokens(needle)
    if len(needle_tokens) < 2:
        return None
    scored: list[tuple[float, dict]] = []
    for epd in epds:
        epd_tokens = _tokens(_match_key(epd.get("name") or ""))
        if len(epd_tokens) < 2:
            continue
        score = len(epd_tokens & needle_tokens) / len(epd_tokens)
        if score >= 0.75:
            scored.append((score, epd))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.15:
        return None  # ambiguous, better to say nothing
    return scored[0][1]


def _format_epd_list(epds: list[dict]) -> str:
    """Format EPD list for inclusion in prompt."""
    lines = []
    for epd in epds:
        reg = epd.get("reg_no", "")
        reg_str = f" ({reg})" if reg else ""

        # GWP-fossil A1-A3 normally, matching Boverket's standard so
        # alternatives are comparable to the baseline. GWP-total (which includes
        # biogenic carbon credit and can be negative for bio-based products) is
        # intentionally never shown, to avoid mixing bases in the same list.
        # The one exception is an EPD whose own components did not add up, where
        # the build falls back to GWP-GHG; that is named here rather than
        # blended in, so the model does not present it as like for like.
        basis_label = "GWP-GHG" if epd.get("gwp_basis") == "ghg" else "GWP-fossil"
        gwp_str = f"{basis_label} A1-A3: {epd['gwp_a1a3']} kg CO2e/{epd['unit']}"
        fu_gwp = epd.get("gwp_per_functional_unit")
        fu_unit = epd.get("functional_unit")
        if fu_gwp is not None and fu_unit:
            gwp_str += f" \u2192 {fu_gwp} kg CO2e/{fu_unit}"

        source = epd.get("source_registry", "environdec")
        source_tag = f" [{source}]" if source != "environdec" else ""

        lines.append(
            f"- {epd['name']} | {epd.get('owner', '?')} | "
            f"{gwp_str} | "
            f"Geo: {epd.get('geo', '?')}{reg_str}{source_tag}"
        )
    return "\n".join(lines)


# Keywords that indicate a component part rather than a complete system.
# Used to filter out alternatives that aren't apples-to-apples with a full baseline.
_COMPONENT_ONLY_KEYWORDS = [
    "membran",
    "ångspärr",
    "ångbroms",
    "underlagsduk",
    "underlagstak",
    "diffusionsspärr",
    "tätskikt",
    "fuktspärr",
    "vindskydd",
    "vapor barrier",
    "vapour barrier",
    "membrane",
    "underlayment",
    "underlag",
]


def _is_component_only(name: str) -> bool:
    """Check if an alternative name suggests it's just a component part, not a complete system.

    E.g. a vapor barrier membrane is not a complete roofing alternative.
    """
    name_lower = name.lower()
    return any(kw in name_lower for kw in _COMPONENT_ONLY_KEYWORDS)


def _validate_alternatives(
    alternatives: list[Alternative],
    baseline_co2e: float,
    component_name: str,
    quantity: float = 0,
    category: str | None = None,
) -> list[Alternative]:
    """Filter out alternatives with data quality issues.

    Removes:
    - Alternatives with co2e_kg <= 0 (unrealistic for building materials)
    - Component-only products when the baseline is a complete system
    - climate_optimized alternatives that don't beat the baseline (mislabeled)
    Flags:
    - Alternatives with cost_sek == 0 get "Pris ej tillgängligt"
    - LLM-estimated prices get "Approximerat pris"
    - Out-of-range prices get "Oväntat pris — verifiera"
    - climate_optimized alternatives with an implausibly low CO2e (broken EPD)
    """
    from aida.data.price_validation import validate_total_price

    # Use the routed category when provided so a kakel-routed wall validates
    # prices against kakel ranges, not the name-derived innervägg ranges.
    if category is None:
        category = normalize_component_name(component_name)
    valid = []
    for alt in alternatives:
        # A) Filter zero/negative CO2 — all building materials have emissions
        if alt.co2e_kg is None or alt.co2e_kg <= 0:
            logger.info(
                "Filtered alternative '%s' for %s: co2e_kg=%s (unrealistic)",
                alt.name, component_name, alt.co2e_kg,
            )
            continue

        # B) Filter component-only products (membranes, vapor barriers etc.)
        if _is_component_only(alt.name):
            logger.info(
                "Filtered alternative '%s' for %s: component part, not complete system",
                alt.name, component_name,
            )
            continue

        # B2) Drop climate_optimized options that don't actually beat the
        #     baseline — a "climate-optimized" choice with co2e >= baseline is
        #     mislabeled and produces absurd kr/sparat-kg in the ranking. Reuse
        #     and info entries are exempt (different comparison / no number).
        if (alt.alternative_type == "climate_optimized"
                and baseline_co2e > 0
                and alt.co2e_kg >= baseline_co2e):
            logger.info(
                "Filtered alternative '%s' for %s: co2e %.1f >= baseline %.1f "
                "(not an improvement)",
                alt.name, component_name, alt.co2e_kg, baseline_co2e,
            )
            continue

        # B3) Flag implausibly-low climate_optimized values — a new product at
        #     <3%% of the baseline is almost certainly a broken EPD (partial
        #     module / wrong unit) that slipped past the catalog floor. Keep it
        #     for transparency but mark it for verification.
        if (alt.alternative_type == "climate_optimized"
                and baseline_co2e > 0
                and alt.co2e_kg < baseline_co2e * 0.03):
            if "verifiera" not in alt.reasoning.lower():
                alt.reasoning = alt.reasoning.rstrip(". ") + (
                    ". Ovanligt lågt CO2e — verifiera mot EPD:n "
                    "(kan vara partiell modul eller felaktig enhet)."
                )

        # C) Price validation — flag zero prices (filtered after enrichment)
        if alt.cost_sek is None or alt.cost_sek <= 0:
            alt.cost_sek = 0
            if "pris ej tillgängligt" not in alt.reasoning.lower():
                alt.reasoning = alt.reasoning.rstrip(". ") + ". Pris ej tillgängligt."
        elif quantity > 0:
            is_estimate = "[uppskattning]" in alt.source.lower()
            _cost, note = validate_total_price(
                alt.cost_sek, quantity, category, is_estimate=is_estimate,
            )
            if note and note.lower() not in alt.reasoning.lower():
                alt.reasoning = alt.reasoning.rstrip(". ") + f". {note}."

        valid.append(alt)

    return valid


def _b1_keep_alternative(alt) -> bool:
    """DoD B1: should this alternative survive the post-enrichment zero-price cut?

    Keep an alternative if it is actionable on at least one axis:
    - baseline/info/reuse entries are never price-cut (reuse is legitimately free)
    - it has a real price (cost_sek > 0) → buyable
    - it is EPD-backed ([EPD] source) → carries a verified CO2 number, the core
      value of a climate tool, even when its obscure product name can't be priced

    Only unpriced AND non-EPD alternatives (pure LLM guesses, unactionable on both
    price and climate verifiability) are dropped.
    """
    if alt.alternative_type in ("baseline", "info", "reuse"):
        return True
    if alt.cost_sek is not None and alt.cost_sek > 0:
        return True
    return "[epd]" in (alt.source or "").lower()


def _effective_baseline_co2e(
    proj_comp, bl_comp, routed_category: str | None, has_directive: bool = False,
) -> float:
    """Baseline reference that follows the routed material category.

    Retroactive-directive fix (Fas 2): when a directive (or usage_context)
    reroutes a component to a different material than its stored baseline was
    computed for — e.g. a "Väggytskikt" whose baseline is innervägg, rerouted to
    kakel by a "ge kakel"-directive added AFTER the baseline was set — the stored
    baseline is for the WRONG material. Comparing kakel alternatives against an
    innervägg baseline makes RC5 (climate_optimized must beat the baseline) drop
    every kakel option ("noll alternativ"), and the report would show an
    innervägg baseline next to kakel choices.

    So when the routed category diverges from the baseline's original category,
    recompute the conventional baseline on the routed category from its EPD
    typvärde. This keeps baseline + alternatives on the SAME material (the #420
    invariant) for the retroactive case, without a separate baseline rerun.

    A genuine Boverket-material baseline (Tier 1) is more accurate than a
    category typvärde, so it is NOT downgraded on a mere name/usage_context
    disagreement between the router and resolve_category. It IS rerouted when the
    user gave an explicit directive (``has_directive``) — there the user is
    actively changing the material, so the old material match no longer applies.

    Returns the stored ``bl_comp.co2e_kg`` unchanged when nothing diverged, the
    baseline is a protected Boverket hit, or the routed category has no usable
    typvärde (can't honestly recompute). Only the CO2e reference moves; cost
    stays as-is (no routed-category price source).
    """
    from aida.data.epd_baseline_medians import get_baseline_typvärde
    from aida.data.palats_client import component_subcategory
    from aida.data.unit_conversion import typical_item_mass

    orig_category = resolve_category(proj_comp.name, proj_comp.category)
    if not routed_category or routed_category == orig_category:
        return bl_comp.co2e_kg

    # Tier 1 Boverket baselines: don't silently downgrade to a typvärde unless an
    # explicit directive overrides the material (boverket_product is set only for
    # genuine Boverket hits — cleared for typvärde/uppskattning in baseline.py).
    if getattr(bl_comp, "boverket_product", "") and not has_directive:
        logger.debug(
            "Component %r has Boverket baseline and no directive; keeping it "
            "(router said %s, baseline category %s).",
            proj_comp.name, routed_category, orig_category,
        )
        return bl_comp.co2e_kg

    subcat = component_subcategory(proj_comp.name, routed_category)
    tv = get_baseline_typvärde(routed_category, proj_comp.unit, subcat)
    # kg->st bridge, mirroring _apply_epd_median_fallback in baseline.py: a
    # count-denominated component whose routed category only has a kg typvärde.
    if not tv and proj_comp.unit == "st":
        kg_tv = get_baseline_typvärde(routed_category, "kg", subcat)
        mass = typical_item_mass(routed_category, subcat)
        if kg_tv and mass:
            tv = {"baseline_co2e_per_unit": kg_tv["baseline_co2e_per_unit"] * mass}
    if not tv:
        logger.warning(
            "Component %r rerouted %s->%s but routed category has no typvärde; "
            "keeping stored baseline (RC5 may still mismatch).",
            proj_comp.name, orig_category, routed_category,
        )
        return bl_comp.co2e_kg

    rerouted = round(tv["baseline_co2e_per_unit"] * proj_comp.quantity, 1)
    logger.info(
        "Component %r rerouted %s->%s: baseline %s -> %s kg (follows directive)",
        proj_comp.name, orig_category, routed_category, bl_comp.co2e_kg, rerouted,
    )
    return rerouted


def _add_palats_reuse(
    alternatives: list[Alternative],
    component_name: str,
    quantity: float,
    project_unit: str,
    palats_listings: list[dict],
) -> None:
    """Add matching Palats reuse listings as alternatives (in-place).

    Palats listings get minimal CO2e (transport/refurbishment only) and
    actual marketplace prices.

    Pricing logic:
    - If project counts in "st" (fönster, dörr), Palats price * quantity
      gives a directly comparable total.
    - If project counts in "m2" (golv, vägg), we can't calculate total
      (unknown coverage per article). Show per-article price instead.
    """
    from aida.data.palats_client import (
        _DEFAULT_REUSE_CO2E,
        REUSE_CO2E_PER_UNIT,
        component_subcategory,
        search_listings_for_component,
    )

    matched = search_listings_for_component(component_name, palats_listings)
    category = normalize_component_name(component_name)
    target_subcat = component_subcategory(component_name, category) if category else ""

    # Strict subcategory filter: when the user asked for a specific subcategory
    # (e.g. "Toalettstol" → subcat "toalett"), drop listings from other
    # subcategories (handfat, dusch, etc.) entirely. They're wrong product
    # type for this component — showing them as "alternatives" is misleading,
    # not graceful degradation. Only fall back to broader category matches
    # when no subcategory keyword was inferable from the component name.
    if target_subcat:
        subcat_matches = [m for m in matched if m.subcategory == target_subcat]
        if not subcat_matches and matched:
            # Tell the user nothing matches, list what *is* available in the
            # broader category so they can decide if a related part fits.
            other_subcats = sorted({m.subcategory for m in matched if m.subcategory})
            other_label = ", ".join(other_subcats) if other_subcats else "annan typ"
            alternatives.append(Alternative(
                name=f"Inget {component_name.lower()} på Palats just nu",
                co2e_kg=0,
                cost_sek=0,
                source="[Palats] palats.app",
                reasoning=(
                    f"Palats har {len(matched)} produkter i kategorin {category} just nu "
                    f"({other_label}) — men inget specifikt {component_name.lower()}. "
                    "Kolla tillbaka när nya annonser publicerats, eller bredda sökningen "
                    "manuellt på palats.app."
                ),
                alternative_type="info",
            ))
            return
        matched = subcat_matches

    if not matched:
        return

    existing_names = {a.name.lower() for a in alternatives}
    co2e_per_unit = REUSE_CO2E_PER_UNIT.get(category, _DEFAULT_REUSE_CO2E)
    units_match = project_unit.lower() in ("st", "styck", "stk")

    for listing in matched[:5]:  # Cap at 5 reuse listings per component
        if listing.title.lower() in existing_names:
            continue

        total_co2e = co2e_per_unit * quantity

        if units_match and listing.price > 0:
            # Units match (both "st") — total is directly comparable.
            #
            # The total covers the FULL component quantity even when fewer are
            # in stock, and so does total_co2e above. That is deliberate: AIda
            # plans early, and stock turns over long before procurement
            # (Henric, 2026-08-15). What was wrong was saying nothing about it.
            # Live check 2026-08-14: 30 windows needed, best listing had 3, and
            # the row read "9 600 kr" with no hint that 27 were assumed.
            total_cost = listing.price * quantity
            price_note = f"Pris: {listing.price:.0f} SEK/st × {int(quantity)} = {int(total_cost)} SEK"
            if listing.quantity < quantity:
                price_note += (
                    f" | OBS: {listing.quantity} av {int(quantity)} finns i lager just nu."
                    " Pris och klimatnytta räknas på hela behovet, alltså som om"
                    " resten går att få tag på begagnat. Kontrollera tillgången"
                    " innan siffran används i ett beslutsunderlag."
                )
            cost_is_estimate = False
        elif listing.price > 0:
            # Units don't match — show per-article price only
            total_cost = listing.price
            price_note = f"Pris: {listing.price:.0f} SEK/st ({listing.quantity} tillgängliga) — yta per artikel okänd"
            cost_is_estimate = True
        else:
            total_cost = 0
            price_note = f"{listing.quantity} tillgängliga"
            cost_is_estimate = False

        location_note = f"Plats: {listing.location}" if listing.location else ""
        url_note = f"Se annons: {listing.url}" if listing.url else ""
        detail_parts = [p for p in [price_note, location_note, url_note] if p]
        detail_str = " | ".join(detail_parts)

        reasoning = (
            "Återbruk via Palats (Karlstads kommuns interna marknadsplats) "
            "eliminerar nästan all tillverkningsrelaterad klimatpåverkan. "
            "Kvarvarande CO2e kommer främst från transport och eventuell renovering."
        )
        if detail_str:
            reasoning += f" {detail_str}"
        if cost_is_estimate:
            reasoning += " OBS: Priset avser en artikel, inte totalbehovet."
        if listing.description:
            desc_preview = listing.description[:150]
            if len(listing.description) > 150:
                desc_preview += "..."
            reasoning += f" Beskrivning: {desc_preview}"

        # Mark name with * when cost is per-article, not total
        display_name = f"{listing.title} (Palats återbruk, {listing.location})" if listing.location else f"{listing.title} (Palats återbruk)"
        if cost_is_estimate:
            display_name += " *"

        alternatives.append(Alternative(
            name=display_name,
            co2e_kg=round(total_co2e, 1),
            cost_sek=round(total_cost),
            source=f"[Palats] palats.app/listing/{listing.id}",
            reasoning=reasoning,
            alternative_type="reuse",
            available_quantity=listing.quantity,
            price_basis="listing" if listing.price > 0 else "",
        ))
        existing_names.add(listing.title.lower())


def _route_components(
    project: Project,
    baseline_components: list,
    available_categories: set[str],
    user_feedback: str | None = None,
) -> dict[str, str]:
    """LLM-route each component to the EPD category its alternatives fit best.

    Why: retrieval is otherwise locked to normalize_component_name(name), which
    maps a "Väggytskikt" to innervägg even when it's a tiled wet-room wall, and
    a user directive ("ge kakel-alternativ") can't pull kakel EPDs. The router
    reads the component name + usage_context + directive and picks the apt
    catalog category. Returns {component_id: category}. Falls back to
    normalize_component_name on ANY failure — it never blocks the pipeline.
    """
    fallback: dict[str, str] = {}
    items: list[dict] = []
    for bl in baseline_components:
        proj_comp = next(
            (c for c in project.components if c.id == bl.component_id), None)
        name = proj_comp.name if proj_comp else bl.component_name
        declared = proj_comp.category if proj_comp else ""
        fallback[bl.component_id] = resolve_category(name, declared)
        if proj_comp:
            items.append({
                "id": bl.component_id,
                "name": proj_comp.name,
                "unit": proj_comp.unit,
                "usage_context": (proj_comp.usage_context or "")[:300],
            })

    if not items or not available_categories:
        return fallback

    directive = (user_feedback or "").strip()[:500]
    # Skip the LLM call when there's nothing to disambiguate: with no directive
    # and no usage_context, name-based routing is already the right answer.
    if not directive and not any(it["usage_context"] for it in items):
        return fallback

    cats = sorted(available_categories)
    directive_clause = ""
    if directive:
        directive_clause = (
            f'\nANVÄNDARENS ÖNSKEMÅL (kan gälla en eller flera komponenter): '
            f'"{directive}". Om önskemålet anger ett material (t.ex. kakel), '
            f'välj den kategorin för den komponent önskemålet rör, även om '
            f'komponentnamnet antyder ett annat material.'
        )
    prompt = (
        "Du mappar byggkomponenter till rätt materialkategori i en EPD-databas, "
        "så att klimatalternativ hämtas från rätt sorts material.\n\n"
        f"Tillgängliga kategorier: {', '.join(cats)}.\n\n"
        "Regler:\n"
        "- Utgå från komponentens NAMN och ANVÄNDNINGSKONTEXT (vad det faktiskt "
        "är för material/yta).\n"
        "- Kaklad våtrumsvägg eller -golv → kakel (inte innervägg/golv).\n"
        "- Målad yta / ommålning → farg. Rör/stambyte → vvs. Elkabel → el. "
        "Radiator/värmeelement → radiator.\n"
        "- Välj EXAKT en kategori per komponent, ur listan ovan."
        f"{directive_clause}\n\n"
        f"Komponenter:\n{json.dumps(items, ensure_ascii=False)}\n\n"
        'Svara ENBART med JSON: {"c1": "kategori", "c2": "kategori", ...}'
    )
    try:
        client = get_client()
        resp = call_model(
            client,
            model=_ROUTER_MODEL,
            max_tokens=REASONING_MAX_TOKENS,
            effort=EFFORT_HIGH,  # correctness step — material routing (toalett/kakel)
            messages=[{"role": "user", "content": prompt}],
        )
        text = extract_text(resp)
        data = extract_json_object(text, what="kategori-routingen")
    except Exception:
        logger.warning("Component routing failed; using name-based fallback",
                       exc_info=True)
        return fallback

    if not isinstance(data, dict):
        logger.warning("Router returned %s, not a dict; name-based fallback",
                       type(data).__name__)
        return fallback

    routed: dict[str, str] = {}
    for cid, default_cat in fallback.items():
        chosen = data.get(cid)
        if isinstance(chosen, str) and chosen in available_categories:
            if chosen != default_cat:
                logger.info("Routed %s: %s -> %s (was name-based)",
                            cid, default_cat, chosen)
            routed[cid] = chosen
        else:
            routed[cid] = default_cat
    return routed


def find_alternatives(
    project: Project,
    baseline: Baseline,
    user_feedback: str | None = None,
) -> AlternativesResult:
    """Find climate-optimized alternatives for each component.

    Strategy:
    1. Load pre-categorized EPD data from Environdec
    2. Fetch available reuse listings from Palats marketplace
    3. For each component, give the LLM the relevant EPDs + baseline
    4. LLM reasons about best alternatives
    5. Supplement with Palats reuse listings (live)
    6. Fall back to hardcoded reuse data if no Palats results
    """
    from aida.data import palats_client
    from aida.data.palats_client import fetch_listings

    epd_data = _load_epd_alternatives()

    # Route each component to the apt EPD category (name + usage_context +
    # directive), so a tiled "Väggytskikt" or a "ge kakel"-directive pulls kakel
    # EPDs instead of being locked to normalize_component_name's guess.
    routing = _route_components(
        project, baseline.components, set(epd_data.keys()), user_feedback,
    )

    # Fetch Palats listings once for the entire analysis
    palats_listings = fetch_listings()
    palats_status = palats_client.last_fetch_status
    has_palats = len(palats_listings) > 0
    if has_palats:
        logger.info("Palats: %d listings available for reuse matching", len(palats_listings))
    elif palats_status != "ok":
        logger.warning("Palats unavailable (status: %s)", palats_status)

    def _process_component(bl_comp):
        proj_comp = next(
            (c for c in project.components if c.id == bl_comp.component_id),
            None,
        )
        if not proj_comp:
            return None

        comp_key = routing.get(bl_comp.component_id) or resolve_category(
            proj_comp.name, proj_comp.category)
        epds_for_category = epd_data.get(comp_key, [])
        # Baseline reference must follow the routed material (retroactive
        # directive): a kakel-routed wall is validated against the kakel
        # baseline, not the stored innervägg one — else RC5 drops every kakel
        # alternative and the report shows the wrong-material baseline.
        eff_baseline_co2e = _effective_baseline_co2e(
            proj_comp, bl_comp, comp_key,
            has_directive=bool((user_feedback or "").strip()),
        )

        # Note: candidates are NOT narrowed to the component's subcategory.
        # Narrowing to e.g. "toalett" only starved sanitet components of the
        # generic "Ceramic Sanitaryware" EPDs (which carry subcategory "") that
        # are the actual usable alternatives — and the "" bucket also holds
        # non-fixture noise, so admitting it wholesale is wrong too. Instead the
        # loader keeps best-N PER subcategory (so real toilets aren't cut by the
        # category cap), and the LLM picks the apt ones from the balanced set.
        # Precise per-component subcategory retrieval is Fas 2 (semantic).

        alternatives = _find_alternatives_with_epds(
            proj_comp, bl_comp, epds_for_category, user_feedback,
            needs_analysis=project.needs_analysis,
            effective_baseline_co2e=eff_baseline_co2e,
        )

        # Validate data quality: filter zero CO2, component-only parts, flag prices
        alternatives = _validate_alternatives(
            alternatives, eff_baseline_co2e, proj_comp.name, proj_comp.quantity,
            category=comp_key,
        )

        # Add live Palats reuse listings
        if has_palats:
            _add_palats_reuse(
                alternatives, proj_comp.name, proj_comp.quantity,
                proj_comp.unit, palats_listings,
            )

        # Show Palats status: connection error vs no matches for this category
        palats_reuse_count = sum(
            1 for a in alternatives
            if a.alternative_type == "reuse" and "[Palats]" in a.source
        )
        if palats_reuse_count == 0:
            if palats_status in ("no_credentials", "auth_failed"):
                alternatives.append(Alternative(
                    name="Palats ej tillgänglig (autentisering)",
                    co2e_kg=0,
                    cost_sek=0,
                    source="[Palats] palats.app",
                    reasoning=(
                        "Kunde inte ansluta till Palats — autentisering misslyckades. "
                        "Återbruksprodukter kan inte sökas. Kontakta systemadministratör."
                    ),
                    alternative_type="info",
                ))
            elif palats_status == "api_error":
                alternatives.append(Alternative(
                    name="Palats ej tillgänglig (anslutningsfel)",
                    co2e_kg=0,
                    cost_sek=0,
                    source="[Palats] palats.app",
                    reasoning=(
                        "Kunde inte hämta data från Palats — anslutningsfel eller "
                        "timeout. Återbruksprodukter kan inte sökas just nu. "
                        "Försök igen senare."
                    ),
                    alternative_type="info",
                ))
            elif has_palats:
                alternatives.append(Alternative(
                    name="Inget tillgängligt i Palats",
                    co2e_kg=0,
                    cost_sek=0,
                    source="[Palats] palats.app",
                    reasoning=(
                        "Inga matchande återbruksprodukter hittades i Palats "
                        "(Karlstads kommuns interna marknadsplats) för denna kategori "
                        "just nu. Utbudet ändras löpande — kolla igen senare."
                    ),
                    alternative_type="info",
                ))

        selectable = [a for a in alternatives if a.alternative_type != "info"]
        if not selectable:
            alternatives.append(Alternative(
                name=f"Inga alternativ hittades för {proj_comp.name}",
                co2e_kg=eff_baseline_co2e,
                cost_sek=bl_comp.cost_sek,
                source="N/A",
                reasoning="Inga alternativ identifierade.",
                alternative_type="baseline",
            ))

        return ComponentAlternatives(
            component_id=bl_comp.component_id,
            component_name=bl_comp.component_name,
            baseline_co2e_kg=eff_baseline_co2e,
            baseline_cost_sek=bl_comp.cost_sek,
            alternatives=alternatives,
        )

    # Run per-component LLM calls in parallel (I/O-bound).
    # max(1, ...) guards against an empty baseline — ThreadPoolExecutor(0) raises.
    max_workers = max(1, min(len(baseline.components), 5))
    results_map = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_comp = {
            executor.submit(_process_component, bl): bl
            for bl in baseline.components
        }
        for future in as_completed(future_to_comp):
            bl = future_to_comp[future]
            try:
                r = future.result()
                if r:
                    results_map[bl.component_id] = r
            except Exception:
                logger.warning("Component failed: %s", bl.component_name, exc_info=True)

    # Preserve original component order
    component_results = [
        results_map[bl.component_id]
        for bl in baseline.components
        if bl.component_id in results_map
    ]

    # Batch price enrichment for alternatives missing prices
    _enrich_alternative_prices(component_results, project, routing)

    # DoD B1: drop alternatives still at cost_sek=0 after enrichment — but only
    # those unactionable on BOTH axes (no price AND no verified EPD). An
    # alternative earns its place by being actionable on at least one:
    #   - has a real price → buyable
    #   - is EPD-backed ([EPD] source) → carries a verified CO2 number, which is
    #     the whole point of a climate tool. These obscure EPD products (foreign
    #     ceramic manufacturers etc.) often can't be web-priced, but deleting
    #     them lost real kakel/facade climate options entirely (Johanna's
    #     toalettblock: every kakel alternative vanished here even after the
    #     baseline reroute). Keep them flagged "Pris ej tillgängligt" (the
    #     validator already adds that note) instead of hiding the climate signal.
    # "reuse" stays exempt: Palats reuse listings are legitimately free.
    for comp in component_results:
        before = len(comp.alternatives)
        comp.alternatives = [a for a in comp.alternatives if _b1_keep_alternative(a)]
        removed = before - len(comp.alternatives)
        if removed:
            logger.info("B1 filter: removed %d unpriced non-EPD alternatives from %s", removed, comp.component_name)

    result = AlternativesResult(components=component_results)
    result.commentary = _generate_commentary(project, baseline, result)
    return result


def _enrich_alternative_prices(
    components: list[ComponentAlternatives],
    project: Project,
    routing: dict[str, str] | None = None,
) -> None:
    """Batch web search for alternatives that have cost_sek == 0.

    Web search returns prices per unit (SEK/m², SEK/st). We multiply by
    the project's component quantity here to produce a total in SEK —
    storing the per-unit value as total would understate cost by the
    quantity factor (e.g. 725 SEK vs 45 × 725 for 45 m² of flooring).

    After each enrichment we run validate_total_price as a safety net:
    it catches both out-of-range prices and the per-unit-as-total bug
    class, in case a future change to the enrichment path regresses.

    Single batch call — alternatives still at 0 after this get filtered
    downstream (DoD B1). Individual sequential lookups were removed to
    avoid 5+ minute timeouts.
    """
    from aida.data.price_validation import validate_total_price
    from aida.data.pricing_provider import lookup_prices_batch

    quantity_by_cid = {c.id: c.quantity for c in project.components}
    routing = routing or {}
    category_by_cid = {
        c.component_id: (routing.get(c.component_id)
                         or normalize_component_name(c.component_name))
        for c in components
    }

    products_needing_prices: list[tuple[str, str]] = []
    alt_index: list[tuple[int, int]] = []  # (comp_idx, alt_idx) for mapping back

    for ci, comp in enumerate(components):
        for ai, alt in enumerate(comp.alternatives):
            # "reuse" excluded: a Palats reuse listing is a specific second-hand
            # item; web-searching a generic market price for it is meaningless,
            # and a zero price is legitimate (free on the municipal marketplace).
            if alt.cost_sek <= 0 and alt.alternative_type not in ("baseline", "info", "reuse"):
                products_needing_prices.append((alt.name, ""))
                alt_index.append((ci, ai))

    if not products_needing_prices:
        return

    # Pass 1: Batch web search
    batch_prices = lookup_prices_batch(products_needing_prices)

    if batch_prices:
        for (ci, ai), (name, _unit) in zip(alt_index, products_needing_prices):
            price_result = batch_prices.get(name.lower())
            if price_result:
                price_per_unit, unit, _source = price_result
                comp = components[ci]
                alt = comp.alternatives[ai]
                quantity = quantity_by_cid.get(comp.component_id, 0) or 0
                if quantity > 0:
                    alt.cost_sek = round(price_per_unit * quantity)
                else:
                    alt.cost_sek = round(price_per_unit)
                # A typical installed price for this KIND of material, not this
                # product's asking price. The table renders it in the same
                # column as a real Palats listing price, so it has to be
                # labelled as what it is.
                alt.price_basis = "market_estimate"
                alt.reasoning = alt.reasoning.replace(". Pris ej tillgängligt.", "")
                alt.reasoning = alt.reasoning.replace("Pris ej tillgängligt.", "")

                # Safety net: validate the enriched total. Detects both
                # per-unit-as-total regressions and out-of-range prices.
                category = category_by_cid.get(comp.component_id, "")
                if quantity > 0 and category:
                    validated_cost, note = validate_total_price(
                        alt.cost_sek, quantity, category,
                        is_estimate=False,
                    )
                    if validated_cost != alt.cost_sek:
                        alt.cost_sek = validated_cost
                    if note and note.lower() not in alt.reasoning.lower():
                        alt.reasoning = alt.reasoning.rstrip(". ") + f". {note}."

                logger.info(
                    "Enriched price for '%s': %d SEK/%s x %g = %d SEK total",
                    alt.name, round(price_per_unit), unit, quantity, alt.cost_sek,
                )

    # Pass 2: Log any still missing — individual lookups removed to avoid timeout
    still_missing = sum(
        1 for comp in components for alt in comp.alternatives
        if alt.cost_sek <= 0 and alt.alternative_type not in ("baseline", "info")
    )
    if still_missing:
        logger.info("%d alternatives still missing price after batch lookup", still_missing)


def _find_alternatives_with_epds(
    proj_comp,
    bl_comp,
    epds: list[dict],
    user_feedback: str | None = None,
    needs_analysis: NeedsAnalysis | None = None,
    effective_baseline_co2e: float | None = None,
) -> list[Alternative]:
    """Use LLM to select best alternatives from EPD data.

    ``effective_baseline_co2e`` is the baseline reference after any
    routed-category reroute (see _effective_baseline_co2e). When a component was
    rerouted (e.g. innervägg→kakel by a directive), the LLM MUST reason about
    savings against the rerouted baseline — otherwise its "−45% CO2e" reasoning
    is computed against the wrong (stale) material and the report text
    contradicts the actual savings math.
    """
    client = get_client()

    # Baseline the LLM reasons against: the rerouted value when it differs from
    # the stored one, else the stored baseline.
    rerouted = (
        effective_baseline_co2e is not None
        and effective_baseline_co2e != bl_comp.co2e_kg
    )
    baseline_for_prompt = effective_baseline_co2e if rerouted else bl_comp.co2e_kg

    baseline_source = (getattr(bl_comp, "source", "") or "").lower()
    if rerouted:
        # A rerouted baseline is the routed category's EPD typvärde, regardless
        # of what the original (wrong-material) baseline source was.
        baseline_label = "EPD-typvärde (kategori-aggregat)"
    elif "uppskattning" in baseline_source:
        baseline_label = "uppskattning"
    elif "epd-typvärde" in baseline_source or "epd-medel" in baseline_source or "epd-median" in baseline_source:
        baseline_label = "EPD-typvärde (kategori-aggregat)"
    else:
        baseline_label = "Boverket Typical"
    prompt = f"""Komponent: {proj_comp.name}
Antal: {proj_comp.quantity} {proj_comp.unit}
Baslinje CO2e: {baseline_for_prompt} kg ({baseline_label})
Baslinje kostnad: {bl_comp.cost_sek} SEK

Föreslå 2-4 relevanta alternativ från EPD-listan nedan. Inkludera hela spannet av CO2e-värden — användaren optimerar totalen över hela projektet, inte per komponent, så ett alternativ som ligger något över baslinjen kan vara värt att visa om det möter behoven bättre. Rangordna med lägst CO2e först. I reasoning: ange explicit hur alternativet jämför mot baslinjen (t.ex. "−45% CO2e" eller "+12% CO2e — men kortare leveranskedja och tystare drift").
"""

    # Project-level needs (user-approved) — overarching framing for the whole
    # selection. Per-component usage_context below is the targeted derivation
    # of these for this specific component.
    inferred = getattr(needs_analysis, "inferred", "") if needs_analysis else ""
    if inferred:
        prompt += f"""
PROJEKTETS BEHOV (godkänt av användaren):
{inferred}

→ Detta är överordnad kontext. Föreslå alternativ från EPD-listan som vanligt och flagga i reasoning hur varje alternativ möter eller utmanar dessa behov. Utelämna ENDAST om alternativet är uppenbart fel byggdel för funktionen (t.ex. utomhusgolv för innerentré). I tveksamma fall: föreslå med tydlig caveat i reasoning — användaren har sista ordet, inte agenten.
"""

    usage_context = getattr(proj_comp, "usage_context", "")
    if usage_context:
        prompt += f"""
ANVÄNDNINGSKONTEXT FÖR DENNA KOMPONENT (funktionella krav från intake):
{usage_context}

→ Använd kontexten för att resonera om hur varje alternativ möter kraven. Föreslå alternativ från EPD-listan även om någon egenskap är osäker — flagga osäkerheten i reasoning (t.ex. "lägre CO2e men kräver halksäkringsbehandling"). Utelämna bara om alternativet är uppenbart fel byggdel.
"""

    if epds:
        prompt += f"""
TILLGÄNGLIGA EPD:er FÖR DENNA KATEGORI ({len(epds)} st):
{_format_epd_list(epds)}

Välj de 2-4 bästa alternativen från listan ovan. Beräkna total CO2e baserat på EPD-värdet × {proj_comp.quantity} {proj_comp.unit}.
Om EPD-enheten inte matchar projektenheten (t.ex. EPD i kg men projektet i m2), gör en rimlig omräkning.
Prioritera svenska/nordiska produkter.
"""
    else:
        prompt += """
Inga EPD:er tillgängliga för denna kategori. Returnera en tom array [].
"""

    if user_feedback:
        prompt += f"\nAnvändarens önskemål: {user_feedback}\n"

    prompt += "\nSvara med JSON-array."

    try:
        response = call_model(
            client,
            model=DEFAULT_MODEL,
            max_tokens=REASONING_MAX_TOKENS,
            effort=EFFORT_HIGH,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        text = extract_text(response)

        data = extract_json_value(text, what="alternativsökningen")
        if isinstance(data, dict):
            data = data.get("alternatives", [data])
        if not isinstance(data, list):
            data = [data]

        results = []
        for item in data:
            # Skip any LLM-fabricated reuse — reuse only comes from Palats
            if item.get("alternative_type") == "reuse":
                logger.info("Filtered LLM-fabricated reuse '%s'", item.get("name"))
                continue

            source = item.get("source", "")
            # Tag source based on whether it references an EPD
            if not source.startswith("["):
                if "epd" in source.lower() or "environdec" in source.lower():
                    source = f"[EPD] {source}"
                else:
                    source = f"[Uppskattning] {source}"

            # Which GWP indicator this rests on is a fact about the catalog, not
            # something to hope the model repeats, so match it back by name.
            alt_name = item.get("name", "Okänt alternativ")
            matched = match_epd_by_name(alt_name, epds)
            gwp_basis = (matched or {}).get("gwp_basis", "") if matched else ""
            if gwp_basis == "ghg":
                # Rides in `source` as well as its own field: source is what the
                # report's component table prints, so the basis reaches the
                # document without a separate plumbing path.
                source = f"{source} (GWP-GHG)"

            results.append(Alternative(
                name=alt_name,
                co2e_kg=item.get("co2e_kg", baseline_for_prompt),
                cost_sek=item.get("cost_sek", 0),
                source=source,
                reasoning=item.get("reasoning", ""),
                alternative_type="climate_optimized",
                gwp_basis=gwp_basis,
            ))

        return results
    except Exception:
        logger.warning("Failed to parse alternatives for %s", proj_comp.name, exc_info=True)
        return []


COMMENTARY_PROMPT = """Du är AIda — en byggnadsexpert som hjälper förvaltare och byggledare att hitta renoveringslösningar med kraftigt minskad klimatpåverkan.

Du har just tagit fram alternativ för ett ombyggnadsprojekt. Skriv en kort kommentar om förslagen. Kommentaren ska:
- Lyfta de mest intressanta alternativen och varför de sticker ut
- Nämna om det finns återbruksmöjligheter och vad det innebär
- Peka på eventuella avvägningar (t.ex. lägre CO2 men högre installerat pris, eller enklare montering som sänker totalkostnaden)
- Resonera kort om hur alternativen uppfyller praktiska behov (ljudmiljö, underhåll, inomhusklimat etc)
- Ge ett helhetsintryck av besparingspotentialen

Format:
- Dela upp texten så den är lätt att skumma: korta stycken, punktlistor eller en kombination.
- Max 5-6 meningar/punkter totalt. Korta formuleringar.
- Konkret och direkt, med materialnamn och siffror.
- Skriv som en kunnig byggnadsexpert som pratar med en projektledare.
- Skriv på svenska."""


def _generate_commentary(
    project: Project,
    baseline: Baseline,
    result: AlternativesResult,
) -> str:
    """Generate a natural language commentary about the alternatives found."""
    client = get_client()

    # Map component_id -> usage_context so commentary can reason about
    # whether alternatives actually meet the functional requirements.
    usage_by_id = {
        c.id: getattr(c, "usage_context", "") for c in project.components
    }

    summary_lines = []
    for comp in result.components:
        bl_co2 = comp.baseline_co2e_kg
        bl_cost = comp.baseline_cost_sek
        summary_lines.append(f"\n{comp.component_name} (baslinje: {bl_co2:.0f} kg CO2e, {bl_cost:.0f} SEK):")
        usage = usage_by_id.get(comp.component_id, "")
        if usage:
            summary_lines.append(f"  Användning: {usage}")
        for alt in comp.alternatives:
            # Convention (matches the per-component prompt): minus = reduction.
            # pct = (alt - baseline)/baseline, so a 45% saving renders as "-45%".
            pct = ((alt.co2e_kg - bl_co2) / bl_co2 * 100) if bl_co2 > 0 else 0
            cost_str = f"{alt.cost_sek:.0f} SEK" if alt.cost_sek > 0 else "Pris ej tillgängligt"
            summary_lines.append(
                f"  - {alt.name} ({alt.alternative_type}): {alt.co2e_kg:.0f} kg CO2e, "
                f"{cost_str} ({pct:+.0f}% CO2e) | {alt.source}"
            )

    needs_block = ""
    inferred = getattr(project.needs_analysis, "inferred", "") if project.needs_analysis else ""
    if inferred:
        needs_block = f"""

Projektets behov (användargodkänt):
{inferred}
"""

    prompt = f"""Projekt: {project.building_type}, {project.area_bta} m2{needs_block}

Alternativ som hittats:
{''.join(summary_lines)}

Skriv din kommentar."""

    try:
        response = call_model(
            client,
            model=DEFAULT_MODEL,
            max_tokens=REASONING_MAX_TOKENS,
            effort=EFFORT_MEDIUM,
            system=COMMENTARY_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return extract_text(response).strip()
    except Exception:
        return ""


def main():
    """CLI entry point for alternatives."""
    if len(sys.argv) < 5 or sys.argv[1] != "--project" or sys.argv[3] != "--baseline":
        print("Usage: python -m aida.agents.alternatives --project <project.json> --baseline <baseline.json>", file=sys.stderr)
        sys.exit(1)

    project_path = sys.argv[2]
    baseline_path = sys.argv[4]

    print("Steg 1/2: Läser projekt och baslinje...", file=sys.stderr)
    project = Project.from_json_file(project_path)
    baseline = Baseline.from_json_file(baseline_path)

    print(f"Steg 2/2: Söker alternativ för {len(project.components)} komponenter...", file=sys.stderr)
    result = find_alternatives(project, baseline)
    print(result.to_json())


if __name__ == "__main__":
    main()
