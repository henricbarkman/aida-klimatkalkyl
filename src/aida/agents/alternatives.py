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
    THINKING_STANDARD,
    extract_text,
    get_client,
    thinking_config,
)
from aida.data.climate_data import (
    REASONING,
    get_alternatives_for_component,
    normalize_component_name,
)
from aida.models import (
    Alternative,
    AlternativesResult,
    Baseline,
    ComponentAlternatives,
    Project,
)

EPD_ALTERNATIVES_PATH = Path(__file__).parent.parent / "data" / "epd_alternatives.json"

SYSTEM_PROMPT = """Du är AIda:s alternativanalys-agent — en byggnadsexpert som hittar klimatsmartare alternativ till konventionella byggmaterial.

UPPDRAG:
Hjälpa förvaltare och byggledare att hitta renoveringslösningar som kraftigt minskar klimatpåverkan utan att ge avkall på praktiska behov. Varje procentenhet reduktion räknas.

Du får:
1. En komponent med baslinjevärde (Boverket Typical, konventionellt standardmaterial)
2. En lista med FAKTISKA EPD:er (Environmental Product Declarations) från Environdec-databasen, med verifierade GWP-värden

Din uppgift:
1. Analysera EPD-listan och välj de 2-4 mest relevanta alternativen med lägre klimatpåverkan
2. Föreslå återbruk om det är möjligt för komponenttypen
3. Beräkna total CO2e baserat på EPD-värdet × antal enheter
4. Resonera om varför alternativet är bättre — beskriv BÅDE klimatvinsten och hur det uppfyller praktiska behov

PRINCIPER FÖR ALTERNATIV:
- Alla alternativ ska ha lägre klimatpåverkan än baslinjen.
- Uttryckta behov är oförhandlingsbara — inget alternativ som inte uppfyller dem.
- Resonera om hur alternativen möter behov: både uttryckta och antagna (ljudmiljö, inomhusklimat, underhåll, estetik, arbetsmiljö vid installation).
- Presentera spridning i pris — det är användarens beslut att väga ekonomi mot klimat.
- Var innovativ — föreslå kombinationer som löser flera behov samtidigt.
- Förklara installationsaspekter som påverkar totalkostnaden (enklare montering kan kompensera dyrare material).

TEKNISKA REGLER:
- Använd GWP-värdena från EPD-listan — de är verifierade, inte uppskattningar
- Om ett omräknat värde visas (efter →), använd det omräknade värdet för beräkningar
- Ange EPD-registreringsnummer i source-fältet
- Prioritera svenska/nordiska produkter (SE, NORD, RER)
- Om EPD-värdet är i en annan enhet (kg) än projektets enhet (m2, st), gör en rimlig omräkning och notera det
- EPD:er kan komma från olika register (Environdec, EPD Norge, EPD Hub). Alla är verifierade
- Om ingen EPD i listan passar, säg det och ge en egen uppskattning med tydlig markering
- co2e_kg MÅSTE vara > 0 — alla byggmaterial har klimatpåverkan. Returnera aldrig 0.
- Föreslå KOMPLETTA system, inte enskilda komponenter. Om baslinjen är ett komplett taksystem (t.ex. betongpannor + underlag), ska alternativen också vara kompletta taksystem — inte bara enskilda membran, ångspärrar eller underlagsdukar.

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
    "alternative_type": "reuse" | "climate_optimized"
  }
]"""


def _load_epd_alternatives() -> dict[str, list[dict]]:
    """Load pre-categorized EPD alternatives, grouped by AIda category."""
    if not EPD_ALTERNATIVES_PATH.exists():
        return {}
    try:
        with open(EPD_ALTERNATIVES_PATH) as f:
            data = json.load(f)
        result: dict[str, list[dict]] = {}
        for epd in data:
            cat = epd.get("category", "")
            if cat:
                result.setdefault(cat, []).append(epd)
        return result
    except (json.JSONDecodeError, OSError):
        return {}


def _format_epd_list(epds: list[dict]) -> str:
    """Format EPD list for inclusion in prompt."""
    lines = []
    for epd in epds:
        reg = epd.get("reg_no", "")
        reg_str = f" ({reg})" if reg else ""

        # Show converted functional unit value when available
        gwp_str = f"GWP A1-A3: {epd['gwp_a1a3']} kg CO2e/{epd['unit']}"
        fu_gwp = epd.get("gwp_per_functional_unit")
        fu_unit = epd.get("functional_unit")
        if fu_gwp is not None and fu_unit:
            gwp_str += f" \u2192 {fu_gwp} kg CO2e/{fu_unit}"

        # Show GWP-total when it differs significantly (e.g. bio-based products)
        gwp_total = epd.get("gwp_total_a1a3")
        if gwp_total is not None and gwp_total != epd["gwp_a1a3"]:
            gwp_str += f" (GWP-total: {gwp_total})"

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
) -> list[Alternative]:
    """Filter out alternatives with data quality issues.

    Removes:
    - Alternatives with co2e_kg <= 0 (unrealistic for building materials)
    - Component-only products when the baseline is a complete system
    Flags:
    - Alternatives with cost_sek == 0 get "Pris ej tillgängligt"
    - LLM-estimated prices get "Approximerat pris"
    - Out-of-range prices get "Oväntat pris — verifiera"
    """
    from aida.data.price_validation import validate_total_price

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

        # C) Price validation
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


