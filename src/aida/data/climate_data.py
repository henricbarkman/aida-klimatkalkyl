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
        "golv": ["golv", "floor", "golvbeläggning", "vinylgolv", "klinker",
                 "laminat", "parkett", "trägolv", "golvmaterial"],
        "innervägg": ["innervägg", "innerväggar", "interior wall", "gipsvägg",
                      "mellanvägg", "gipsskiva", "byggskivor", "byggskiva",
                      "ytskikt", "målning", "måla", "väggfärg", "färg",
                      "dispersionsfärg", "väggöverdraget", "väggöverdrag"],
        "yttervägg": ["yttervägg", "ytterväggar", "fasad", "exterior wall",
                      "puts", "bruk", "tegel", "tegelfasad"],
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
    }

    for key, variants in mappings.items():
        for v in variants:
            if v in name_lower:
                return key

    return ""


