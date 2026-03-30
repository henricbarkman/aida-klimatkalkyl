"""Report agent: generates exportable summary report."""

from __future__ import annotations

import json
import sys
from datetime import date

from aida.agents.aggregate import compute_aggregate
from aida.api_client import (
    DEFAULT_MODEL,
    THINKING_STANDARD,
    extract_text,
    get_client,
    thinking_config,
)
from aida.models import Project, Selections

REPORT_SYSTEM_PROMPT = """Du är AIda:s rapportgenerator — en byggnadsexpert som skapar strukturerade beslutsunderlag för ombyggnadsprojekt.

AIda:s uppdrag är att hjälpa förvaltare och byggledare att hitta renoveringslösningar som kraftigt minskar klimatpåverkan utan att ge avkall på praktiska behov.

Rapporten ska:
1. Vara skriven på formell svenska, lämplig för tjänsteskrivelser och upphandlingsunderlag
2. Vara strukturerad med tydliga rubriker
3. Inkludera alla siffror med enheter (kg CO2e, SEK)
4. Referera till datakällor
5. Innehålla en sammanfattning överst
6. Ha en komponenttabell med valda alternativ
7. Notera osäkerheter och begränsningar
8. Ange att priser avser installerat pris (material + arbete) exkl. moms

UNDVIK:
- Informellt språk
- Anglicismer
- AI-typiska fraser ("delve into", "game-changer")
- Em-dashes

Skriv rapporten i markdown-format."""


def generate_report_markdown(project: Project, selections: Selections) -> str:
    """Generate a markdown report from project and selections."""
    aggregate = compute_aggregate(project, selections)

    # Build context for LLM
    component_table = ""
    for comp in aggregate.components:
        saving_pct = (comp["co2e_besparing_kg"] / comp["baslinje_co2e_kg"] * 100) if comp["baslinje_co2e_kg"] > 0 else 0
        component_table += (
            f"| {comp['name']} | {comp['valt_alternativ']} | "
            f"{comp['co2e_kg']:.0f} | {comp['baslinje_co2e_kg']:.0f} | "
            f"{comp['co2e_besparing_kg']:.0f} ({saving_pct:.0f}%) | "
            f"{comp['kostnad_sek']:,.0f} | {comp['källa']} |\n"
        )

    saving_pct_total = (
        aggregate.co2e_savings_kg / aggregate.baseline_total_co2e_kg * 100
    ) if aggregate.baseline_total_co2e_kg > 0 else 0

    client = get_client()

    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=4000 + THINKING_STANDARD,
        thinking=thinking_config(THINKING_STANDARD),
        system=REPORT_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""Generera en rapport för detta ombyggnadsprojekt:

Projekttyp: {project.building_type}
Area: {project.area_bta} m² BTA
Datum: {date.today().isoformat()}

Sammanställning:
- Total klimatpåverkan (valt): {aggregate.total_co2e_kg:.0f} kg CO2e
- Baslinje (konventionellt): {aggregate.baseline_total_co2e_kg:.0f} kg CO2e
- Klimatbesparing: {aggregate.co2e_savings_kg:.0f} kg CO2e ({saving_pct_total:.0f}%)
- Total kostnad (valt): {aggregate.total_cost_sek:,.0f} SEK
- Baslinje kostnad: {aggregate.baseline_total_cost_sek:,.0f} SEK
- Kostnadsskillnad: {aggregate.cost_difference_sek:+,.0f} SEK

Komponenttabell:
| Komponent | Valt alternativ | CO2e (kg) | Baslinje (kg) | Besparing | Kostnad (SEK) | Källa |
|-----------|----------------|-----------|---------------|-----------|---------------|-------|
{component_table}

Skriv en komplett rapport i markdown. Inkludera disclaimer om att detta är uppskattningar för beslutsstöd."""
        }],
    )

    return extract_text(response)


def generate_report_pdf(project: Project, selections: Selections, output_path: str) -> str:
    """Generate a PDF report. Falls back to markdown if PDF generation fails."""
    markdown = generate_report_markdown(project, selections)

    try:
        import subprocess
        md_path = output_path.replace(".pdf", ".md")
        with open(md_path, "w") as f:
            f.write(markdown)

        result = subprocess.run(
            ["pandoc", md_path, "-o", output_path, "--pdf-engine=xelatex"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return output_path
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    # Fallback: save as markdown
    md_path = output_path.replace(".pdf", ".md")
    with open(md_path, "w") as f:
        f.write(markdown)
    return md_path


def main():
    """CLI entry point for report."""
    args = sys.argv[1:]

    project_path = None
    selections_path = None
    fmt = "markdown"
    output_path = None

    i = 0
    while i < len(args):
        if args[i] == "--project" and i + 1 < len(args):
            project_path = args[i + 1]
            i += 2
        elif args[i] == "--selections" and i + 1 < len(args):
            selections_path = args[i + 1]
            i += 2
        elif args[i] == "--format" and i + 1 < len(args):
            fmt = args[i + 1]
            i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            output_path = args[i + 1]
            i += 2
        else:
            i += 1

    if not project_path or not selections_path:
        print("Usage: python -m aida.agents.report --project <project.json> --selections <selections.json> [--format markdown|pdf] [--output path]", file=sys.stderr)
        sys.exit(1)

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

    if not selections.components:
        print("Fel: Inga komponenter valda. Kan inte generera rapport.", file=sys.stderr)
        sys.exit(1)

    if fmt == "pdf" and output_path:
        path = generate_report_pdf(project, selections, output_path)
        print(f"Rapport sparad: {path}", file=sys.stderr)
        with open(path) as f:
            print(f.read())
    else:
        report = generate_report_markdown(project, selections)
        if output_path:
            with open(output_path, "w") as f:
                f.write(report)
            print(f"Rapport sparad: {output_path}", file=sys.stderr)
        print(report)


if __name__ == "__main__":
    main()
