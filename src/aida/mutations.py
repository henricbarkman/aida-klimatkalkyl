"""Every state mutation Aida can make, as pure functions.

These used to live inside chat_agent.py, reachable only by the model. That was
fine while the chat was the only way to change anything. It stopped being fine
when cells in the sheet became editable: a cell that edited state its own way
would be a second set of rules for what goes stale, and the two would drift.

So the rules live here instead, and both doors open onto them. The chat agent
dispatches tool calls through HANDLERS; /api/mutate dispatches a cell edit
through apply_mutation. Same functions, same invariants, one owner.

No LLM, no HTTP, no DOM. Every handler takes the four state bags plus a list of
pending actions, mutates them in place, and returns (message, ok, touched_bags).
That signature is what lets this module move behind /api/turn (design §3)
unchanged when the orchestrator arrives.
"""

from __future__ import annotations


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


def _scale_component_values(cid: str, factor: float, baseline, alternatives, selections) -> set[str]:
    """Scale all cached CO₂e and cost values for a single component by `factor`.

    Returns a set naming which state bags were touched. Per-unit climate and price
    are linear in quantity under NollCO2; scaling avoids a full rerun when only
    quantity changes.
    """
    touched: set[str] = set()
    if factor == 1.0:
        return touched

    if baseline and baseline.get("components"):
        for c in baseline["components"]:
            if c.get("component_id") == cid:
                c["co2e_kg"] = c.get("co2e_kg", 0) * factor
                c["cost_sek"] = c.get("cost_sek", 0) * factor
                touched.add("baseline")

    if alternatives and alternatives.get("components"):
        for c in alternatives["components"]:
            if c.get("component_id") != cid:
                continue
            if "baseline_co2e_kg" in c:
                c["baseline_co2e_kg"] = c.get("baseline_co2e_kg", 0) * factor
            if "baseline_cost_sek" in c:
                c["baseline_cost_sek"] = c.get("baseline_cost_sek", 0) * factor
            for a in c.get("alternatives", []):
                a["co2e_kg"] = a.get("co2e_kg", 0) * factor
                a["cost_sek"] = a.get("cost_sek", 0) * factor
            touched.add("alternatives")

    if selections and cid in selections:
        sel = selections[cid]
        sel["baseline_co2e_kg"] = sel.get("baseline_co2e_kg", 0) * factor
        sel["baseline_cost_sek"] = sel.get("baseline_cost_sek", 0) * factor
        chosen = sel.get("selected_alternative") or {}
        if chosen:
            chosen["co2e_kg"] = chosen.get("co2e_kg", 0) * factor
            chosen["cost_sek"] = chosen.get("cost_sek", 0) * factor
        touched.add("selections")

    return touched


def _apply_update_component(inp, project, baseline, alternatives, selections, pending_actions):
    cid = inp.get("component_id")
    target = _find_component(project, cid)
    if not target:
        return f"Komponent {cid} finns inte i projektet.", False, set()

    changed = {}
    old_quantity = target.get("quantity")
    for key in ("name", "quantity", "unit", "category"):
        if key in inp and inp[key] is not None:
            target[key] = inp[key]
            changed[key] = inp[key]
    if not changed:
        return f"Ingen ändring angiven för {cid}.", False, set()

    # If material identity changed (name/category), prior usage_context may no
    # longer match — better to clear it than carry stale functional requirements
    # into the next alternatives search. Pure quantity/unit changes preserve it.
    # Chat-agent has no tool to set usage_context directly; rerun intake to
    # generate a fresh one when needed.
    if "name" in changed or "category" in changed:
        target["usage_context"] = ""

    touched: set[str] = {"project"}

    quantity_only = set(changed.keys()) == {"quantity"}
    if quantity_only:
        new_quantity = target["quantity"]
        try:
            old_q = float(old_quantity)
            new_q = float(new_quantity)
        except (TypeError, ValueError):
            # Unparseable stored/new quantity — don't collapse to "0 == 0" and
            # report a false no-change; fall through to the generic update so the
            # user's edit (already written to the component) is kept.
            old_q = new_q = None
        if old_q is not None and new_q is not None:
            if old_q > 0 and new_q > 0 and old_q != new_q:
                factor = new_q / old_q
                touched |= _scale_component_values(cid, factor, baseline, alternatives, selections)
                return (
                    f"Uppdaterade {cid}: mängd {old_q:g} → {new_q:g} {target.get('unit', '')}. "
                    f"Baslinje och alternativ skalade automatiskt — ingen omräkning behövs."
                ), True, touched
            if old_q == new_q:
                return f"Ingen ändring: {cid} är redan {new_q:g}.", False, set()

    return (
        f"Uppdaterade komponent {cid}: {changed}. "
        f"OBS: baslinjen och alternativen för denna komponent är nu inaktuella — kör om dem."
    ), True, touched


