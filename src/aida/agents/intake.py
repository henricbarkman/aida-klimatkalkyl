"""Intake agent: extracts project parameters from natural language description."""

from __future__ import annotations

import json
import os
import sys

from aida.api_client import get_client, DEFAULT_MODEL
from aida.models import Component, Project

SYSTEM_PROMPT = """Du är AIda, en assistent för klimatkalkylering av ombyggnationer i Karlstads kommun.

Din uppgift är att extrahera projektinformation från en fri textbeskrivning av ett ombyggnadsprojekt.

Du ska identifiera:
1. Byggnadstyp (t.ex. skola, kontor, förskola, bostadshus)
2. Ungefärlig area i BTA (bruttoarea i kvadratmeter)
3. En lista av renoveringskomponenter (vad som ska bytas/renoveras)

Om beskrivningen är vag eller saknar viktig information, be om förtydligande i fältet "clarification_needed".

Svara ALLTID med giltig JSON i detta format:
{
  "building_type": "string",
  "area_bta": number,
  "name": "projektnamn om nämnt",
  "description": "original beskrivning",
  "components": [
    {"id": "c1", "name": "komponentnamn", "quantity": number, "unit": "m2|st|lm", "category": "kategori"}
  ],
  "clarification_needed": "null eller sträng med frågor"
}

Regler:
- Komponent-id ska vara c1, c2, c3 etc
- Gissa rimlig quantity om den inte anges (baserat på area och byggnadstyp)
- Unit ska vara m2, st, eller lm (löpmeter)
- Category ska vara en av: golv, vägg, tak, fönster, dörr, installation, isolering, övrigt
- Om area inte anges, sätt area_bta till 0 och be om förtydligande
- Svara på svenska
"""


def run_intake(description: str) -> dict:
    """Extract project parameters from a natural language description."""
    client = get_client()

    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": description}
        ],
    )

    text = response.content[0].text

    # Extract JSON from response (handle markdown code blocks)
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    return json.loads(text.strip())


def intake_from_description(description: str) -> Project:
    """Run intake and return a Project object."""
    data = run_intake(description)
    return Project.from_dict(data)


def main():
    """CLI entry point for intake."""
    if len(sys.argv) < 3 or sys.argv[1] != "--input":
        print("Usage: python -m aida.agents.intake --input <description>", file=sys.stderr)
        sys.exit(1)

    description = sys.argv[2]
    print("Steg 1/1: Analyserar projektbeskrivning...", file=sys.stderr)

    result = run_intake(description)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
