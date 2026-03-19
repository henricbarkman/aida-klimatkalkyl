"""Alternatives agent: finds climate-optimized and reuse alternatives per component."""

from __future__ import annotations

import json
import sys

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
)
from aida.models import (
    Alternative,
    AlternativesResult,
    Baseline,
    ComponentAlternatives,
    Project,
)

SYSTEM_PROMPT = """Du är AIda:s alternativanalys-agent. Du hittar klimatsmartare alternativ till konventionella byggmaterial.

Du får en komponent och dess baslinjevärde. Föreslå minst 3 alternativ med lägre klimatpåverkan:
1. Återbruk (om möjligt) - material från Sola byggåterbruk, CCBuild, eller liknande. Notera att live-sökning i marknadsplatser inte är tillgängligt ännu, så ange rimliga uppskattningar.
2. Klimatoptimerat nyinköp - minst 2 olika nyproducerade material med lägre CO2e. Ge konkreta produktnamn/materialtyper, inte generiska beskrivningar.

DATAKÄLLOR (i prioritetsordning):
1. EPD:er (Environmental Product Declarations) från environdec.com, EPD Norge, eller produktspecifika EPD:er. Dessa är alltid förstahandskälla för CO2e-värden.
2. Boverkets klimatdatabas — använd om ingen specifik EPD finns.
3. Egen uppskattning baserad på materialkunskap — sista utväg, notera att det är en uppskattning.

För varje alternativ, ange:
- name: Beskrivande namn
- co2e_kg: Total CO2e i kg
- cost_sek: Uppskattad kostnad i SEK
- source: Datakälla (EPD-referens med nummer/produktnamn om möjligt, annars Boverket)
- reasoning: Varför detta alternativ har lägre klimatpåverkan
- alternative_type: "reuse" eller "climate_optimized"

Om inga återbruksalternativ finns, säg det explicit.
Om du är osäker på en siffra, ge ett intervall och notera osäkerheten.

Svara med giltig JSON-array av alternativ-objekt.
"""


def find_alternatives(
    project: Project,
    baseline: Baseline,
    user_feedback: str | None = None,
) -> AlternativesResult:
    """Find climate-optimized alternatives for each component.

    If user_feedback is provided, the LLM is called for all components
    to incorporate the user's specific requests (e.g. more material options).
    """
    component_results = []
    provider = ClimateProvider()

    for bl_comp in baseline.components:
        # Find matching project component
        proj_comp = next(
            (c for c in project.components if c.id == bl_comp.component_id),
            None,
        )
        if not proj_comp:
            continue

        alternatives = []

        # 1. Environdec EPD:er — produktspecifika alternativ med verifierade värden
        epd_alts = _find_environdec_alternatives(provider, proj_comp, bl_comp)
        alternatives.extend(epd_alts)

        # 2. Lokal data (återbruk + klimatoptimerat)
        local_alts = get_alternatives_for_component(proj_comp.name)
        existing_names = {a.name.lower() for a in alternatives}
        for mat in local_alts:
            if mat.name.lower() in existing_names:
                continue
            co2e = mat.co2e_per_unit * proj_comp.quantity
            cost = mat.cost_per_unit * proj_comp.quantity
            reasoning = REASONING.get(mat.category, "")
            confidence_tag = "Verifierad" if mat.confidence == "high" else "Estimat"
            alternatives.append(Alternative(
                name=mat.name,
                co2e_kg=round(co2e, 1),
                cost_sek=round(cost),
                source=f"[{confidence_tag}] {mat.source}",
                reasoning=reasoning,
                alternative_type=mat.category,
            ))

        # 3. LLM om inga alternativ hittades eller om användaren bad om fler
        if not alternatives or user_feedback:
            llm_alts = _estimate_alternatives_llm(proj_comp, bl_comp, user_feedback)
            existing_names = {a.name.lower() for a in alternatives}
            for alt in llm_alts:
                if alt.name.lower() not in existing_names:
                    alternatives.append(alt)

        # If still no alternatives (shouldn't happen with LLM), note it
        if not alternatives:
            alternatives.append(Alternative(
                name=f"Inga alternativ hittades för {proj_comp.name}",
                co2e_kg=bl_comp.co2e_kg,
                cost_sek=bl_comp.cost_sek,
                source="N/A",
                reasoning="Inga återbruksalternativ hittades. Inga klimatoptimerade alternativ identifierade.",
                alternative_type="baseline",
            ))

        component_results.append(ComponentAlternatives(
            component_id=bl_comp.component_id,
            component_name=bl_comp.component_name,
            baseline_co2e_kg=bl_comp.co2e_kg,
            baseline_cost_sek=bl_comp.cost_sek,
            alternatives=alternatives,
        ))

    result = AlternativesResult(components=component_results)
    result.commentary = _generate_commentary(project, baseline, result)
    return result


COMMENTARY_PROMPT = """Du är AIda. Du har just tagit fram alternativ för ett ombyggnadsprojekt.

Skriv en kort kommentar (3-6 meningar) om förslagen. Kommentaren ska:
- Lyfta de mest intressanta alternativen och varför de sticker ut
- Nämna om det finns återbruksmöjligheter och vad det innebär
- Peka på eventuella avvägningar (t.ex. lägre CO2 men högre kostnad, eller tvärtom)
- Ge ett helhetsintryck av besparingspotentialen

Skriv på svenska. Var konkret, inte generisk. Referera till faktiska materialnamn och siffror från datan.
Skriv som en kunnig rådgivare som pratar med en projektledare.
Inte som en lista, utan som en sammanhängande kommentar."""


