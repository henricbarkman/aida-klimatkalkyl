#!/usr/bin/env python3
"""Build EPD alternatives catalog from Environdec index + external EPDs.

Automated pipeline:
1. Load Environdec index (cached locally)
2. Search per category using expanded keywords
3. Fetch EPD details (GWP A1-A3) via API, with SQLite cache
4. Validate: reject null GWP, expired, misclassified, outliers
5. Unit conversion where possible (kg → m2/st/lm)
6. Merge external EPDs (Forbo, Swedoor, etc.)
7. Write output to epd_alternatives.json

Usage:
    python aida/scripts/build_epd_alternatives.py [--dry-run] [--category golv]
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from aida.data.environdec_client import EnvirondecClient, EPDSummary
from aida.data.unit_conversion import (
    COMPONENT_CONVERSIONS,
    TYPICAL_DENSITIES,
    convert_to_functional_unit,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DATA_DIR = PROJECT_ROOT / "src" / "aida" / "data"
OUTPUT_PATH = DATA_DIR / "epd_alternatives.json"
EXTERNAL_PATH = DATA_DIR / "external_epds.json"
CACHE_DB_PATH = DATA_DIR / "epd_detail_cache.db"

# Per-category search queries. Each category gets multiple queries
# to cast a wide net. Results are deduplicated by UUID.
CATEGORY_QUERIES: dict[str, list[str]] = {
    "golv": ["floor", "flooring", "vinyl", "linoleum", "parquet", "laminate",
             "carpet", "epoxy", "terrazzo", "rubber floor", "bamboo floor", "cork floor"],
    "innervägg": ["plasterboard", "gypsum board", "drywall", "partition wall",
                  "fibre board", "acoustic panel", "interior wall board"],
    "yttervägg": ["facade", "brick", "cladding", "exterior wall", "curtain wall",
                  "fibre cement", "sandwich panel", "render"],
    "betongvägg": ["concrete wall", "precast concrete", "reinforced concrete",
                   "concrete element", "betong"],
    "fönster": ["window", "glass", "glazing", "triple glass", "double glass"],
    "tak": ["roof", "roofing", "roof tile", "membrane", "bitumen",
            "sedum roof", "green roof", "slate roof", "roof panel"],
    "isolering": ["insulation", "mineral wool", "glass wool", "stone wool",
                  "cellulose insulation", "eps", "xps", "polyurethane", "pir",
                  "hemp insulation"],
    "dörr": ["door", "interior door", "wooden door", "fire door", "steel door",
             "door leaf"],
    "hiss": ["elevator", "lift", "escalator", "passenger lift"],
    "belysning": ["luminaire", "lighting", "led", "downlight", "spotlight",
                  "lamp", "light fixture"],
    "ventilation": ["ventilation", "duct", "air handling", "ahu", "damper",
                    "grille", "diffuser", "fan", "ventilationskanal"],
    "diskmaskin": ["dishwasher", "commercial kitchen", "kitchen equipment",
                   "cooker hood", "storkök"],
    "kylanläggning": ["chiller", "heat pump", "cooling", "air condition",
                      "fan coil", "hvac", "compressor", "condenser", "refriger"],
}

# Target counts per category
TARGETS = {
    "abundant": 30,   # most categories
    "moderate": 25,   # dörr, innervägg, betongvägg, hiss
    "scarce": 99,     # take everything (diskmaskin, kylanläggning)
}

CATEGORY_TIER: dict[str, str] = {
    "diskmaskin": "scarce",
    "kylanläggning": "scarce",
    "dörr": "moderate",
    "innervägg": "moderate",
    "betongvägg": "moderate",
    "hiss": "moderate",
}

# Reject EPDs containing these strings (misclassified products)
REJECT_PATTERNS: dict[str, list[str]] = {
    "diskmaskin": ["hose", "tube", "pipe", "cable", "connector", "valve",
                   "bracket", "screw", "seal", "gasket"],
    "kylanläggning": ["pipe", "tube", "cable", "wire"],
}

# GWP outlier thresholds (per declared unit, not functional)
MAX_GWP_PER_UNIT: dict[str, float] = {
    "golv": 200,
    "innervägg": 500,
    "yttervägg": 500,
    "betongvägg": 500,
    "fönster": 500,
    "tak": 300,
    "isolering": 100,
    "dörr": 500,
    "hiss": 50000,
    "belysning": 200,
    "ventilation": 300,
    "diskmaskin": 2000,
    "kylanläggning": 5000,
}


def init_cache_db(db_path: Path) -> sqlite3.Connection:
    """Initialize SQLite cache for EPD detail fetches."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS epd_details (
            uuid TEXT PRIMARY KEY,
            fetched_at REAL,
            gwp_fossil REAL,
            gwp_total REAL,
            gwp_biogenic REAL,
            declared_unit TEXT,
            owner TEXT,
            reg_no TEXT,
            name TEXT,
            geo TEXT,
            raw_json TEXT
        )
    """)
    conn.commit()
    return conn


def get_cached_detail(conn: sqlite3.Connection, uuid: str) -> dict | None:
    """Get cached EPD detail if fresh (< 30 days)."""
    row = conn.execute(
        "SELECT * FROM epd_details WHERE uuid = ?", (uuid,)
    ).fetchone()
    if not row:
        return None
    fetched_at = row[1]
    if time.time() - fetched_at > 30 * 86400:
        return None  # stale
    return {
        "uuid": row[0],
        "gwp_fossil": row[2],
        "gwp_total": row[3],
        "gwp_biogenic": row[4],
        "declared_unit": row[5],
        "owner": row[6],
        "reg_no": row[7],
        "name": row[8],
        "geo": row[9],
    }


def cache_detail(conn: sqlite3.Connection, uuid: str, detail: dict) -> None:
    """Cache an EPD detail fetch result."""
    conn.execute(
        """INSERT OR REPLACE INTO epd_details
           (uuid, fetched_at, gwp_fossil, gwp_total, gwp_biogenic,
            declared_unit, owner, reg_no, name, geo, raw_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            uuid,
            time.time(),
            detail.get("gwp_fossil"),
            detail.get("gwp_total"),
            detail.get("gwp_biogenic"),
            detail.get("declared_unit", "kg"),
            detail.get("owner", ""),
            detail.get("reg_no", ""),
            detail.get("name", ""),
            detail.get("geo", ""),
            json.dumps(detail, ensure_ascii=False),
        ),
    )
    conn.commit()


