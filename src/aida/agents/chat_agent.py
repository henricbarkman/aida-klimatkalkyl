"""Chat agent with tool-use: surgical state mutations via conversation.

Phase 1 scope: three tools that edit project components and selections.
Heavier operations (rerun baseline/alternatives) stay on the button flow —
the agent suggests them in text when appropriate.
"""

from __future__ import annotations

import copy
import logging

from aida.api_client import DEFAULT_MODEL, extract_text, get_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Du är AIda — en byggnadsexpert som hjälper förvaltare och byggledare att hitta renoveringslösningar med kraftigt minskad klimatpåverkan utan att ge avkall på praktiska behov.

Du ser projektets nuvarande state (komponenter, baslinje, alternativ, val) och kan använda verktyg för att korrigera det direkt baserat på användarens input.

VERKTYG:
- update_component — korrigera material, mängd, enhet eller kategori för en komponent ("det är linoleum, inte vinyl", "500 m² blev 700")
- select_alternative — välj ett alternativ för en komponent ("välj Tarkett iQ för golvet")
- remove_component — ta bort en komponent ("vi byter inte fönstren ändå")

NÄR DU SKA ANVÄNDA VERKTYG:
- Använd verktyg när användaren ger en konkret korrigering eller ett val som går att genomföra direkt.
- Använd INTE verktyg för rena frågor ("varför är betong sämre?") — svara bara med text.
- Om användaren är tvetydig, fråga först, använd verktyg sen.

EFTER EN MUTERING:
- Bekräfta kort vad som ändrades.
- Om ändringen påverkar klimat/pris (material, mängd, tillkommen eller borttagen komponent): berätta i texten att baslinjen och alternativen nu är inaktuella och föreslå en omkörning ("Klicka 'Räkna om baslinjen' för att uppdatera värdet").
- Om det är ett val (select_alternative): nämn den nya totala besparingen om baslinje och alla val finns.

