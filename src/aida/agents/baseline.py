"""Baseline agent: calculates baseline (conventional standard materials) per component."""

from __future__ import annotations

import json
import sys

from aida.api_client import (
    DEFAULT_MODEL,
    THINKING_DEEP,
    extract_text,
    get_client,
    thinking_config,
)
from aida.data.climate_data import REASONING, normalize_component_name
from aida.data.climate_provider import ClimateProvider
from aida.data.price_validation import validate_total_price
from aida.models import Baseline, BaselineResult, Project


def _validate_baseline_prices(results: list[BaselineResult], components: list) -> list[BaselineResult]:
    """Validate prices on baseline results and annotate descriptions."""
    comp_map = {c.id: c for c in components}
    for r in results:
        comp = comp_map.get(r.component_id)
        quantity = comp.quantity if comp else 0
        category = normalize_component_name(r.component_name)
        is_estimate = "uppskattning" in (r.cost_source or "").lower()

        if r.cost_sek <= 0:
            r.cost_sek = 0
            if "pris ej tillgängligt" not in r.description.lower():
                r.description = r.description.rstrip(". ") + ". Pris ej tillgängligt."
            continue

        _cost, note = validate_total_price(
            r.cost_sek, quantity, category, is_estimate=is_estimate,
        )
        if note and note.lower() not in r.description.lower():
            r.description = r.description.rstrip(". ") + f". {note}."
    return results

SYSTEM_PROMPT = """Du är AIda:s baslinjeberäknare — en byggnadsexpert som beräknar baslinjen för klimatpåverkan.

Baslinjen representerar standardfallet enligt NollCO2-metoden: vad det kostar klimatmässigt om projektet använder konventionella material utan särskild klimathänsyn. Det är referenspunkten som klimatsmartare alternativ jämförs mot. Samma metod som NollCO2 använder.

Du får komponenter där klimatdata redan har hämtats från Boverkets klimatdatabas (Typical A1-A3).
Din uppgift är att uppskatta baslinjen för komponenter som SAKNAS i vår databas.

DATAKÄLLA:
Boverkets klimatdatabas med Typical-värden (A1-A3). Inte Conservative (+25%).
Om en komponent saknas: uppskatta baserat på materialkunskap, men markera tydligt som uppskattning.

PRISER:
Alla priser avser installerat pris (material + arbete) i SEK exklusive moms.

Ange alltid vilken datakälla du använt i "source"-fältet.

Svara med giltig JSON:
{
  "method": "NollCO2",
  "components": [
    {
      "component_id": "string",
      "component_name": "string",
      "co2e_kg": number,
      "cost_sek": number,
      "method": "NollCO2",
      "description": "Beskrivning av baslinjeberäkningen",
      "source": "Datakälla (EPD-referens, Boverket, eller uppskattning)"
    }
  ]
}
"""


def _friendly_source(climate) -> str:
    """Human-readable source label for climate data."""
    src = climate.source_layer if hasattr(climate, 'source_layer') else ""
    if src == "boverket" or "Boverket" in (climate.source or ""):
        return "Boverkets klimatdatabas"
    if src == "environdec" or "Environdec" in (climate.source or ""):
        return "EPD (Environdec)"
    if src == "local":
        raw = climate.source or ""
        if "Boverket" in raw:
            return "Boverkets typvärde"
        return "Typiskt värde (uppskattning)"
    return "Uppskattning"


def _friendly_cost_source(climate) -> str:
    """Human-readable source label for price data."""
    if climate.cost_per_unit <= 0:
        return ""
    return "Webbsökning (AI)"


