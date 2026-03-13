"""Aggregate agent: computes totals from user selections."""

from __future__ import annotations

import json
import sys

from aida.models import Project, Selections, AggregateResult



def compute_aggregate(project: Project, selections: Selections) -> AggregateResult:
    """Compute aggregate totals from component selections."""
    total_co2e = 0.0
    total_cost = 0.0
    baseline_co2e = 0.0
    baseline_cost = 0.0
    component_details = []

    # Validate: all project components must have a selection
    project_ids = {c.id for c in project.components}
    selection_ids = {c.id for c in selections.components}
    missing = project_ids - selection_ids
    if missing:
        print(f"Varning: Komponenter saknar val: {missing}", file=sys.stderr)

    for sel in selections.components:
        alt = sel.selected_alternative
        alt_co2e = alt.get("co2e_kg", 0)
        alt_cost = alt.get("cost_sek", 0)

        total_co2e += alt_co2e
        total_cost += alt_cost
        baseline_co2e += sel.baseline_co2e_kg
        baseline_cost += sel.baseline_cost_sek

        component_details.append({
            "id": sel.id,
            "name": sel.name,
            "valt_alternativ": alt.get("name", ""),
            "co2e_kg": alt_co2e,
            "kostnad_sek": alt_cost,
            "baslinje_co2e_kg": sel.baseline_co2e_kg,
            "baslinje_kostnad_sek": sel.baseline_cost_sek,
            "co2e_besparing_kg": round(sel.baseline_co2e_kg - alt_co2e, 1),
            "källa": alt.get("source", ""),
        })

    return AggregateResult(
        total_co2e_kg=round(total_co2e, 1),
        total_cost_sek=round(total_cost),
        baseline_total_co2e_kg=round(baseline_co2e, 1),
        baseline_total_cost_sek=round(baseline_cost),
        co2e_savings_kg=round(baseline_co2e - total_co2e, 1),
        cost_difference_sek=round(total_cost - baseline_cost),
        components=component_details,
    )


def main():
    """CLI entry point for aggregate."""
    if len(sys.argv) < 5 or sys.argv[1] != "--project" or sys.argv[3] != "--selections":
        print("Usage: python -m aida.agents.aggregate --project <project.json> --selections <selections.json>", file=sys.stderr)
        sys.exit(1)

    project_path = sys.argv[2]
    selections_path = sys.argv[4]

    try:
        project = Project.from_json_file(project_path)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Fel: Kunde inte läsa projektfilen: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        selections = Selections.from_json_file(selections_path)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Fel: Kunde inte läsa urvalsfilen: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate that selections reference valid component IDs
    project_ids = {c.id for c in project.components}
    selection_ids = {c.id for c in selections.components}
    invalid_ids = selection_ids - project_ids
    if invalid_ids and not (selection_ids & project_ids):
        print(f"Fel: Komponent-ID i urval matchar inte projektet: {invalid_ids}", file=sys.stderr)
        sys.exit(1)

    result = compute_aggregate(project, selections)
    print(result.to_json())


if __name__ == "__main__":
    main()
