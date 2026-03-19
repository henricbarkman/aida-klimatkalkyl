"""Alternatives agent: finds climate-optimized and reuse alternatives per component.

Uses pre-categorized Environdec EPD data to give the LLM real product-specific
GWP values. The LLM acts as expert, selecting and reasoning about the best
alternatives from the EPD data it receives.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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

SYSTEM_PROMPT = """Du är AIda:s alternativanalys-agent. Du hittar klimatsmartare alternativ till konventionella byggmaterial.

Du får:
1. En komponent med baslinjevärde (Boverket Typical, konventionellt standardmaterial)
2. En lista med FAKTISKA EPD:er (Environmental Product Declarations) från Environdec-databasen, med verifierade GWP-värden

Din uppgift:
1. Analysera EPD-listan och välj de 2-4 mest relevanta alternativen med lägre klimatpåverkan
2. Föreslå återbruk om det är möjligt för komponenttypen
3. Beräkna total CO2e baserat på EPD-värdet × antal enheter
4. Resonera om varför alternativet är bättre

VIKTIGT:
- Använd GWP-värdena från EPD-listan — de är verifierade, inte uppskattningar
- Ange EPD-registreringsnummer i source-fältet
- Prioritera svenska/nordiska produkter (SE, NORD, RER)
- Om EPD-värdet är i en annan enhet (kg) än projektets enhet (m2, st), gör en rimlig omräkning och notera det
- Om ingen EPD i listan passar, säg det och ge en egen uppskattning med tydlig markering

Svara med giltig JSON-array:
[
  {
    "name": "Produktnamn (Tillverkare)",
    "co2e_kg": <total CO2e i kg>,
    "cost_sek": <uppskattad kostnad i SEK, 0 om okänt>,
    "source": "[EPD] Environdec <registreringsnummer>",
    "reasoning": "Varför detta alternativ har lägre klimatpåverkan",
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
        lines.append(
            f"- {epd['name']} | {epd.get('owner', '?')} | "
            f"GWP A1-A3: {epd['gwp_a1a3']} kg CO2e/{epd['unit']} | "
            f"Geo: {epd.get('geo', '?')}{reg_str}"
        )
    return "\n".join(lines)


def find_alternatives(
    project: Project,
    baseline: Baseline,
    user_feedback: str | None = None,
) -> AlternativesResult:
    """Find climate-optimized alternatives for each component.

    Strategy:
    1. Load pre-categorized EPD data from Environdec
    2. For each component, give the LLM the relevant EPDs + baseline
    3. LLM reasons about best alternatives
    4. Supplement with local data (reuse options)
    """
    epd_data = _load_epd_alternatives()
    component_results = []

    for bl_comp in baseline.components:
        proj_comp = next(
            (c for c in project.components if c.id == bl_comp.component_id),
            None,
        )
        if not proj_comp:
            continue

        comp_key = normalize_component_name(proj_comp.name)
        epds_for_category = epd_data.get(comp_key, [])

        # Get LLM alternatives using real EPD data
        alternatives = _find_alternatives_with_epds(
            proj_comp, bl_comp, epds_for_category, user_feedback
        )

        # Supplement with local reuse data
        local_alts = get_alternatives_for_component(proj_comp.name)
        existing_names = {a.name.lower() for a in alternatives}
        for mat in local_alts:
            if mat.name.lower() in existing_names:
                continue
            if mat.category != "reuse":
                continue  # EPD data covers climate_optimized better
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
        return []


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