def _add_palats_reuse(
    alternatives: list[Alternative],
    component_name: str,
    quantity: float,
    palats_listings: list[dict],
) -> None:
    """Add matching Palats reuse listings as alternatives (in-place).

    Palats listings get minimal CO2e (transport/refurbishment only) and
    actual marketplace prices.
    """
    from aida.data.palats_client import (
        _DEFAULT_REUSE_CO2E,
        REUSE_CO2E_PER_UNIT,
        search_listings_for_component,
    )

    matched = search_listings_for_component(component_name, palats_listings)
    if not matched:
        return

    existing_names = {a.name.lower() for a in alternatives}
    category = normalize_component_name(component_name)
    co2e_per_unit = REUSE_CO2E_PER_UNIT.get(category, _DEFAULT_REUSE_CO2E)

    for listing in matched[:5]:  # Cap at 5 reuse listings per component
        if listing.title.lower() in existing_names:
            continue

        total_co2e = co2e_per_unit * quantity
        # Use listing price if available, otherwise 0 (will be enriched later)
        total_cost = listing.price * quantity if listing.price > 0 else 0

        price_note = f"Pris: {listing.price:.0f} SEK/{listing.unit}" if listing.price > 0 else ""
        url_note = f"Se annons: {listing.url}" if listing.url else ""
        detail_parts = [p for p in [price_note, url_note] if p]
        detail_str = " | ".join(detail_parts)

        reasoning = (
            "Återbruk via Palats (Karlstads kommuns interna marknadsplats) "
            "eliminerar nästan all tillverkningsrelaterad klimatpåverkan. "
            "Kvarvarande CO2e kommer främst från transport och eventuell renovering."
        )
        if detail_str:
            reasoning += f" {detail_str}"
        if listing.description:
            desc_preview = listing.description[:150]
            if len(listing.description) > 150:
                desc_preview += "..."
            reasoning += f" Beskrivning: {desc_preview}"

        alternatives.append(Alternative(
            name=f"{listing.title} (Palats återbruk)",
            co2e_kg=round(total_co2e, 1),
            cost_sek=round(total_cost),
            source=f"[Palats] palats.app/listing/{listing.id}",
            reasoning=reasoning,
            alternative_type="reuse",
        ))
        existing_names.add(listing.title.lower())


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
    from aida.data.palats_client import fetch_listings

    epd_data = _load_epd_alternatives()

    # Fetch Palats listings once for the entire analysis
    palats_listings = fetch_listings()
    has_palats = len(palats_listings) > 0
    if has_palats:
        logger.info("Palats: %d listings available for reuse matching", len(palats_listings))

    def _process_component(bl_comp):
        proj_comp = next(
            (c for c in project.components if c.id == bl_comp.component_id),
            None,
        )
        if not proj_comp:
            return None

        comp_key = normalize_component_name(proj_comp.name)
        epds_for_category = epd_data.get(comp_key, [])

        alternatives = _find_alternatives_with_epds(
            proj_comp, bl_comp, epds_for_category, user_feedback
        )

        # Validate data quality: filter zero CO2, component-only parts, flag prices
        alternatives = _validate_alternatives(
            alternatives, bl_comp.co2e_kg, proj_comp.name, proj_comp.quantity,
        )

        # Add live Palats reuse listings
        if has_palats:
            _add_palats_reuse(
                alternatives, proj_comp.name, proj_comp.quantity, palats_listings,
            )

        # Fallback: hardcoded reuse data (only if no Palats results for this component)
        palats_reuse_count = sum(
            1 for a in alternatives
            if a.alternative_type == "reuse" and "[Palats]" in a.source
        )
        if palats_reuse_count == 0:
            local_alts = get_alternatives_for_component(proj_comp.name)
            existing_names = {a.name.lower() for a in alternatives}
            for mat in local_alts:
                if mat.name.lower() in existing_names:
                    continue
                if mat.category != "reuse":
                    continue
                co2e = mat.co2e_per_unit * proj_comp.quantity
                cost = mat.cost_per_unit * proj_comp.quantity
                alternatives.append(Alternative(
                    name=mat.name,
                    co2e_kg=round(co2e, 1),
                    cost_sek=round(cost),
                    source=f"[Lokal data] {mat.source}",
                    reasoning=REASONING.get("reuse", ""),
                    alternative_type="reuse",
                ))

        if not alternatives:
            alternatives.append(Alternative(
                name=f"Inga alternativ hittades för {proj_comp.name}",
                co2e_kg=bl_comp.co2e_kg,
                cost_sek=bl_comp.cost_sek,
                source="N/A",
                reasoning="Inga alternativ identifierade.",
                alternative_type="baseline",
            ))

        return ComponentAlternatives(
            component_id=bl_comp.component_id,
            component_name=bl_comp.component_name,
            baseline_co2e_kg=bl_comp.co2e_kg,
            baseline_cost_sek=bl_comp.cost_sek,
            alternatives=alternatives,
        )

    # Run per-component LLM calls in parallel (I/O-bound)
    max_workers = min(len(baseline.components), 5)
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
    _enrich_alternative_prices(component_results)

    result = AlternativesResult(components=component_results)
    result.commentary = _generate_commentary(project, baseline, result)
    return result


