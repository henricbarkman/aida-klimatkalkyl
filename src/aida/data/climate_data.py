"""Generic climate data for building components.

Data source priority (same hierarchy used in LLM fallback):
1. EPD:er (environdec.com, EPD Norge, product-specific) — always first choice
2. Boverkets klimatdatabas — when no specific EPD exists
3. Estimates — last resort, clearly marked

Values represent typical CO2e per unit for conventional (baseline) and climate-optimized alternatives.
All values are approximations for decision support, not certified calculations.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MaterialData:
    name: str
    co2e_per_unit: float  # kg CO2e per unit
    cost_per_unit: float  # SEK per unit
    unit: str
    source: str
    category: str = ""  # reuse, climate_optimized, conventional


# Baseline data: conventional new production (NollCO2 worst-case principle)
# These represent materials bought new without climate optimization.
BASELINE_DATA: dict[str, list[MaterialData]] = {
    "golv": [
        MaterialData("Konventionellt vinylgolv", 12.0, 350, "m2", "Boverkets klimatdatabas 2023", "conventional"),
        MaterialData("Konventionell klinker", 15.0, 500, "m2", "EPD environdec.com", "conventional"),
        MaterialData("Konventionellt laminatgolv", 8.5, 250, "m2", "EPD environdec.com", "conventional"),
    ],
    "innervägg": [
        MaterialData("Gipsskiva + stålregel (standard)", 18.0, 800, "m2", "Boverkets klimatdatabas 2023", "conventional"),
    ],
    "yttervägg": [
        MaterialData("Tegelfasad + mineralull (standard)", 45.0, 2500, "m2", "Boverkets klimatdatabas 2023", "conventional"),
    ],
    "betongvägg": [
        MaterialData("Konventionell betong C30/37", 55.0, 1200, "m2", "Boverkets klimatdatabas 2023, NollCO2 2022", "conventional"),
    ],
    "fönster": [
        MaterialData("Standard 3-glas PVC-fönster", 85.0, 4500, "st", "EPD environdec.com", "conventional"),
        MaterialData("Standard 2-glas aluminium", 110.0, 5500, "st", "Boverkets klimatdatabas 2023", "conventional"),
    ],
    "tak": [
        MaterialData("Betongpannor (standard)", 25.0, 600, "m2", "Boverkets klimatdatabas 2023", "conventional"),
    ],
    "isolering": [
        MaterialData("Mineralull (standard)", 3.5, 150, "m2", "EPD environdec.com", "conventional"),
        MaterialData("EPS cellplast (standard)", 5.0, 120, "m2", "EPD environdec.com", "conventional"),
    ],
    "diskmaskin": [
        MaterialData("Industriell diskmaskin (standard)", 450.0, 35000, "st", "Generisk EPD industriutrustning", "conventional"),
    ],
    "kylanläggning": [
        MaterialData("Kylsystem R-404A (standard)", 1200.0, 85000, "st", "EPD kylsystem, Boverket 2023", "conventional"),
    ],
    "belysning": [
        MaterialData("Standard LED-armatur", 8.0, 1200, "st", "EPD environdec.com", "conventional"),
    ],
    "ventilation": [
        MaterialData("Ventilationskanal stål (standard)", 12.0, 800, "lm", "Boverkets klimatdatabas 2023", "conventional"),
    ],
    "dörr": [
        MaterialData("Standard innerdörr", 35.0, 3500, "st", "EPD environdec.com", "conventional"),
    ],
    "hiss": [
        MaterialData("Standard personhiss", 15000.0, 500000, "st", "Generisk EPD hissinstallation", "conventional"),
    ],
}

# Climate-optimized alternatives
OPTIMIZED_DATA: dict[str, list[MaterialData]] = {
    "golv": [
        MaterialData("Linoleumgolv (biobaserat)", 4.5, 400, "m2", "EPD Forbo Marmoleum", "climate_optimized"),
        MaterialData("Trägolv massivt (FSC)", 2.0, 550, "m2", "EPD svenskt trägolv, environdec.com", "climate_optimized"),
    ],
    "innervägg": [
        MaterialData("Gipsskiva + träreglar", 10.0, 750, "m2", "EPD Gyproc + träregel, Boverket 2023", "climate_optimized"),
    ],
    "yttervägg": [
        MaterialData("Träfasad + cellulosa", 15.0, 2200, "m2", "EPD träfasadsystem", "climate_optimized"),
    ],
    "betongvägg": [
        MaterialData("Klimatförbättrad betong (slagg)", 30.0, 1350, "m2", "EPD klimatbetong, Heidelberg/Swecem", "climate_optimized"),
    ],
    "fönster": [
        MaterialData("3-glas träfönster (FSC)", 55.0, 5800, "st", "EPD svenskt träfönster", "climate_optimized"),
    ],
    "tak": [
        MaterialData("Lertegel (lokal tillverkning)", 15.0, 750, "m2", "EPD Monier", "climate_optimized"),
    ],
    "isolering": [
        MaterialData("Cellulosaisolering (returfiber)", 1.0, 180, "m2", "EPD Thermofloc", "climate_optimized"),
    ],
    "diskmaskin": [
        MaterialData("Energieffektiv diskmaskin A+++", 350.0, 42000, "st", "EPD Electrolux Professional", "climate_optimized"),
    ],
    "kylanläggning": [
        MaterialData("Kylsystem CO2/propan (naturligt köldmedium)", 600.0, 95000, "st", "EPD kylsystem naturligt köldmedium", "climate_optimized"),
    ],
    "belysning": [
        MaterialData("LED-armatur låg klimatpåverkan", 5.0, 1500, "st", "EPD Fagerhult", "climate_optimized"),
    ],
    "ventilation": [
        MaterialData("Ventilationskanal återvunnet stål", 7.0, 900, "lm", "EPD återvunnen stålkanal", "climate_optimized"),
    ],
    "dörr": [
        MaterialData("Innerdörr massivt trä (FSC)", 15.0, 4500, "st", "EPD Swedoor", "climate_optimized"),
    ],
    "hiss": [
        MaterialData("Energieffektiv hiss (regen-broms)", 12000.0, 550000, "st", "EPD KONE EcoSpace", "climate_optimized"),
    ],
}

# Reuse data (typical savings and sources)
REUSE_DATA: dict[str, list[MaterialData]] = {
    "golv": [
        MaterialData("Återbrukat trägolv", 0.5, 200, "m2", "Sola byggåterbruk / CCBuild", "reuse"),
    ],
    "innervägg": [
        MaterialData("Återbrukade gipsskivor", 1.5, 400, "m2", "Sola byggåterbruk", "reuse"),
    ],
    "fönster": [
        MaterialData("Återbrukade fönster (renoverade)", 10.0, 2500, "st", "CCBuild marknadsplats", "reuse"),
    ],
    "dörr": [
        MaterialData("Återbrukad innerdörr", 3.0, 1500, "st", "Sola byggåterbruk / CCBuild", "reuse"),
    ],
    "belysning": [
        MaterialData("Återbrukad LED-armatur", 1.0, 600, "st", "CCBuild / lokal återbrukshandel", "reuse"),
    ],
    "ventilation": [
        MaterialData("Befintliga ventilationskanaler (återanvända)", 0.5, 200, "lm", "Projektets befintliga installation", "reuse"),
    ],
}

# Reasoning templates per alternative type
REASONING = {
    "reuse": "Återbruk eliminerar nästan all tillverkningsrelaterad klimatpåverkan. Kvarvarande CO2e kommer främst från transport och eventuell renovering av materialet.",
    "climate_optimized": "Klimatoptimerat alternativ med lägre CO2e-avtryck jämfört med konventionell produkt, genom val av material med lägre inbyggd klimatpåverkan.",
    "conventional": "Konventionell nyproduktion utan särskild klimathänsyn. Representerar baslinje enligt NollCO2-metodens princip.",
}


def normalize_component_name(name: str) -> str:
    """Normalize a Swedish component name to match our data keys."""
    name_lower = name.lower().strip()

    mappings = {
        "golv": ["golv", "floor", "golvbeläggning", "vinylgolv", "klinker", "laminat", "parkett", "trägolv"],
        "innervägg": ["innervägg", "innerväggar", "interior wall", "gipsvägg", "mellanvägg"],
        "yttervägg": ["yttervägg", "ytterväggar", "fasad", "exterior wall"],
        "betongvägg": ["betongvägg", "betong", "concrete"],
        "fönster": ["fönster", "window", "fönsterbyte", "energiglas"],
        "tak": ["tak", "roof", "takpannor", "takbeläggning", "yttertak"],
        "isolering": ["isolering", "insulation", "tilläggsisolering", "mineralull", "cellplast"],
        "diskmaskin": ["diskmaskin", "dishwasher", "diskutrustning"],
        "kylanläggning": ["kylanläggning", "kyl", "kylsystem", "refriger", "cooling", "kylutrustning"],
        "belysning": ["belysning", "ljus", "lighting", "lampor", "armaturer"],
        "ventilation": ["ventilation", "ventilationskanal", "fläkt"],
        "dörr": ["dörr", "dörrar", "door", "innerdörr"],
        "hiss": ["hiss", "elevator", "personhiss"],
    }

    for key, variants in mappings.items():
        for v in variants:
            if v in name_lower:
                return key

    return ""


def get_baseline_for_component(name: str) -> MaterialData | None:
    """Get the highest-emission baseline material for a component (NollCO2 worst-case)."""
    key = normalize_component_name(name)
    if not key or key not in BASELINE_DATA:
        return None
    options = BASELINE_DATA[key]
    return max(options, key=lambda m: m.co2e_per_unit)


def get_alternatives_for_component(name: str) -> list[MaterialData]:
    """Get all climate-optimized and reuse alternatives for a component."""
    key = normalize_component_name(name)
    if not key:
        return []
    result = []
    if key in REUSE_DATA:
        result.extend(REUSE_DATA[key])
    if key in OPTIMIZED_DATA:
        result.extend(OPTIMIZED_DATA[key])
    return result