def _generate_commentary(
    project: Project,
    baseline: Baseline,
    result: AlternativesResult,
) -> str:
    """Generate a natural language commentary about the alternatives found."""
    client = get_client()

    # Build a summary of the alternatives for the LLM
    summary_lines = []
    for comp in result.components:
        bl_co2 = comp.baseline_co2e_kg
        bl_cost = comp.baseline_cost_sek
        summary_lines.append(f"\n{comp.component_name} (baslinje: {bl_co2:.0f} kg CO2e, {bl_cost:.0f} SEK):")
        for alt in comp.alternatives:
            pct = ((bl_co2 - alt.co2e_kg) / bl_co2 * 100) if bl_co2 > 0 else 0
            summary_lines.append(
                f"  - {alt.name} ({alt.alternative_type}): {alt.co2e_kg:.0f} kg CO2e, "
                f"{alt.cost_sek:.0f} SEK ({pct:+.0f}% CO2e) | {alt.source}"
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


def _find_environdec_alternatives(
    provider: ClimateProvider,
    proj_comp,
    bl_comp,
    max_results: int = 3,
) -> list[Alternative]:
    """Search Environdec for product-specific EPD alternatives with lower CO2e than baseline."""
    from aida.data.climate_data import normalize_component_name

    comp_key = normalize_component_name(proj_comp.name)
    if not comp_key:
        return []

    client = provider._get_environdec()
    # Search for products matching this component type
    matches = client.search_index(
        proj_comp.name,
        component_hint=comp_key,
        max_results=10,
    )

    if not matches:
        return []

    alternatives = []
    baseline_per_unit = bl_comp.co2e_kg / proj_comp.quantity if proj_comp.quantity > 0 else 0

    for match in matches:
        if len(alternatives) >= max_results:
            break

        detail = client.fetch_epd_detail(match.uuid, match.version)
        if detail is None:
            continue

        gwp = detail.gwp_fossil_a1a3 or detail.gwp_total_a1a3
        if gwp is None or gwp <= 0:
            continue

        # Convert to functional unit via cache entry
        entry = client.epd_to_cache_entry(detail, proj_comp.name)
        from aida.data.climate_provider import ClimateResult
        cr = ClimateResult(
            name=entry.name, co2e_per_unit=entry.co2e_per_unit,
            cost_per_unit=0.0, unit=entry.unit, source=entry.source,
            confidence=entry.confidence, source_layer="environdec",
        )
        converted = provider._maybe_convert_units(cr, comp_key, entry.extra_json)
        co2e_per_unit = converted.co2e_per_unit
        co2e_total = co2e_per_unit * proj_comp.quantity

        # Only include if it's better than baseline
        if co2e_total >= bl_comp.co2e_kg:
            continue

        reg_label = detail.reg_no or detail.uuid[:8]
        alternatives.append(Alternative(
            name=f"{detail.name} ({detail.owner.strip()})" if detail.owner.strip() else detail.name,
            co2e_kg=round(co2e_total, 1),
            cost_sek=0,  # EPD:er har inte kostnad
            source=f"[EPD] Environdec {reg_label}",
            reasoning=f"{REASONING['climate_optimized']} GWP-fossil A1-A3: {gwp:.1f} kg CO2e/{detail.declared_unit} (Environdec EPD).",
            alternative_type="climate_optimized",
        ))

    return alternatives


def _estimate_alternatives_llm(proj_comp, bl_comp, user_feedback: str | None = None) -> list[Alternative]:
    """Use LLM for components without local data, or when user requests more options."""
    client = get_client()

    prompt = f"""Komponent: {proj_comp.name}
Antal: {proj_comp.quantity} {proj_comp.unit}
Baslinje CO2e: {bl_comp.co2e_kg} kg
Baslinje kostnad: {bl_comp.cost_sek} SEK

Föreslå klimatsmartare alternativ. Om återbruk inte är realistiskt, säg det explicit."""

    if user_feedback:
        prompt += f"\n\nAnvändarens önskemål: {user_feedback}"

    prompt += """

VIKTIGT om datakällor:
- Använd EPD-värden (environdec.com, EPD Norge) om du har tillförlitlig kunskap om dem.
- Annars, använd Boverkets klimatdatabas.
- Om du inte har specifika värden: uppskatta, men ange "Uppskattning baserad på generisk materialdata" som source.
- Var ärlig om osäkerheten. Ange INTE specifika EPD-nummer du inte är säker på.

Svara med JSON-array."""

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
        raw_source = item.get("source", "Generisk uppskattning")
        results.append(Alternative(
            name=item.get("name", "Okänt alternativ"),
            co2e_kg=item.get("co2e_kg", bl_comp.co2e_kg),
            cost_sek=item.get("cost_sek", bl_comp.cost_sek),
            source=f"[Uppskattning] {raw_source}",
            reasoning=item.get("reasoning", ""),
            alternative_type=item.get("alternative_type", "climate_optimized"),
        ))

    return results


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
