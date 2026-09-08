"""Overrides on derived values (orchestration-redesign §12.5).

A förvaltare with a real EPD or a framework-agreement price wants the report to
use that figure instead of Aida's estimate. That is Gunnar's and Anna's case,
and it is also what makes follow-up possible at all.

The rule that shapes this whole module: an override lies ON TOP of the computed
value, never in place of it. Reruns compute their figure as usual and the
override is laid on afterwards, at read time. So it survives every rerun, and
lifting it shows the computed number again without recomputing anything.

That is why nothing here writes into the stored bags. `apply` takes the state
and returns new bags. The stored state stays exactly what the pipeline produced,
which is also what makes "what would Aida have said?" answerable at any point.

The same figure lives in four places at once - the baseline bag, the alternatives
bag's baseline row, the selection's baseline field, and the selected alternative
itself when the user chose "Baslinje". A single override has to reach all four or
the sheet contradicts itself. Hence one function rather than a patch at each read
site.

The client has a twin of `apply` in JS, because the view renders without asking
the server. `scripts/test_overrides.py` and `scripts/test_overrides_agree.js` run
both over the same fixtures and require identical output; the twin is allowed to
exist, drifting is not.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timezone

# What can be overridden. Both are baseline figures: §12.10's acceptance case is
# "an override on the floor's baseline survives 'think broader' plus a full rerun
# and is marked in the Word report". Overriding a chosen alternative's own figure
# is the same mechanism with two more keys and belongs in its own change.
OVERRIDE_FIELDS = ("baseline_co2e", "baseline_cost")

# Henric, 2026-09-08: "fritt med kort anteckning". Free because a förvaltare with
# a supplier EPD knows something Aida does not, and a tool that argues is a tool
# they route around. The note is mandatory because a number with a different
# origin that cannot say where it came from is worse than no number: it reads as
# Aida's own. 120 characters so it fits both the cell and the report margin.
NOTE_MAX = 120

_FIELD_LABELS = {
    "baseline_co2e": "Baslinje CO₂e",
    "baseline_cost": "Baslinje kostnad",
}


def normalize(field: str, value, note: str, at: str | None = None) -> tuple[dict | None, str]:
    """Validate one override. Returns (override, error) with exactly one set.

    Called from both doors, so the rules cannot differ between typing in a cell
    and asking in the chat.
    """
    if field not in OVERRIDE_FIELDS:
        return None, f"Okänt fält: {field}"

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, "Värdet måste vara ett tal."
    if number != number or number in (float("inf"), float("-inf")):
        return None, "Värdet måste vara ett tal."
    # Zero is allowed: a reused component really can be zero new production, and
    # a donated product really can be zero kronor. Negative is not a quantity
    # anyone has.
    if number < 0:
        return None, "Värdet kan inte vara negativt."

    text = (note or "").strip()
    if not text:
        return None, "En överskrivning måste ha en anteckning om var siffran kommer ifrån."
    text = text[:NOTE_MAX]

    return {
        "value": number,
        "note": text,
        "at": at or datetime.now(UTC).isoformat(timespec="seconds"),
    }, ""


def _get(overrides: dict | None, cid: str, field: str) -> dict | None:
    if not overrides or not cid:
        return None
    entry = overrides.get(cid)
    if not isinstance(entry, dict):
        return None
    one = entry.get(field)
    if not isinstance(one, dict) or "value" not in one:
        return None
    return one


def has_any(overrides: dict | None) -> bool:
    return any(
        _get(overrides, cid, field)
        for cid in (overrides or {})
        for field in OVERRIDE_FIELDS
    )


def apply(baseline, alternatives, selections, overrides):
    """Lay the overrides on top. Pure: returns new bags, inputs untouched.

    Every substituted number is marked in place with a `*_override` field
    carrying the note. The marking travels with the value rather than being
    recomputed at each display site, the same rule PR #550 set for GWP-GHG: a
    number with a different origin than the rest must never look like the rest.
    """
    baseline = copy.deepcopy(baseline) if baseline else baseline
    alternatives = copy.deepcopy(alternatives) if alternatives else alternatives
    selections = copy.deepcopy(selections) if selections else selections

    if not has_any(overrides):
        return baseline, alternatives, selections

    for cid in overrides:
        co2e = _get(overrides, cid, "baseline_co2e")
        cost = _get(overrides, cid, "baseline_cost")
        if not (co2e or cost):
            continue

        if baseline:
            for c in baseline.get("components", []) or []:
                if c.get("component_id") != cid:
                    continue
                if co2e:
                    c["co2e_kg"] = co2e["value"]
                    c["co2e_override"] = co2e["note"]
                if cost:
                    c["cost_sek"] = cost["value"]
                    c["cost_override"] = cost["note"]

        if alternatives:
            for c in alternatives.get("components", []) or []:
                if c.get("component_id") != cid:
                    continue
                if co2e:
                    c["baseline_co2e_kg"] = co2e["value"]
                    c["baseline_co2e_override"] = co2e["note"]
                if cost:
                    c["baseline_cost_sek"] = cost["value"]
                    c["baseline_cost_override"] = cost["note"]

        sel = (selections or {}).get(cid) if isinstance(selections, dict) else None
        if isinstance(sel, dict):
            chosen = sel.get("selected_alternative")
            # "Baslinje" as a choice means the selected figure IS the baseline
            # figure. Overriding one and not the other would put two different
            # numbers for the same decision in the same report.
            picked_baseline = isinstance(chosen, dict) and chosen.get("name") == "Baslinje"
            if co2e:
                sel["baseline_co2e_kg"] = co2e["value"]
                sel["baseline_co2e_override"] = co2e["note"]
                if picked_baseline:
                    chosen["co2e_kg"] = co2e["value"]
                    chosen["co2e_override"] = co2e["note"]
            if cost:
                sel["baseline_cost_sek"] = cost["value"]
                sel["baseline_cost_override"] = cost["note"]
                if picked_baseline:
                    chosen["cost_sek"] = cost["value"]
                    chosen["cost_override"] = cost["note"]

    return baseline, alternatives, selections


def apply_to_selections_payload(payload: dict, overrides: dict | None) -> dict:
    """The report and the aggregate receive `{components: [...]}`, not the map.

    Same substitution, different container. Kept here rather than at the call
    site so there is still one place where an override becomes a number.
    """
    if not payload or not has_any(overrides):
        return payload
    rows = payload.get("components") or []
    as_map = {r.get("id"): r for r in rows if isinstance(r, dict) and r.get("id")}
    _, _, patched = apply(None, None, as_map, overrides)
    out = copy.deepcopy(payload)
    out["components"] = [patched.get(r.get("id"), r) for r in rows]
    return out


def listing(project, overrides: dict | None) -> list[dict]:
    """The overrides in report order: component name, what was replaced, note.

    Deterministic, built from state rather than from anything the model wrote,
    for the same reason the GWP appendix is: the marking is the condition, not a
    nicety, so it must not depend on whether the model repeated it.
    """
    if not has_any(overrides):
        return []
    names = {}
    for c in (project or {}).get("components", []) or []:
        if c.get("id"):
            names[c["id"]] = c.get("name", "")
    rows = []
    for c in (project or {}).get("components", []) or []:
        cid = c.get("id")
        for field in OVERRIDE_FIELDS:
            one = _get(overrides, cid, field)
            if one:
                rows.append({
                    "komponent": names.get(cid, cid or ""),
                    "fält": _FIELD_LABELS[field],
                    "värde": one["value"],
                    "anteckning": one["note"],
                })
    # A component that has since been removed from the project still has to be
    # accounted for if its override is somehow still stored; silently dropping a
    # manual figure is the one thing this module must not do.
    seen = {c.get("id") for c in (project or {}).get("components", []) or []}
    for cid in sorted(overrides or {}):
        if cid in seen:
            continue
        for field in OVERRIDE_FIELDS:
            one = _get(overrides, cid, field)
            if one:
                rows.append({
                    "komponent": cid,
                    "fält": _FIELD_LABELS[field],
                    "värde": one["value"],
                    "anteckning": one["note"],
                })
    return rows


def drop_for_component(overrides: dict | None, cid: str) -> list[str]:
    """Forget this component's overrides. Returns the labels dropped, to report.

    Called when the component changes materially or disappears. An override is a
    figure for a specific thing at a specific quantity; once that thing changes,
    keeping the number would silently attribute a stale figure to the user's own
    source. Dropping it loudly is the honest half of "free to override".
    """
    if not overrides or cid not in overrides:
        return []
    entry = overrides.get(cid)
    dropped = [
        _FIELD_LABELS[field] for field in OVERRIDE_FIELDS
        if isinstance(entry, dict) and isinstance(entry.get(field), dict)
    ]
    if dropped:
        del overrides[cid]
    return dropped