def _next_component_id(project) -> str:
    """First free cN, so a new component never collides with an existing id.

    Not len(components)+1: after a removal that reuses a live id, and the whole
    state model keys baseline, alternatives and selections on it. A collision
    would silently attach the new component's figures to the old one.
    """
    taken = {c.get("id") for c in project.get("components", [])}
    n = 1
    while f"c{n}" in taken:
        n += 1
    return f"c{n}"


def _apply_add_component(inp, project, baseline, alternatives, selections, pending_actions):
    name = (inp.get("name") or "").strip()
    if not name:
        return "Komponenten behöver ett namn.", False, set()

    quantity = inp.get("quantity")
    try:
        quantity = float(quantity)
    except (TypeError, ValueError):
        return f"Mängd saknas eller går inte att tolka för {name}.", False, set()
    if quantity <= 0:
        return f"Mängden för {name} måste vara större än noll.", False, set()

    unit = inp.get("unit") or ""
    if unit not in ("m2", "st", "lm"):
        return f"Enheten för {name} måste vara m2, st eller lm.", False, set()

    # A duplicate name is more likely a second attempt at the same thing than a
    # genuine second component, and two rows with the same name are impossible
    # to tell apart in the comparison table.
    existing = [c for c in project.get("components", [])
                if (c.get("name") or "").strip().lower() == name.lower()]
    if existing:
        return (
            f"Det finns redan en komponent som heter {name} ({existing[0].get('id')}). "
            "Vill du ändra mängden på den i stället, eller ska den nya heta något annat?"
        ), False, set()

    cid = _next_component_id(project)
    source = inp.get("quantity_source")
    project.setdefault("components", []).append({
        "id": cid,
        "name": name,
        "quantity": quantity,
        "unit": unit,
        "category": inp.get("category") or "",
        # Component.__post_init__ normalises anything unexpected to "estimated",
        # so a wrong guess here degrades to the honest label rather than a lie.
        "quantity_source": source if source in ("user_specified", "estimated") else "estimated",
        "usage_context": inp.get("usage_context") or "",
    })

    return (
        f"La till {cid}: {name}, {quantity:g} {unit}. "
        "Den saknar baslinje och alternativ tills de körts för just den komponenten. "
        "Övriga komponenter och deras val rörs inte."
    ), True, {"project"}


def _apply_remove_component(inp, project, baseline, alternatives, selections, pending_actions):
    cid = inp.get("component_id")
    target = _find_component(project, cid)
    if not target:
        return f"Komponent {cid} finns inte i projektet.", False, set()

    project["components"] = [c for c in project.get("components", []) if c.get("id") != cid]
    touched: set[str] = {"project"}

    if baseline and baseline.get("components"):
        before = len(baseline["components"])
        baseline["components"] = [c for c in baseline["components"] if c.get("component_id") != cid]
        if len(baseline["components"]) != before:
            touched.add("baseline")

    if alternatives and alternatives.get("components"):
        before = len(alternatives["components"])
        alternatives["components"] = [
            c for c in alternatives["components"] if c.get("component_id") != cid
        ]
        if len(alternatives["components"]) != before:
            touched.add("alternatives")

    if selections and cid in selections:
        del selections[cid]
        touched.add("selections")

    return f"Komponenten {cid} ({target.get('name')}) borttagen.", True, touched


