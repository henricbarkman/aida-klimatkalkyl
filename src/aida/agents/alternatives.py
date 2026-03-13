"""Alternatives agent: finds climate-optimized and reuse alternatives per component."""

from __future__ import annotations

import json
import os
import sys

from aida.api_client import get_client, DEFAULT_MODEL
from aida.models import (
    Project, Baseline, Alternative, ComponentAlternatives, AlternativesResult,
)
from aida.data.climate_data import (
    get_alternatives_for_component, normalize_component_name, REASONING,
)

SYSTEM_PROMPT = """Du är AIda:s alternativanalys-agent. Du hittar klimatsmartare alternativ till konventionella byggmaterial.

Du får en komponent och dess baslinjevärde. Föreslå 1-3 alternativ med lägre klimatpåverkan:
1. Återbruk (om möjligt) - material från Sola byggåterbruk, CCBuild, eller liknande
2. Klimatoptimerat nyinköp - nyproducerat material med lägre CO2e

För varje alternativ, ange:
- name: Beskrivande namn
- co2e_kg: Total CO2e i kg
- cost_sek: Uppskattad kostnad i SEK
- source: Datakälla (EPD, Boverket, etc.)
- reasoning: Varför detta alternativ har lägre klimatpåverkan
- alternative_type: "reuse" eller "climate_optimized"

Om inga återbruksalternativ finns, säg det explicit.
Om du är osäker på en siffra, ge ett intervall och notera osäkerheten.

Svara med giltig JSON-array av alternativ-objekt.
"""


def find_alternatives(project: Project, baseline: Baseline) -> AlternativesResult:
    """Find climate-optimized alternatives for each component."""
    component_results = []

    for bl_comp in baseline.components:
        # Find matching project component
        proj_comp = next(
            (c for c in project.components if c.id == bl_comp.component_id),
            None,
        )
        if not proj_comp:
            continue

        # Try local data first
        local_alts = get_alternatives_for_component(proj_comp.name)
        alternatives = []

        for mat in local_alts:
            co2e = mat.co2e_per_unit * proj_comp.quantity
            cost = mat.cost_per_unit * proj_comp.quantity
            reasoning = REASONING.get(mat.category, "")
            alternatives.append(Alternative(
                name=mat.name,
                co2e_kg=round(co2e, 1),
                cost_sek=round(cost),
                source=mat.source,
                reasoning=reasoning,
                alternative_type=mat.category,
            ))

        # If no local alternatives, use LLM
        if not alternatives:
            llm_alts = _estimate_alternatives_llm(proj_comp, bl_comp)
            alternatives.extend(llm_alts)

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

    return AlternativesResult(components=component_results)


def _estimate_alternatives_llm(proj_comp, bl_comp) -> list[Alternative]:
    """Use LLM for components without local data."""
    client = get_client()

    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""Komponent: {proj_comp.name}
Antal: {proj_comp.quantity} {proj_comp.unit}
Baslinje CO2e: {bl_comp.co2e_kg} kg
Baslinje kostnad: {bl_comp.cost_sek} SEK

Föreslå klimatsmartare alternativ. Om återbruk inte är realistiskt, säg det explicit.
Svara med JSON-array."""
        }],
    )

    text = response.content[0].text
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
        results.append(Alternative(
            name=item.get("name", "Okänt alternativ"),
            co2e_kg=item.get("co2e_kg", bl_comp.co2e_kg),
            cost_sek=item.get("cost_sek", bl_comp.cost_sek),
            source=item.get("source", "LLM-uppskattning"),
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
