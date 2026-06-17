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


def normalize_component_name(name: str) -> str:
    """Normalize a Swedish component name to match our data keys."""
    name_lower = name.lower().strip()

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