PRINCIPER:
- Priser avser installerat pris (material + arbete) i SEK exkl moms.
- Svara på svenska, kortfattat och konkret.
- Siffror hämtar du från statet jag ger dig, fabricera aldrig.
"""


TOOLS = [
    {
        "name": "update_component",
        "description": (
            "Uppdatera en komponents egenskaper (namn, mängd, enhet, kategori). "
            "Använd när användaren korrigerar ett material eller en mängd. "
            "Inkludera bara de fält som faktiskt ska ändras."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "component_id": {
                    "type": "string",
                    "description": "ID från projektets komponentlista (c1, c2, etc)",
                },
                "name": {"type": "string"},
                "quantity": {"type": "number"},
                "unit": {"type": "string", "enum": ["m2", "st", "lm"]},
                "category": {
                    "type": "string",
                    "enum": [
                        "golv", "innervägg", "yttervägg", "betongvägg", "tak",
                        "fönster", "dörr", "isolering", "belysning", "ventilation",
                        "hiss", "kylanläggning", "sanitet", "vitvaror", "storköksutrustning",
                    ],
                },
            },
            "required": ["component_id"],
        },
    },
    {
        "name": "select_alternative",
        "description": (
            "Välj ett av de befintliga alternativen för en komponent. "
            "Matcha fuzzy på produktnamn mot alternatives-listan i state. "
            "Om användaren vill välja baslinjen istället, använd alternative_name='baslinje'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "component_id": {"type": "string"},
                "alternative_name": {
                    "type": "string",
                    "description": "Produktnamn eller del av namn (fuzzy-matchas), eller 'baslinje' för baslinjevalet.",
                },
            },
            "required": ["component_id", "alternative_name"],
        },
    },
    {
        "name": "remove_component",
        "description": "Ta bort en komponent helt från projektet. Baslinje, alternativ och val för komponenten rensas också.",
        "input_schema": {
            "type": "object",
            "properties": {
                "component_id": {"type": "string"},
            },
            "required": ["component_id"],
        },
    },
]


def _format_state(project, baseline, alternatives, selections) -> str:
    """Compact, LLM-readable snapshot of current state."""
    lines = []
    if project:
        lines.append(f"PROJEKT: {project.get('building_type', '?')}, {project.get('area_bta', '?')} m² BTA")
        if project.get("name"):
            lines.append(f"Namn: {project['name']}")
        lines.append("KOMPONENTER:")
        for c in project.get("components", []):
            lines.append(
                f"  {c.get('id')}: {c.get('name')} — {c.get('quantity')} {c.get('unit')} [{c.get('category', '?')}]"
            )
    else:
        lines.append("PROJEKT: (inget projekt än)")

    if baseline and baseline.get("components"):
        total_co2 = sum(c.get("co2e_kg", 0) for c in baseline["components"])
        total_cost = sum(c.get("cost_sek", 0) for c in baseline["components"])
        lines.append(f"\nBASLINJE: {round(total_co2):,} kg CO₂e, {round(total_cost):,} SEK totalt")
        for c in baseline["components"]:
            lines.append(
                f"  {c.get('component_id')}: {c.get('component_name')} — "
                f"{round(c.get('co2e_kg', 0))} kg CO₂e, {round(c.get('cost_sek', 0))} SEK"
            )

    if alternatives and alternatives.get("components"):
        lines.append("\nALTERNATIV:")
        for c in alternatives["components"]:
            alts = c.get("alternatives", [])
            lines.append(f"  {c.get('component_id')}: {c.get('component_name')} — {len(alts)} alternativ")
            for a in alts[:5]:
                lines.append(
                    f"    • {a.get('name')}: {round(a.get('co2e_kg', 0))} kg CO₂e, {round(a.get('cost_sek', 0))} SEK"
                )
            if len(alts) > 5:
                lines.append(f"    ... +{len(alts) - 5} till")

    if selections:
        sel_entries = [(cid, s) for cid, s in selections.items() if s]
        if sel_entries:
            lines.append("\nVAL:")
            for cid, s in sel_entries:
                sel = s.get("selected_alternative", {})
                lines.append(
                    f"  {cid}: {s.get('name')} → {sel.get('name')} "
                    f"({round(sel.get('co2e_kg', 0))} kg, {round(sel.get('cost_sek', 0))} SEK)"
                )

    return "\n".join(lines)


def _find_component(project, component_id):
    if not project:
        return None
    for c in project.get("components", []):
        if c.get("id") == component_id:
            return c
    return None


def _find_component_alternatives(alternatives, component_id):
    if not alternatives:
        return None
    for c in alternatives.get("components", []):
        if c.get("component_id") == component_id:
            return c
    return None


def _apply_update_component(inp, project, baseline, alternatives, selections):
    cid = inp.get("component_id")
    target = _find_component(project, cid)
    if not target:
        return f"Komponent {cid} finns inte i projektet.", False

    changed = {}
    for key in ("name", "quantity", "unit", "category"):
        if key in inp and inp[key] is not None:
            target[key] = inp[key]
            changed[key] = inp[key]
    if not changed:
        return f"Ingen ändring angiven för {cid}.", False

    return (
        f"Uppdaterade komponent {cid}: {changed}. "
        f"OBS: baslinjen och alternativen för denna komponent är nu inaktuella — kör om dem."
    ), True


def _apply_remove_component(inp, project, baseline, alternatives, selections):
    cid = inp.get("component_id")
    target = _find_component(project, cid)
    if not target:
        return f"Komponent {cid} finns inte i projektet.", False

    project["components"] = [c for c in project.get("components", []) if c.get("id") != cid]

    if baseline and baseline.get("components"):
        baseline["components"] = [c for c in baseline["components"] if c.get("component_id") != cid]

    if alternatives and alternatives.get("components"):
        alternatives["components"] = [
            c for c in alternatives["components"] if c.get("component_id") != cid
        ]

    if selections and cid in selections:
        del selections[cid]

    return f"Komponenten {cid} ({target.get('name')}) borttagen.", True


def _apply_select_alternative(inp, project, baseline, alternatives, selections):
    cid = inp.get("component_id")
    alt_query = (inp.get("alternative_name") or "").strip().lower()
    comp_alts = _find_component_alternatives(alternatives, cid)
    if not comp_alts:
        return f"Inga alternativ finns för {cid}.", False

    if alt_query == "baslinje":
        selections[cid] = {
            "id": cid,
            "name": comp_alts.get("component_name", ""),
            "selected_alternative": {
                "name": "Baslinje",
                "co2e_kg": comp_alts.get("baseline_co2e_kg", 0),
                "cost_sek": comp_alts.get("baseline_cost_sek", 0),
                "source": "NollCO2",
            },
            "baseline_co2e_kg": comp_alts.get("baseline_co2e_kg", 0),
            "baseline_cost_sek": comp_alts.get("baseline_cost_sek", 0),
        }
        return f"Valde baslinjen för {comp_alts.get('component_name', cid)}.", True

    match = None
    for a in comp_alts.get("alternatives", []):
        if alt_query in (a.get("name") or "").lower():
            match = a
            break

    if not match:
        names = [a.get("name", "") for a in comp_alts.get("alternatives", [])]
        return (
            f"Hittade inget alternativ som matchar '{inp.get('alternative_name')}' för {cid}. "
            f"Tillgängliga: {', '.join(names)}"
        ), False

    selections[cid] = {
        "id": cid,
        "name": comp_alts.get("component_name", ""),
        "selected_alternative": {
            "name": match.get("name", ""),
            "co2e_kg": match.get("co2e_kg", 0),
            "cost_sek": match.get("cost_sek", 0),
            "source": match.get("source", ""),
        },
        "baseline_co2e_kg": comp_alts.get("baseline_co2e_kg", 0),
        "baseline_cost_sek": comp_alts.get("baseline_cost_sek", 0),
    }
    return (
        f"Valde '{match.get('name')}' för {comp_alts.get('component_name', cid)} "
        f"({round(match.get('co2e_kg', 0))} kg CO₂e, {round(match.get('cost_sek', 0))} SEK)."
    ), True


_HANDLERS = {
    "update_component": _apply_update_component,
    "select_alternative": _apply_select_alternative,
    "remove_component": _apply_remove_component,
}


def _sanitize_history(history: list) -> list[dict]:
    """Filter history to a shape Anthropic accepts: only {role, content} entries
    with role in {user, assistant} and content as a non-empty string. Collapses
    consecutive same-role turns by dropping the earlier one — we never want
    two user or two assistant turns in a row."""
    clean: list[dict] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        content = entry.get("content")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if clean and clean[-1]["role"] == role:
            clean[-1] = {"role": role, "content": content}
        else:
            clean.append({"role": role, "content": content})
    return clean


def run_chat_agent(
    message: str,
    history: list[dict] | None = None,
    project: dict | None = None,
    baseline: dict | None = None,
    alternatives: dict | None = None,
    selections: dict | None = None,
    max_turns: int = 5,
) -> dict:
    """Run chat with tool-use loop.

    Returns dict with:
      - reply: str — assistant's final text reply
      - state_updates: dict — {project?, selections?} with changed objects
      - tool_calls: list — trace of tool invocations (for debug/UI)
    """
    client = get_client()
    history = _sanitize_history(history or [])

    # Work on copies so we can diff at the end.
    project = copy.deepcopy(project) if project else None
    baseline = copy.deepcopy(baseline) if baseline else None
    alternatives = copy.deepcopy(alternatives) if alternatives else None
    selections = copy.deepcopy(selections) if selections else {}

    project_changed = False
    selections_changed = False
    tool_calls: list[dict] = []

    state_block = _format_state(project, baseline, alternatives, selections)
    system_prompt = SYSTEM_PROMPT + "\n\nNUVARANDE STATE:\n" + state_block

    messages: list[dict] = list(history[-10:]) + [{"role": "user", "content": message}]

    for _ in range(max_turns):
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=1500,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            reply = extract_text(response) or ""
            return {
                "reply": reply.strip(),
                "state_updates": _build_state_updates(
                    project if project_changed else None,
                    selections if selections_changed else None,
                ),
                "tool_calls": tool_calls,
            }

        # Accumulate assistant turn (text + tool_use blocks)
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            handler = _HANDLERS.get(block.name)
            if not handler:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": f"Okänt verktyg: {block.name}",
                    "is_error": True,
                })
                tool_calls.append({"name": block.name, "input": block.input, "ok": False})
                continue

            # Snapshot selection membership so we only flag selections_changed
            # when remove_component actually dropped a selected entry.
            removed_cid = block.input.get("component_id") if block.name == "remove_component" else None
            had_selection_for_removed = bool(removed_cid and removed_cid in selections)

            try:
                result_text, ok = handler(block.input, project, baseline, alternatives, selections)
            except Exception as e:
                logger.exception("Tool %s failed", block.name)
                result_text = f"Fel vid {block.name}: {e}"
                ok = False

            if ok:
                if block.name in ("update_component", "remove_component"):
                    project_changed = True
                if block.name == "remove_component" and had_selection_for_removed:
                    selections_changed = True
                if block.name == "select_alternative":
                    selections_changed = True

            tool_calls.append({
                "name": block.name,
                "input": block.input,
                "ok": ok,
                "result": result_text,
            })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_text,
                **({"is_error": True} if not ok else {}),
            })

        messages.append({"role": "user", "content": tool_results})

    # Exhausted turns without a stop — force a final reply.
    logger.warning("chat_agent hit max_turns=%d", max_turns)
    return {
        "reply": "Jag fastnade i en loop. Försök formulera om, eller använd knapparna för att köra om stegen.",
        "state_updates": _build_state_updates(
            project if project_changed else None,
            selections if selections_changed else None,
        ),
        "tool_calls": tool_calls,
    }


def _build_state_updates(project, selections):
    updates = {}
    if project is not None:
        updates["project"] = project
    if selections is not None:
        updates["selections"] = selections
    return updates
