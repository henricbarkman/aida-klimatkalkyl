"""Robust extraction of JSON from LLM responses.

The agents used to do this inline:

    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return json.loads(text.strip())

That assumes the model always answers with exactly one JSON object, optionally
fenced. When it doesn't, `json.loads` raises and the raw Python message travels
all the way to the user. Both testers hit this in production:

  - Johanna (2026-06): "Fel: Expecting value: line 1 column 1 (char 0)"
    -> the response had no text block at all, so the parser got "".
  - Sara (2026-06-23, twice): "Fel: Expecting value: line 1 column 4 (char 3)"
    -> the parsed string started with "[", i.e. an array where an object was
    expected. (Brute-forcing the message confirms char 3 on line 1 is only
    reachable from a leading "[".)

The naive splitter also picks the WRONG block when the model appends a second
fenced example after the real answer, and it truncates when a fence appears
inside a string value.

So: try the cheap paths first, then fall back to scanning for balanced JSON
regions, and raise a typed error carrying the raw text for server-side logging.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Fenced blocks, with or without a language tag. Non-greedy so several blocks in
# one response stay separate. re.I so "```JSON" is caught too (the old splitter
# was case-sensitive and fell through to the bare-fence branch).
_FENCE_RE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)


class ModelOutputError(ValueError):
    """The model's answer could not be read as the JSON we asked for.

    Carries the raw text so callers can log it without re-plumbing it through
    the exception message (which may reach a user).
    """

    def __init__(self, message: str, raw: str = "", decode_error: str = "") -> None:
        super().__init__(message)
        self.raw = raw
        #: Why json.loads refused, kept out of the message so nothing that may
        #: reach a user reads like "Expecting value: line 1 column 4 (char 3)".
        self.decode_error = decode_error


def _balanced_spans(text: str, opener: str, closer: str) -> list[tuple[int, int]]:
    """Spans of balanced `opener`/`closer` regions, ignoring braces inside JSON
    strings. Only top-level regions are returned (nested ones are inside them
    anyway), longest-first is applied by the caller."""
    spans: list[tuple[int, int]] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            if depth == 0:
                start = i
            depth += 1
        elif ch == closer:
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    spans.append((start, i + 1))
                    start = -1
    return spans


def _candidates(text: str) -> list[str]:
    """Every substring worth trying."""
    out: list[str] = []

    def add(s: str) -> None:
        s = s.strip()
        if s and s not in out:
            out.append(s)

    # Fenced blocks, json-tagged first. The old code took the FIRST "```json"
    # occurrence, which loses when the model appends an illustrative snippet
    # after the real answer, so keep them all and let scoring decide.
    fenced = _FENCE_RE.findall(text)
    for tag, body in fenced:
        if tag.lower() == "json":
            add(body)
    for tag, body in fenced:
        if tag.lower() != "json":
            add(body)

    # An unterminated fence ("```json\n{...}" with the response cut short) never
    # matches the regex above, so peel it by hand.
    for marker in ("```json", "```JSON", "```"):
        if marker in text:
            add(text.split(marker, 1)[1])
            break

    # Balanced regions anywhere in the text, which is what rescues a payload
    # wrapped in prose.
    for opener, closer in (("{", "}"), ("[", "]")):
        for start, end in _balanced_spans(text, opener, closer):
            add(text[start:end])

    return out


def _size(parsed: Any) -> int:
    """How much payload a candidate carries. Used to prefer the real answer over
    a two-key example the model tacked on at the end."""
    return len(parsed) if isinstance(parsed, (dict, list)) else 0


def extract_json_value(
    text: str, *, what: str = "svaret", accept: tuple[type, ...] = (dict, list)
) -> Any:
    """Parse an LLM response into JSON of one of the `accept` types.

    Raises ModelOutputError (never json.JSONDecodeError) so callers can map it
    to a message a building manager can act on.
    """
    if not text or not text.strip():
        raise ModelOutputError(
            f"Modellen returnerade inget innehåll för {what}.", raw=text or ""
        )

    # The whole response being valid JSON is the overwhelmingly common case and
    # is never ambiguous, so it wins outright before any scoring.
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        first_error: str | None = str(exc)
    else:
        first_error = None
        if isinstance(parsed, accept):
            return parsed

    best: Any = None
    best_size = -1
    wrong_type: str | None = None
    for candidate in _candidates(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            if first_error is None:
                first_error = str(exc)
            continue
        # "[{...}]" where an object was wanted is a common one-off slip and
        # unambiguous, so unwrap it rather than failing the whole run.
        if (
            dict in accept
            and isinstance(parsed, list)
            and len(parsed) == 1
            and isinstance(parsed[0], dict)
        ):
            parsed = parsed[0]
        if not isinstance(parsed, accept):
            if wrong_type is None:
                wrong_type = type(parsed).__name__
            continue
        size = _size(parsed)
        if size > best_size:
            best, best_size = parsed, size

    if best is not None:
        return best
    if wrong_type is not None:
        raise ModelOutputError(
            f"Modellen svarade med {wrong_type} i stället för förväntad struktur "
            f"för {what}.",
            raw=text,
            decode_error=first_error or "",
        )
    raise ModelOutputError(
        f"Modellens svar för {what} gick inte att läsa som JSON.",
        raw=text,
        decode_error=first_error or "",
    )


def extract_json_object(text: str, *, what: str = "svaret") -> dict[str, Any]:
    """Parse an LLM response into a JSON object."""
    return extract_json_value(text, what=what, accept=(dict,))
