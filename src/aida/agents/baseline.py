"""Baseline agent: calculates NollCO2 baseline (worst-case new production) per component."""

from __future__ import annotations

import json
import os
import sys

from aida.api_client import get_client, DEFAULT_MODEL
from aida.models import Project, Baseline, BaselineResult
from aida.data.climate_data import get_baseline_for_component, normalize_component_name, BASELINE_DATA, REASONING

SYSTEM_PROMPT = """Du är AIda:s baslinjeberäknare. Du beräknar klimatpåverkan enligt NollCO2-metodens baslinjeprincip.

NollCO2-metoden: Baslinjen representerar ett scenario där alla material köps nytt utan klimathänsyn.
Detta ger det "värsta fallet" som jämförelse för klimatsmarta alternativ.

Du får en projektbeskrivning med komponenter och en uppsättning klimatdata.
Din uppgift:
1. Matcha varje komponent mot klimatdata
2. Beräkna CO2e (kg) = klimatdata per enhet × antal
3. Beräkna kostnad (SEK) = kostnad per enhet × antal
4. Välj det alternativ med HÖGST CO2e (worst-case, per NollCO2-principen)

Om en komponent inte hittas i datan, uppskatta baserat på liknande material och notera osäkerheten.

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
      "source": "Datakälla"
    }
  ]
}
"""


def calculate_baseline(project: Project) -> Baseline:
    """Calculate NollCO2 baseline for each component.

    Uses local climate data first, falls back to LLM for unknown components.
    """
    results = []
    unknown_components = []

    for comp in project.components:
        material = get_baseline_for_component(comp.name)
        if material:
            co2e = material.co2e_per_unit * comp.quantity
            cost = material.cost_per_unit * comp.quantity
            results.append(BaselineResult(
                component_id=comp.id,
                component_name=comp.name,
                co2e_kg=round(co2e, 1),
                cost_sek=round(cost),
                method="NollCO2",
                description=f"Baslinje (NollCO2): {material.name}, {material.co2e_per_unit} kg CO2e/{material.unit} × {comp.quantity} {comp.unit}. {REASONING['conventional']}",
                source=material.source,
            ))
        else:
            unknown_components.append(comp)

    if unknown_components:
        llm_results = _estimate_unknown_components(project, unknown_components)
        results.extend(llm_results)

    return Baseline(components=results)


def _estimate_unknown_components(project: Project, components: list) -> list[BaselineResult]:
    """Use LLM to estimate baseline for components not in our database."""
    client = get_client()

    comp_list = "\n".join(
        f"- {c.name}: {c.quantity} {c.unit}"
        for c in components
    )

    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""Projekt: {project.building_type}, {project.area_bta} m² BTA

Följande komponenter finns inte i vår standarddatabas. Uppskatta baslinje (NollCO2 worst-case):

{comp_list}

Använd dina kunskaper om byggmaterial och EPD-data. Var tydlig med att detta är uppskattningar.
Svara med JSON-array av objekt med: component_id, component_name, co2e_kg, cost_sek, method, description, source."""
        }],
    )

    text = response.content[0].text
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    data = json.loads(text.strip())
    if isinstance(data, dict) and "components" in data:
        data = data["components"]

    results = []
    for item in data:
        results.append(BaselineResult(
            component_id=item["component_id"],
            component_name=item["component_name"],
            co2e_kg=item["co2e_kg"],
            cost_sek=item["cost_sek"],
            method="NollCO2",
            description=item.get("description", "LLM-uppskattning (ej i standarddatabas)"),
            source=item.get("source", "Uppskattning baserad på generiska EPD-data"),
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