def _apply_select_alternative(inp, project, baseline, alternatives, selections, pending_actions):
    cid = inp.get("component_id")
    alt_query = (inp.get("alternative_name") or "").strip().lower()
    comp_alts = _find_component_alternatives(alternatives, cid)
    if not comp_alts:
        return f"Inga alternativ finns för {cid}.", False, set()

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
        return f"Valde baslinjen för {comp_alts.get('component_name', cid)}.", True, {"selections"}

    match = None
    for a in comp_alts.get("alternatives", []):
        # Skip "info" pseudo-alternatives ("Inget tillgängligt i Palats" etc.):
        # they carry co2e=0/cost=0 and selecting one would understate the total.
        if a.get("alternative_type") == "info":
            continue
        if alt_query in (a.get("name") or "").lower():
            match = a
            break

    if not match:
        names = [a.get("name", "") for a in comp_alts.get("alternatives", [])]
        return (
            f"Hittade inget alternativ som matchar '{inp.get('alternative_name')}' för {cid}. "
            f"Tillgängliga: {', '.join(names)}"
        ), False, set()

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
    ), True, {"selections"}


def _validate_component_ids(cids: list[str], project) -> tuple[list[str], list[str]]:
    """Split component_ids into (known, unknown) based on the project."""
    if not project:
        return [], cids
    known_set = {c.get("id") for c in project.get("components", [])}
    known = [c for c in cids if c in known_set]
    unknown = [c for c in cids if c not in known_set]
    return known, unknown


def _already_requested(pending_actions: list, action_type: str, cids: list[str]) -> bool:
    """True if this exact (type, sorted-component-ids) combo is already queued this turn."""
    target = tuple(sorted(cids))
    for pa in pending_actions:
        if pa.get("type") == action_type and tuple(sorted(pa.get("component_ids") or [])) == target:
            return True
    return False


_REASON_MAX = 500
_FEEDBACK_MAX = 500


def _apply_rerun_baseline(inp, project, baseline, alternatives, selections, pending_actions):
    raw_cids = inp.get("component_ids")
    cids = list(raw_cids) if isinstance(raw_cids, list) else []
    # Cap length so an oversized LLM-emitted reason cannot flood the next prompt
    # or the chat UI. The reason is shown to the user and stored in pending_actions.
    reason = (inp.get("reason") or "").strip()[:_REASON_MAX]

    if not reason:
        return "Saknar reason. Varför ska baslinjen räknas om?", False, set()

    if cids:
        known, unknown = _validate_component_ids(cids, project)
        if unknown:
            return f"Okänt komponent-id i rerun_baseline: {unknown}.", False, set()
        cids = known

    if _already_requested(pending_actions, "rerun_baseline", cids):
        return "Baslinje-omkörning är redan begärd denna tur.", False, set()

    pending_actions.append({
        "type": "rerun_baseline",
        "component_ids": cids,
        "reason": reason,
    })

    scope = "alla komponenter" if not cids else f"komponent {', '.join(cids)}"
    return f"Begärt: räkna om baslinjen för {scope}. Orsak: {reason}", True, set()


def _apply_rerun_alternatives(inp, project, baseline, alternatives, selections, pending_actions):
    raw_cids = inp.get("component_ids")
    cids = list(raw_cids) if isinstance(raw_cids, list) else []
    reason = (inp.get("reason") or "").strip()[:_REASON_MAX]
    # user_feedback flows into the alternatives-LLM prompt as an extra instruction.
    # Cap to limit prompt-injection blast radius from a manipulated LLM emission.
    user_feedback = (inp.get("user_feedback") or "").strip()[:_FEEDBACK_MAX]

    if not reason:
        return "Saknar reason. Varför ska alternativen räknas om?", False, set()

    if cids:
        known, unknown = _validate_component_ids(cids, project)
        if unknown:
            return f"Okänt komponent-id i rerun_alternatives: {unknown}.", False, set()
        cids = known

    if _already_requested(pending_actions, "rerun_alternatives", cids):
        return "Alternativ-omkörning är redan begärd denna tur.", False, set()

    action = {
        "type": "rerun_alternatives",
        "component_ids": cids,
        "reason": reason,
    }
    if user_feedback:
        action["user_feedback"] = user_feedback
    pending_actions.append(action)

    scope = "alla komponenter" if not cids else f"komponent {', '.join(cids)}"
    feedback_note = f" Önskemål: {user_feedback}." if user_feedback else ""
    return f"Begärt: kör om alternativen för {scope}. Orsak: {reason}.{feedback_note}", True, set()