def calculate_baseline(project: Project) -> Baseline:
    """Calculate NollCO2 baseline for each component.

    Uses ClimateProvider (Boverket → local → LLM fallback chain).
    Optimized: climate lookups first, then batched price enrichment.
    """
    provider = ClimateProvider()
    provider.ensure_synced()

    # Phase 1: Climate data lookups (no pricing — fast)
    climate_hits: list[tuple[Component, ClimateResult]] = []
    unknown_components = []

    for comp in project.components:
        climate = provider.lookup_without_price(comp.name)
        if climate:
            climate_hits.append((comp, climate))
        else:
            unknown_components.append(comp)

    # Phase 2: Batch price enrichment (single LLM call instead of N calls)
    from aida.data.pricing_provider import lookup_prices_batch

    # Batch price enrichment for any component without a web-searched installed price.
    # Local/hardcoded prices are material-only — we need installed prices (material + labor).
    products_needing_prices = [
        (comp.name, climate.unit)
        for comp, climate in climate_hits
        if not _is_price_cached(provider, comp.name)
    ]

    batch_prices: dict[str, tuple[float, str, str]] = {}
    if products_needing_prices:
        batch_prices = lookup_prices_batch(products_needing_prices)
        # Update cache with fetched prices
        for product_key, (price, _unit, _source) in batch_prices.items():
            provider._cache.update_cost(product_key, price)

    # Phase 3: Build results
    results = []
    for comp, climate in climate_hits:
        cost_per_unit = climate.cost_per_unit
        cost_source = _friendly_cost_source(climate)

        # Prefer web-searched installed price over local material-only price
        batch_result = batch_prices.get(comp.name.lower())
        if batch_result:
            cost_per_unit = batch_result[0]
            cost_source = "Webbsökning (AI)"

        co2e = climate.co2e_per_unit * comp.quantity
        cost = cost_per_unit * comp.quantity

        results.append(BaselineResult(
            component_id=comp.id,
            component_name=comp.name,
            co2e_kg=round(co2e, 1),
            cost_sek=round(cost),
            method="NollCO2",
            description=f"Baslinje (NollCO2): {climate.name}, {climate.co2e_per_unit} kg CO2e/{climate.unit} x {comp.quantity} {comp.unit}. {REASONING['conventional']}",
            source=_friendly_source(climate),
            cost_source=cost_source,
        ))

    # Phase 4: LLM fallback for components not in any database
    if unknown_components:
        llm_results = _estimate_unknown_components(project, unknown_components)
        results.extend(llm_results)

    results = _validate_baseline_prices(results, project.components)
    return Baseline(components=results)


def _is_price_cached(provider: ClimateProvider, product_name: str) -> bool:
    """Check if a product already has a cached enriched price."""
    cached = provider._cache.get(product_name.lower().strip())
    return bool(cached and cached.price_enriched and cached.cost_per_unit > 0)


def _estimate_unknown_components(project: Project, components: list) -> list[BaselineResult]:
    """Use LLM to estimate baseline for components not in our database."""
    client = get_client()

    comp_list = "\n".join(
        f"- {c.name}: {c.quantity} {c.unit}"
        for c in components
    )

    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=2000 + THINKING_DEEP,
        thinking=thinking_config(THINKING_DEEP),
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""Projekt: {project.building_type}, {project.area_bta} m² BTA

Följande komponenter finns inte i Boverkets klimatdatabas. Uppskatta baslinjen (konventionellt standardmaterial):

{comp_list}

VIKTIGT:
- Baslinjen representerar konventionella standardmaterial, inte worst case.
- Använd Boverkets klimatdatabas som referens om du känner till typiska värden.
- Om du inte har specifika värden: uppskatta, men ange "Uppskattning" som source.
- Var ärlig om osäkerheten. Ange INTE specifika EPD-nummer eller databaskällor du inte är säker på.

Svara med JSON-array av objekt med: component_id, component_name, co2e_kg, cost_sek, method, description, source."""
        }],
    )

    text = extract_text(response)
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    data = json.loads(text.strip())
    if isinstance(data, dict) and "components" in data:
        data = data["components"]

    results = []
    for item in data:
        raw_source = item.get("source", "Generisk uppskattning")
        results.append(BaselineResult(
            component_id=item["component_id"],
            component_name=item["component_name"],
            co2e_kg=item["co2e_kg"],
            cost_sek=item["cost_sek"],
            method="NollCO2",
            description=item.get("description", "LLM-uppskattning (ej i standarddatabas)"),
            source="Uppskattning",
            cost_source="Uppskattning (AI)",
        ))

    return results


def main():
    """CLI entry point for baseline."""
    if len(sys.argv) < 3 or sys.argv[1] != "--project":
        print("Usage: python -m aida.agents.baseline --project <project.json>", file=sys.stderr)
        sys.exit(1)

    project_path = sys.argv[2]
    print("Steg 1/2: Läser projektbeskrivning...", file=sys.stderr)

    try:
        project = Project.from_json_file(project_path)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Fel: Kunde inte läsa projektfilen: {e}", file=sys.stderr)
        sys.exit(1)

    if not project.components:
        print("Fel: Projektet har inga komponenter.", file=sys.stderr)
        sys.exit(1)

    print(f"Steg 2/2: Beräknar baslinje (NollCO2) för {len(project.components)} komponenter...", file=sys.stderr)
    baseline = calculate_baseline(project)
    print(baseline.to_json())


if __name__ == "__main__":
    main()