def search_candidates(
    client: EnvirondecClient,
    category: str,
    queries: list[str],
    max_per_query: int = 30,
) -> list[EPDSummary]:
    """Search index with multiple queries, dedup by UUID."""
    seen_uuids: set[str] = set()
    candidates: list[EPDSummary] = []

    for query in queries:
        results = client.search_index(
            query=query,
            component_hint=category,
            max_results=max_per_query,
        )
        for epd in results:
            if epd.uuid not in seen_uuids:
                seen_uuids.add(epd.uuid)
                candidates.append(epd)

    logger.info("  %s: %d unique candidates from %d queries",
                category, len(candidates), len(queries))
    return candidates


def fetch_and_validate(
    client: EnvirondecClient,
    conn: sqlite3.Connection,
    candidates: list[EPDSummary],
    category: str,
    target_count: int,
) -> list[dict]:
    """Fetch EPD details, validate, and return clean entries."""
    reject_patterns = REJECT_PATTERNS.get(category, [])
    max_gwp = MAX_GWP_PER_UNIT.get(category, 10000)
    valid_entries: list[dict] = []
    api_calls = 0
    cache_hits = 0

    for epd in candidates:
        # Check cache first
        cached = get_cached_detail(conn, epd.uuid)
        if cached:
            cache_hits += 1
            detail_dict = cached
        else:
            # Fetch from API
            detail = client.fetch_epd_detail(epd.uuid, epd.version)
            if not detail:
                continue
            api_calls += 1
            detail_dict = {
                "uuid": detail.uuid,
                "gwp_fossil": detail.gwp_fossil_a1a3,
                "gwp_total": detail.gwp_total_a1a3,
                "gwp_biogenic": detail.gwp_biogenic_a1a3,
                "declared_unit": detail.declared_unit,
                "owner": detail.owner or epd.owner,
                "reg_no": detail.reg_no or epd.reg_no,
                "name": detail.name or epd.name,
                "geo": detail.geo or epd.geo,
            }
            cache_detail(conn, epd.uuid, detail_dict)

            # Rate limiting
            if api_calls % 10 == 0:
                time.sleep(1)

        # --- Validation ---
        gwp_fossil = detail_dict.get("gwp_fossil")
        gwp_total = detail_dict.get("gwp_total")
        # Prefer fossil, but accept total. Use 'is not None' to handle 0.0
        gwp = gwp_fossil if gwp_fossil is not None else gwp_total
        if gwp is None:
            continue  # no GWP data

        # Reject negative fossil GWP (suspicious unless bio-based floor)
        if gwp < 0 and category not in ("golv",):
            continue

        # Reject outliers
        if abs(gwp) > max_gwp:
            continue

        # Reject misclassified products
        name_lower = detail_dict.get("name", "").lower()
        if any(pat in name_lower for pat in reject_patterns):
            continue

        # Reject expired EPDs (valid_until is a year, not timestamp)
        current_year = time.gmtime().tm_year
        if epd.valid_until and epd.valid_until < current_year:
            continue

        # Build entry
        unit = detail_dict.get("declared_unit", "kg")

        # Attempt functional unit conversion
        gwp_per_fu = None
        functional_unit = None
        spec = COMPONENT_CONVERSIONS.get(category)
        if spec and unit == "kg":
            density = TYPICAL_DENSITIES.get(category)
            converted_val, converted_unit = convert_to_functional_unit(
                gwp, category, density
            )
            if converted_unit != "kg":
                gwp_per_fu = round(converted_val, 2)
                functional_unit = converted_unit

        entry = {
            "category": category,
            "name": detail_dict.get("name", epd.name),
            "owner": detail_dict.get("owner", epd.owner),
            "gwp_a1a3": round(gwp, 2),
            "unit": unit,
            "geo": detail_dict.get("geo", epd.geo),
            "reg_no": detail_dict.get("reg_no", epd.reg_no),
            "uuid": epd.uuid,
            "source_registry": "environdec",
        }

        if gwp_per_fu is not None:
            entry["gwp_per_functional_unit"] = gwp_per_fu
            entry["functional_unit"] = functional_unit

        valid_entries.append(entry)

    logger.info("  %s: %d valid (API: %d, cache: %d, target: %d)",
                category, len(valid_entries), api_calls, cache_hits,
                target_count)

    # Sort by geographic preference then GWP
    geo_rank = {"SE": 0, "NORD": 1, "DK": 2, "NO": 2, "FI": 2, "RER": 3, "GLO": 4}
    valid_entries.sort(key=lambda e: (
        geo_rank.get(e["geo"], 5),
        abs(e["gwp_a1a3"]),
    ))

    return valid_entries[:target_count]


