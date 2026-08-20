"""Matching an LLM-written product name back to the name we asked about.

The model retypes names rather than copying them, and the differences are
invisible at a glance. Every place that has to recognise "the thing I asked
about" in "the thing the model wrote back" needs the same normalisation, and
until 2026-08-20 two of them had drifted apart: the EPD matcher had it, the
price matcher did not, so a price the web search had genuinely found was
discarded because the model wrote an en dash where the catalogue had a hyphen.

Live evidence for each rule below is in the docstrings. They are all real
failures, not defensive guesses.
"""
from __future__ import annotations

import re

_DASHES = ("‐", "‑", "‒", "–", "—", "―", "−")
_MARKS = ("®", "™", "©")
_NBSP = " "


def match_key(name: str) -> str:
    """Normalise a product name for comparison.

    Observed rewrites, all from live runs:
      - "Outdoor panel spruce – black" for a catalogue "... - black". An en dash
        instead of a hyphen was enough to lose the GWP-GHG marking (2026-08-17).
      - "BCLarch profiled cladding" for "BCLarch® profiled cladding".
      - "Marmoleum Real linoleumgolv 2,5 mm" for "... 2.5 mm". Swedish decimal
        comma; the price for that row was found and then thrown away
        (2026-08-20).
      - "**Gipsskiva standardskiva 13 mm**" when the model answers in markdown.
    """
    lowered = name.strip().lower().strip("*").strip()
    for dash in _DASHES:
        lowered = lowered.replace(dash, "-")
    for mark in _MARKS:
        lowered = lowered.replace(mark, "")
    lowered = lowered.replace(_NBSP, " ")
    # A decimal comma and a decimal point are the same number. Narrow on
    # purpose: only between digits, so "HardiePanel®, Hardie®" keeps its comma
    # as a separator rather than becoming part of a word.
    lowered = re.sub(r"(?<=\d),(?=\d)", ".", lowered)
    return " ".join(lowered.split())


def tokens(key: str) -> set[str]:
    """Distinctive words in a normalised name, for when containment fails."""
    return {
        "".join(ch for ch in word if ch.isalnum())
        for word in key.split()
        if len(word) >= 3
    } - {""}


def best_token_match(
    needle: str,
    candidates: list[str],
    *,
    threshold: float = 0.6,
    margin: float = 0.15,
) -> str | None:
    """Pick the candidate whose distinctive words the needle covers best.

    For names the model reordered or re-punctuated past what containment can
    follow: "Fibre cement cladding - HardiePanel® / Hardie® Architectural Panel"
    against "S-P-10857 Fibre cement cladding: HardiePanel®, Hardie®
    Architectural Panel".

    Returns None unless one candidate is clearly ahead. Assigning the wrong
    price to a product is worse than leaving it unpriced: an unpriced row is
    visible and gets an estimate, a mispriced one travels into the report
    looking like a fact.
    """
    needle_tokens = tokens(match_key(needle))
    if len(needle_tokens) < 2:
        return None

    scored: list[tuple[float, str]] = []
    for cand in candidates:
        cand_tokens = tokens(match_key(cand))
        if len(cand_tokens) < 2:
            continue
        score = len(cand_tokens & needle_tokens) / len(cand_tokens)
        if score >= threshold:
            scored.append((score, cand))

    if not scored:
        return None
    scored.sort(key=lambda s: (-s[0], s[1]))
    if len(scored) > 1 and scored[0][0] - scored[1][0] < margin:
        return None
    return scored[0][1]