HANDLERS = {
    "update_component": _apply_update_component,
    "add_component": _apply_add_component,
    "select_alternative": _apply_select_alternative,
    "remove_component": _apply_remove_component,
    "rerun_baseline": _apply_rerun_baseline,
    "rerun_alternatives": _apply_rerun_alternatives,
}


def build_state_updates(
    touched: set[str], project, baseline, alternatives, selections,
    pending_actions: list[dict] | None = None,
) -> dict:
    updates: dict = {}
    if "project" in touched:
        updates["project"] = project
    if "baseline" in touched:
        updates["baseline"] = baseline
    if "alternatives" in touched:
        updates["alternatives"] = alternatives
    if "selections" in touched:
        updates["selections"] = selections
    if pending_actions:
        updates["pending_actions"] = pending_actions
    return updates


# Fields whose change makes the cached climate and cost figures wrong rather than
# merely scaled. Quantity is linear under NollCO2 and is handled by scaling; these
# are not, so they need the numbers computed again.
_MATERIAL_FIELDS = ("name", "category", "unit")


def _component_ids(project) -> set[str]:
    return {c.get("id") for c in (project or {}).get("components", [])}


def _queue_reruns(tool, inp, project, before_ids, before_component, pending_actions):
    """Queue the scoped reruns the chat agent's prompt asks the model to make.

    The chat path states these two rules in the system prompt and trusts the model
    to follow them. A cell edit has no model to trust, so the same rules are
    applied here in code. That is the better place for them: a rule in a prompt is
    a request, a rule here is a guarantee, and the two doors now cannot disagree
    about when a component's numbers stopped being true.
    """
    if tool == "add_component":
        cids = sorted(_component_ids(project) - before_ids)
        reason = "Ny komponent saknar baslinje och alternativ."
    elif tool == "update_component":
        changed = [f for f in _MATERIAL_FIELDS
                   if f in inp and inp[f] is not None
                   and (before_component or {}).get(f) != inp[f]]
        if not changed:
            return  # quantity only: the handler already scaled, nothing is stale
        cids = [inp.get("component_id")]
        reason = f"Ändrat {', '.join(changed)} kräver nya värden."
    else:
        return

    if not cids:
        return

    # Scoped, never the empty list. Empty means "recompute everything", which
    # would throw away every other component's choices to fix one cell.
    for action_type in ("rerun_baseline", "rerun_alternatives"):
        if not _already_requested(pending_actions, action_type, cids):
            pending_actions.append({
                "type": action_type,
                "component_ids": cids,
                "reason": reason,
            })


def apply_mutation(tool, inp, project, baseline, alternatives, selections,
                   auto_rerun: bool = False) -> dict:
    """Run one mutation and report what changed. The door /api/mutate comes in by.

    `auto_rerun` is what separates a cell edit from a chat tool call. The model is
    told to follow a material change with scoped reruns; a cell has nobody to tell,
    so it asks for them here.
    """
    handler = HANDLERS.get(tool)
    if handler is None:
        return {"ok": False, "message": f"Okänt verktyg: {tool}", "state_updates": {}}

    before_ids = _component_ids(project)
    before_component = None
    if tool == "update_component":
        found = _find_component(project, inp.get("component_id"))
        before_component = dict(found) if found else None

    pending_actions: list[dict] = []
    message, ok, touched = handler(
        inp, project, baseline, alternatives, selections, pending_actions,
    )

    if ok and auto_rerun:
        _queue_reruns(tool, inp, project, before_ids, before_component, pending_actions)

    return {
        "ok": ok,
        "message": message,
        "state_updates": build_state_updates(
            touched, project, baseline, alternatives, selections, pending_actions,
        ),
    }
