"""Baseline agent: calculates baseline (conventional standard materials) per component."""

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
from aida.data.climate_data import REASONING
from aida.models import Baseline, BaselineResult, Project

SYSTEM_PROMPT = """Du är AIda:s baslinjeberäknare. Du beräknar baslinjen för klimatpåverkan — vad konventionella standardmaterial kostar klimatmässigt.

Baslinjen representerar standardfallet: vad det kostar klimatmässigt om projektet använder konventionella material utan särskild klimathänsyn. Det är referenspunkten som klimatsmartare alternativ jämförs mot.

Du får komponenter där klimatdata redan har hämtats från Boverkets klimatdatabas (Typical A1-A3).
Din uppgift är att uppskatta baslinjen för komponenter som SAKNAS i vår databas.

DATAKÄLLA:
Boverkets klimatdatabas med Typical-värden (A1-A3). Inte Conservative (+25%).
Om en komponent saknas: uppskatta baserat på materialkunskap, men markera tydligt som uppskattning.

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


def calculate_baseline(project: Project) -> Baseline:
    """Calculate NollCO2 baseline for each component.

    Uses ClimateProvider (Boverket → local → LLM fallback chain).
    """
    provider = ClimateProvider()
    results = []
    unknown_components = []

    for comp in project.components:
        climate = provider.lookup(comp.name)
        if climate:
            co2e = climate.co2e_per_unit * comp.quantity
            cost = climate.cost_per_unit * comp.quantity
            results.append(BaselineResult(
                component_id=comp.id,
                component_name=comp.name,
                co2e_kg=round(co2e, 1),
                cost_sek=round(cost),
                method="NollCO2",
                description=f"Baslinje (NollCO2): {climate.name}, {climate.co2e_per_unit} kg CO2e/{climate.unit} x {comp.quantity} {comp.unit}. {REASONING['conventional']}",
                source=f"[{climate.confidence.title()}] {climate.source}",
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
        max_tokens=2000 + THINKING_STANDARD,
        thinking=thinking_config(THINKING_STANDARD),
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
            source=f"[Uppskattning] {raw_source}",
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
