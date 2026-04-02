"""Unit conversion for climate data: kg CO2e/kg → kg CO2e per functional unit.

Boverket provides climate data in kg CO2e/kg. AIda needs values per functional
unit (m2, st, lm) to match how renovation projects are described.

Conversion formula for area-based products:
    CO2e/m2 = CO2e/kg × density (kg/m3) × thickness (m)

For count-based products (windows, doors, elevators):
    CO2e/st = CO2e/kg × typical_weight_kg

For linear products (ventilation ducts, pipes):
    CO2e/lm = CO2e/kg × weight_per_meter (kg/m)

Typical values below are defaults for common renovation scenarios.
JJ/project team can adjust these based on actual project specifications.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class ConversionSpec:
    """Specification for converting kg CO2e/kg to a functional unit."""
    target_unit: str          # "m2", "st", "lm"
    method: str               # "area", "count", "linear", "direct"
    typical_thickness_m: float = 0.0    # for area-based (m)
    typical_weight_kg: float = 0.0      # for count-based (kg/st) or linear (kg/m)
    description: str = ""


# Typical thicknesses and weights for renovation scenarios in Swedish buildings.
# Sources: Boverkets klimatdatabas, NollCO2 Manual 1.2, LFM30 beräkningsanvisning.
#
# These are DEFAULTS. Actual projects should specify their own values.
# JJ: Review and adjust based on fastighetsavdelningens typiska val.

COMPONENT_CONVERSIONS: dict[str, ConversionSpec] = {
    # --- Area-based (CO2e/m2 = CO2e/kg × density × thickness) ---

    "golv": ConversionSpec(
        target_unit="m2",
        method="area",
        typical_thickness_m=0.015,  # 15mm vinyl/linoleum/parkett
        description="Golvbeläggning, 15mm typisk tjocklek",
    ),
    "innervägg": ConversionSpec(
        target_unit="m2",
        method="area",
        typical_thickness_m=0.013,  # 13mm gipsskiva (en sida)
        description="Gipsskiva innervägg, 13mm standard",
    ),
    "yttervägg": ConversionSpec(
        target_unit="m2",
        method="area",
        typical_thickness_m=0.200,  # 200mm komplett vägguppbyggnad
        description="Yttervägg komplett, ~200mm",
    ),
    "betongvägg": ConversionSpec(
        target_unit="m2",
        method="area",
        typical_thickness_m=0.200,  # 200mm betongvägg
        description="Betongvägg, 200mm typisk",
    ),
    "tak": ConversionSpec(
        target_unit="m2",
        method="area",
        typical_thickness_m=0.025,  # 25mm takpannor
        description="Takbeläggning, 25mm",
    ),
    "isolering": ConversionSpec(
        target_unit="m2",
        method="area",
        typical_thickness_m=0.200,  # 200mm isolering
        description="Tilläggsisolering, 200mm",
    ),

    # --- Count-based (CO2e/st = CO2e/kg × typisk vikt) ---

    "fönster": ConversionSpec(
        target_unit="st",
        method="count",
        typical_weight_kg=40.0,  # ~40 kg per standardfönster 1.2x1.2m
        description="Standardfönster ~1.2x1.2m, ~40 kg",
    ),
    "dörr": ConversionSpec(
        target_unit="st",
        method="count",
        typical_weight_kg=25.0,  # ~25 kg innerdörr
        description="Innerdörr standard, ~25 kg",
    ),
    "storköksutrustning": ConversionSpec(
        target_unit="st",
        method="count",
        typical_weight_kg=80.0,
        description="Storköksutrustning, ~80 kg",
    ),
    "sanitet": ConversionSpec(
        target_unit="st",
        method="count",
        typical_weight_kg=15.0,
        description="Sanitetsprodukt (toalett/handfat), ~15 kg",
    ),
    "vitvaror": ConversionSpec(
        target_unit="st",
        method="count",
        typical_weight_kg=50.0,
        description="Vitvara (tvättmaskin/köksfläkt), ~50 kg",
    ),
    "kylanläggning": ConversionSpec(
        target_unit="st",
        method="count",
        typical_weight_kg=200.0,
        description="Kylsystem, ~200 kg",
    ),
    "hiss": ConversionSpec(
        target_unit="st",
        method="count",
        typical_weight_kg=3000.0,
        description="Personhiss komplett, ~3000 kg",
    ),
    "belysning": ConversionSpec(
        target_unit="st",
        method="count",
        typical_weight_kg=3.0,
        description="LED-armatur, ~3 kg",
    ),

    # --- Linear (CO2e/lm = CO2e/kg × kg/m) ---

    "ventilation": ConversionSpec(
        target_unit="lm",
        method="linear",
        typical_weight_kg=5.0,  # ~5 kg/m stålkanal
        description="Ventilationskanal stål, ~5 kg/m",
    ),
}


def convert_to_functional_unit(
    co2e_per_kg: float,
    component_key: str,
    density_kg_m3: float | None = None,
) -> tuple[float, str]:
    """Convert kg CO2e/kg to CO2e per functional unit.

    Args:
        co2e_per_kg: Climate impact in kg CO2e per kg of material
        component_key: AIda component category (e.g. "golv", "fönster")
        density_kg_m3: Material density from Boverket (optional, used for area method)

    Returns:
        Tuple of (co2e_per_unit, unit_string)
        Falls back to (co2e_per_kg, "kg") if no conversion is defined.
    """
    spec = COMPONENT_CONVERSIONS.get(component_key)
    if not spec:
        return co2e_per_kg, "kg"

    if spec.method == "area":
        if density_kg_m3 and density_kg_m3 > 0 and spec.typical_thickness_m > 0:
            co2e_per_m2 = co2e_per_kg * density_kg_m3 * spec.typical_thickness_m
            return round(co2e_per_m2, 2), "m2"
        return co2e_per_kg, "kg"  # can't convert without density

    elif spec.method == "count":
        if spec.typical_weight_kg > 0:
            co2e_per_st = co2e_per_kg * spec.typical_weight_kg
            return round(co2e_per_st, 1), "st"
        return co2e_per_kg, "kg"

    elif spec.method == "linear":
        if spec.typical_weight_kg > 0:
            co2e_per_lm = co2e_per_kg * spec.typical_weight_kg
            return round(co2e_per_lm, 2), "lm"
        return co2e_per_kg, "kg"

    return co2e_per_kg, "kg"


def get_density_from_extra(extra_json: str) -> float | None:
    """Extract density from a cache entry's extra_json field."""
    if not extra_json:
        return None
    try:
        extra = json.loads(extra_json)
        return extra.get("density_kg_m3")
    except (json.JSONDecodeError, TypeError):
        return None


# Typical densities for common building materials (kg/m3).
# Used as fallback when EPD/Boverket doesn't provide density.
# Sources: Boverkets klimatdatabas, IVL, materialhandböcker.
TYPICAL_DENSITIES: dict[str, float] = {
    "golv": 1400,       # vinyl/linoleum ~1300-1500
    "innervägg": 800,   # gipsskiva ~700-900
    "yttervägg": 1800,  # tegel+puts ~1600-2000
    "betongvägg": 2400, # betong C30/37
    "tak": 2100,        # betongpannor ~2000-2200
    "isolering": 30,    # mineralull/glasull ~20-40
}


def get_density_for_component(
    component_key: str,
    extra_json: str = "",
) -> float | None:
    """Get density for a component, trying extra_json first, then typical values.

    Checks:
    1. density_kg_m3 in extra_json (from Boverket API)
    2. Typical density lookup by component key
    """
    # Try explicit density from data source
    density = get_density_from_extra(extra_json)
    if density and density > 0:
        return density

    # Fall back to typical density
    return TYPICAL_DENSITIES.get(component_key)
