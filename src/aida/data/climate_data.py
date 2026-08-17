"""Utilities for component name normalization and reasoning templates.

Hardcoded climate data has been removed. Data sources:
- Baseline: Boverkets klimatdatabas (Typical A1-A3) + LLM estimation
- Alternatives: Environdec EPD:er (epd_alternatives.json)
- Reuse: Palats API (live)
"""

from __future__ import annotations

# Reasoning templates per alternative type
REASONING = {
    "reuse": "Återbruk eliminerar nästan all tillverkningsrelaterad klimatpåverkan. Kvarvarande CO2e kommer främst från transport och eventuell renovering av materialet.",
    "climate_optimized": "Klimatoptimerat alternativ med lägre CO2e-avtryck jämfört med konventionell produkt, genom val av material med lägre inbyggd klimatpåverkan.",
    "conventional": "Konventionell nyproduktion utan särskild klimathänsyn. Representerar baslinjen: vad standardmaterial kostar klimatmässigt (Boverket Typical A1-A3).",
}


# Checked before the substring table below. Plain substring matching cannot
# express "the primary noun wins", so a name carrying two category words lands
# wherever the table happens to look first. These two compounds are unambiguous
# on their own: both name a paint product, and without them yttervägg's bare
# "fasad" swallowed "fasadfärg" and priced a coat of paint as a wall. Keep the
# list to words that can only ever mean one thing.
_PRIORITY_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("farg", ("fasadfärg", "fasadmålning")),
)