def _enrich_alternative_prices(components: list[ComponentAlternatives]) -> None:
    """Batch web search for alternatives that have cost_sek == 0."""
    from aida.data.pricing_provider import lookup_prices_batch

    products_needing_prices: list[tuple[str, str]] = []
    alt_index: list[tuple[int, int]] = []  # (comp_idx, alt_idx) for mapping back

    for ci, comp in enumerate(components):
        for ai, alt in enumerate(comp.alternatives):
            if alt.cost_sek <= 0 and alt.alternative_type != "baseline":
                products_needing_prices.append((alt.name, ""))
                alt_index.append((ci, ai))

    if not products_needing_prices:
        return

    batch_prices = lookup_prices_batch(products_needing_prices)
    if not batch_prices:
        return

    for (ci, ai), (name, _unit) in zip(alt_index, products_needing_prices):
        price_result = batch_prices.get(name.lower())
        if price_result:
            price, unit, _source = price_result
            alt = components[ci].alternatives[ai]
            alt.cost_sek = round(price)
            # Clean up "Pris ej tillgängligt" note since we now have a price
            alt.reasoning = alt.reasoning.replace(". Pris ej tillgängligt.", "")
            alt.reasoning = alt.reasoning.replace("Pris ej tillgängligt.", "")
            logger.info(
                "Enriched price for alternative '%s': %d SEK",
                alt.name, alt.cost_sek,
            )


def _find_alternatives_with_epds(
    proj_comp,
    bl_comp,
    epds: list[dict],
    user_feedback: str | None = None,
) -> list[Alternative]:
    """Use LLM to select best alternatives from EPD data."""
    client = get_client()

    prompt = f"""Komponent: {proj_comp.name}
Antal: {proj_comp.quantity} {proj_comp.unit}
Baslinje CO2e: {bl_comp.co2e_kg} kg (Boverket Typical)
Baslinje kostnad: {bl_comp.cost_sek} SEK
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
Inga EPD:er tillgängliga för denna kategori. Ge din bästa uppskattning av klimatsmartare alternativ.
Ange tydligt att det är uppskattningar.
"""

    if user_feedback:
        prompt += f"\nAnvändarens önskemål: {user_feedback}\n"

    prompt += "\nSvara med JSON-array."

    try:
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=2000 + THINKING_STANDARD,
            thinking=thinking_config(THINKING_STANDARD),
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        text = extract_text(response)
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        data = json.loads(text.strip())
        if isinstance(data, dict):
            data = data.get("alternatives", [data])
        if not isinstance(data, list):
            data = [data]

        results = []
        for item in data:
            source = item.get("source", "")
            # Tag source based on whether it references an EPD
            if not source.startswith("["):
                if "epd" in source.lower() or "environdec" in source.lower():
                    source = f"[EPD] {source}"
                else:
                    source = f"[Uppskattning] {source}"

            results.append(Alternative(
                name=item.get("name", "Okänt alternativ"),
                co2e_kg=item.get("co2e_kg", bl_comp.co2e_kg),
                cost_sek=item.get("cost_sek", 0),
                source=source,
                reasoning=item.get("reasoning", ""),
                alternative_type=item.get("alternative_type", "climate_optimized"),
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

    summary_lines = []
    for comp in result.components:
        bl_co2 = comp.baseline_co2e_kg
        bl_cost = comp.baseline_cost_sek
        summary_lines.append(f"\n{comp.component_name} (baslinje: {bl_co2:.0f} kg CO2e, {bl_cost:.0f} SEK):")
        for alt in comp.alternatives:
            pct = ((bl_co2 - alt.co2e_kg) / bl_co2 * 100) if bl_co2 > 0 else 0
            cost_str = f"{alt.cost_sek:.0f} SEK" if alt.cost_sek > 0 else "Pris ej tillgängligt"
            summary_lines.append(
                f"  - {alt.name} ({alt.alternative_type}): {alt.co2e_kg:.0f} kg CO2e, "
                f"{cost_str} ({pct:+.0f}% CO2e) | {alt.source}"
            )

    prompt = f"""Projekt: {project.building_type}, {project.area_bta} m2

Alternativ som hittats:
{''.join(summary_lines)}

Skriv din kommentar."""

    try:
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=500 + THINKING_STANDARD,
            thinking=thinking_config(THINKING_STANDARD),
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