def load_external_epds() -> list[dict]:
    """Load manually curated external EPDs."""
    if not EXTERNAL_PATH.exists():
        logger.warning("No external EPDs file at %s", EXTERNAL_PATH)
        return []
    try:
        with open(EXTERNAL_PATH) as f:
            data = json.load(f)
        logger.info("Loaded %d external EPDs", len(data))

        # Attempt unit conversion for external EPDs too
        for entry in data:
            category = entry.get("category", "")
            unit = entry.get("unit", "kg")
            gwp = entry.get("gwp_a1a3", 0)
            spec = COMPONENT_CONVERSIONS.get(category)

            if spec and unit == "kg" and "gwp_per_functional_unit" not in entry:
                density = TYPICAL_DENSITIES.get(category)
                converted_val, converted_unit = convert_to_functional_unit(
                    gwp, category, density
                )
                if converted_unit != "kg":
                    entry["gwp_per_functional_unit"] = round(converted_val, 2)
                    entry["functional_unit"] = converted_unit

            if "source_registry" not in entry:
                entry["source_registry"] = "manual"

        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load external EPDs: %s", e)
        return []


def build_catalog(
    categories: list[str] | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Build the complete EPD alternatives catalog."""
    client = EnvirondecClient()
    conn = init_cache_db(CACHE_DB_PATH)

    all_categories = list(CATEGORY_QUERIES.keys())
    if categories:
        all_categories = [c for c in categories if c in CATEGORY_QUERIES]

    all_entries: list[dict] = []

    for category in all_categories:
        queries = CATEGORY_QUERIES[category]
        tier = CATEGORY_TIER.get(category, "abundant")
        target = TARGETS[tier]

        logger.info("Processing: %s (tier=%s, target=%d)", category, tier, target)

        candidates = search_candidates(client, category, queries)

        if dry_run:
            logger.info("  [dry-run] Would fetch %d candidates", len(candidates))
            continue

        entries = fetch_and_validate(
            client, conn, candidates, category, target
        )
        all_entries.extend(entries)

    # Merge external EPDs
    if not dry_run:
        external = load_external_epds()
        existing_uuids = {e.get("uuid", "") for e in all_entries}
        for ext in external:
            if categories and ext.get("category") not in categories:
                continue
            ext_uuid = ext.get("uuid", "")
            if not ext_uuid:
                logger.warning("External EPD missing uuid: %s", ext.get("name", "?"))
                continue
            if ext_uuid not in existing_uuids:
                all_entries.append(ext)
                existing_uuids.add(ext_uuid)

    conn.close()

    # Summary
    cat_counts: dict[str, int] = {}
    for e in all_entries:
        cat = e["category"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    logger.info("\n=== Build Summary ===")
    for cat in sorted(cat_counts):
        logger.info("  %-16s %3d entries", cat, cat_counts[cat])
    logger.info("  %-16s %3d entries", "TOTAL", len(all_entries))

    fu_count = sum(1 for e in all_entries if e.get("gwp_per_functional_unit"))
    logger.info("  Functional unit conversions: %d/%d (%.0f%%)",
                fu_count, len(all_entries),
                fu_count / len(all_entries) * 100 if all_entries else 0)

    return all_entries


def main():
    parser = argparse.ArgumentParser(description="Build EPD alternatives catalog")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only search index, don't fetch details")
    parser.add_argument("--category", type=str, default="",
                        help="Process only this category (comma-separated)")
    parser.add_argument("--output", type=str, default="",
                        help="Output file path (default: epd_alternatives.json)")
    args = parser.parse_args()

    categories = [c.strip() for c in args.category.split(",") if c.strip()] or None
    output_path = Path(args.output) if args.output else OUTPUT_PATH

    entries = build_catalog(categories=categories, dry_run=args.dry_run)

    if not args.dry_run and entries:
        # Sort by category then name for readability
        entries.sort(key=lambda e: (e["category"], e["name"]))

        with open(output_path, "w") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        size_kb = output_path.stat().st_size / 1024
        logger.info("Wrote %d entries to %s (%.1f KB)", len(entries), output_path, size_kb)


if __name__ == "__main__":
    main()