def normalize_component_name(name: str) -> str:
    """Normalize a Swedish component name to match our data keys."""
    name_lower = name.lower().strip()

    for key, tokens in _PRIORITY_TOKENS:
        if any(t in name_lower for t in tokens):
            return key

    mappings = {
        # Keramik/kakel checked BEFORE golv: "golvklinker" contains "golv", so
        # golv would otherwise steal it. Ceramic wall+floor tile share one
        # material bucket; klinker (floor tile) moved here from golv 2026-06-17
        # so it gets a ceramic typvärde, not the vinyl/linoleum-dominated golv one.
        "kakel": ["kakel", "klinker", "keramik", "kakelplatt", "väggkakel",
                  "kakla", "ceramic tile"],
        "golv": ["golv", "floor", "golvbeläggning", "vinylgolv",
                 "laminat", "parkett", "trägolv", "golvmaterial"],
        # Paint moved out to the dedicated "farg" category 2026-06-17 (it has its
        # own EPDs and a wrong unit basis vs gypsum). "ytskikt" stays here since
        # an unqualified surface layer on an interior wall is usually board, not paint.
        "innervägg": ["innervägg", "innerväggar", "interior wall", "gipsvägg",
                      "mellanvägg", "gipsskiva", "byggskivor", "byggskiva",
                      "ytskikt", "väggöverdraget", "väggöverdrag"],
        # Facade CLADDING, checked before yttervägg. Renovating the skin of a
        # building is not the same job as building a wall, and conflating the
        # two made both numbers wrong at once: Sara's 22mm wood panel was
        # measured against a typvärde for a complete ~200mm wall build-up, so
        # the baseline came out far too high and any alternative looked
        # spectacular. Same trap PROJECT.md notes for roof membranes vs whole
        # roofs. Bare "fasad" deliberately stays with yttervägg below, since
        # unqualified it usually means the wall; the compounds that name the
        # layer land here.
        # Stems, not full words: "fasadskiva" would miss "fasadskivor".
        "fasadskikt": ["fasadskikt", "fasadpanel", "fasadbeklädnad",
                       "fasadskiv", "fasadplatt", "fasadrenovering",
                       "fasadbyte", "träfasad", "träpanel", "panelbräd",
                       "wood cladding", "timber cladding", "facade cladding"],
        "yttervägg": ["yttervägg", "ytterväggar", "fasad", "exterior wall",
                      "puts", "bruk", "tegel", "tegelfasad"],
        # Bärande stomme (steel/timber/concrete frame). Placed BEFORE betongvägg
        # so a "betongbjälklag" (a floor slab is frame, not wall) routes here via
        # "bjälklag", while a plain "betongvägg" still falls through below. We
        # deliberately omit bare "bärande" (would steal load-bearing walls) and
        # bare "stål" (would steal ventilation's "stålkanal").
        "stomme": ["stomme", "stomsystem", "stålstomme", "stålbalk",
                   "stålpelare", "stålbjälke", "limträ", "limträbalk",
                   "kl-trä", "klträ", "korslimmat", "massivträ", "träbalk",
                   "träpelare", "betongbalk", "betongpelare", "bjälklag",
                   "håldäck", "balk", "pelare"],
        "betongvägg": ["betongvägg", "betong", "concrete"],
        "fönster": ["fönster", "window", "fönsterbyte", "energiglas"],
        "tak": ["tak", "roof", "takpannor", "takbeläggning", "yttertak",
                "takprodukter"],
        "isolering": ["isolering", "insulation", "tilläggsisolering",
                      "mineralull", "cellplast", "glasull", "stenull",
                      "cellulosa", "eps"],
        "storköksutrustning": ["storköksutrustning", "storkök", "diskmaskin",
                               "diskutrustning", "industrial kitchen"],
        "kylanläggning": ["kylanläggning", "kyl", "kylsystem", "refriger",
                          "cooling", "kylutrustning"],
        "belysning": ["belysning", "ljus", "lighting", "lampor", "armaturer"],
        "ventilation": ["ventilation", "ventilationskanal", "fläkt",
                        "stålkanal"],
        "dörr": ["dörr", "dörrar", "door", "innerdörr"],
        "hiss": ["hiss", "elevator", "personhiss"],
        "sanitet": ["sanitet", "toalett", "wc", "handfat", "tvättställ",
                    "dusch", "badkar", "urinal", "blandare", "toilet",
                    "washbasin", "shower"],
        "vitvaror": ["vitvaror", "tvättmaskin", "torktumlare", "torkskåp",
                     "spis", "häll", "ugn", "mikrovåg", "köksfläkt",
                     "cooker hood", "washing machine"],
        # Renovation materials added 2026-06-17. Terms kept specific to avoid
        # stealing: no bare "el" (matches "element"/"elektronik"), no bare
        # "element" (matches "betongelement"), no bare "färg" (matches colours).
        # No bare "rör"/"stam": "rör" is too short and "stam" risks false hits.
        # Compounds below cover the real renovation cases (stambyte = pipe
        # replacement). "ventilationsrör" is already caught by ventilation above
        # ("ventilation" is a substring of it).
        "vvs": ["vvs", "stambyte", "stamledning", "avloppsrör", "vattenrör",
                "spillvatten", "tappvatten", "rörledning", "kopparrör",
                "dagvatten", "avlopp"],
        "farg": ["målning", "ommålning", "väggfärg", "fasadfärg",
                 "dispersionsfärg", "grundfärg", "målningsarbete"],
        "el": ["elkabel", "kabel", "kablage", "elinstallation", "elledning",
               "starkström", "elcentral"],
        "radiator": ["radiator", "radiatorer", "värmeelement", "värmepanel",
                     "handdukstork"],
    }

    for key, variants in mappings.items():
        for v in variants:
            if v in name_lower:
                return key

    return ""


# Canonical category keys produced by normalize_component_name — also the valid
# values for a component's declared `category` field. Keep in sync with the
# mappings above, intake's category enum, and the EPD catalog categories.
VALID_CATEGORIES = {
    "kakel", "golv", "innervägg", "yttervägg", "fasadskikt", "stomme",
    "betongvägg", "fönster", "tak", "isolering", "storköksutrustning",
    "kylanläggning", "belysning", "ventilation", "dörr", "hiss", "sanitet",
    "vitvaror", "vvs", "farg", "el", "radiator",
}


def resolve_category(name: str, declared_category: str = "") -> str:
    """Resolve a component's EPD category.

    Honors the component's declared `category` (set by intake, which knows
    kakel/vvs/farg/el/radiator) when it is a known catalog category — so a
    tiled wall intake tagged `kakel` is treated as kakel by BOTH the baseline
    and the alternatives step, instead of being silently re-derived to
    innervägg from its name "Väggytskikt". Falls back to name-based
    normalization when the declared category is missing or unknown.
    """
    cat = (declared_category or "").strip().lower()
    if cat in VALID_CATEGORIES:
        return cat
    return normalize_component_name(name)


