"""SQLite cache for climate data lookups.

On Vercel (serverless), the deploy bundle is read-only. The cache DB is
copied to /tmp on first access for read-write support. The /tmp copy
persists within a warm lambda instance but is lost on cold starts.
Pre-populated data (Boverket sync) is bundled in the deploy.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

DB_PATH = Path(__file__).parent / "climate_cache.db"
_VERCEL = bool(os.environ.get("VERCEL"))


def _resolve_writable_path(source: Path) -> Path:
    """On Vercel, copy bundled DB to /tmp for write access."""
    if not _VERCEL:
        return source

    tmp_path = Path("/tmp") / source.name
    if not tmp_path.exists() and source.exists():
        shutil.copy2(source, tmp_path)
    return tmp_path


# TTL in seconds
TTL_BOVERKET = 30 * 24 * 3600   # 30 days
TTL_LOCAL = 365 * 24 * 3600     # 1 year (static data)
TTL_ENVIRONDEC = 30 * 24 * 3600 # 30 days
TTL_LLM = 7 * 24 * 3600        # 7 days
TTL_PRICING = 30 * 24 * 3600   # 30 days


@dataclass
class CacheEntry:
    product_name: str
    name: str
    co2e_per_unit: float
    cost_per_unit: float
    unit: str
    source: str
    source_layer: str  # "boverket", "environdec", "llm"
    fetched_at: float
    expires_at: float
    extra_json: str = ""
    price_enriched: int = 0


class ClimateCache:
    def __init__(self, db_path: Path | str | None = None):
        source_path = Path(db_path) if db_path else DB_PATH
        self.db_path = _resolve_writable_path(source_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_table()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_table(self) -> None:
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS climate_cache (
                product_name TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                co2e_per_unit REAL NOT NULL,
                cost_per_unit REAL NOT NULL,
                unit TEXT NOT NULL,
                source TEXT NOT NULL,
                confidence TEXT NOT NULL DEFAULT '',
                source_layer TEXT NOT NULL DEFAULT 'boverket',
                fetched_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                extra_json TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS boverket_categories (
                boverket_category TEXT PRIMARY KEY,
                aida_component TEXT NOT NULL
            )
        """)
        conn.commit()
        # Add price_enriched column if missing (migration)
        try:
            conn.execute("ALTER TABLE climate_cache ADD COLUMN price_enriched INTEGER DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

    def get(self, product_name: str) -> CacheEntry | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT product_name, name, co2e_per_unit, cost_per_unit, unit, "
            "source, source_layer, fetched_at, expires_at, extra_json, price_enriched "
            "FROM climate_cache WHERE product_name = ?",
            (product_name.lower().strip(),),
        ).fetchone()
        if row is None:
            return None
        entry = CacheEntry(**dict(row))
        if entry.expires_at < time.time():
            return None  # expired
        return entry

    def put(self, entry: CacheEntry) -> None:
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO climate_cache
                (product_name, name, co2e_per_unit, cost_per_unit, unit,
                 source, source_layer, fetched_at, expires_at, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.product_name.lower().strip(),
            entry.name,
            entry.co2e_per_unit,
            entry.cost_per_unit,
            entry.unit,
            entry.source,
            entry.source_layer,
            entry.fetched_at,
            entry.expires_at,
            entry.extra_json,
        ))
        conn.commit()

    def put_many(self, entries: list[CacheEntry]) -> None:
        conn = self._get_conn()
        conn.executemany("""
            INSERT OR REPLACE INTO climate_cache
                (product_name, name, co2e_per_unit, cost_per_unit, unit,
                 source, source_layer, fetched_at, expires_at, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                e.product_name.lower().strip(), e.name, e.co2e_per_unit,
                e.cost_per_unit, e.unit, e.source,
                e.source_layer, e.fetched_at, e.expires_at, e.extra_json,
            )
            for e in entries
        ])
        conn.commit()

    def count(self, source_layer: str | None = None) -> int:
        conn = self._get_conn()
        if source_layer:
            row = conn.execute(
                "SELECT COUNT(*) FROM climate_cache WHERE source_layer = ?",
                (source_layer,),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM climate_cache").fetchone()
        return row[0]

    def clear(self, source_layer: str | None = None) -> int:
        conn = self._get_conn()
        if source_layer:
            cursor = conn.execute(
                "DELETE FROM climate_cache WHERE source_layer = ?",
                (source_layer,),
            )
        else:
            cursor = conn.execute("DELETE FROM climate_cache")
        conn.commit()
        return cursor.rowcount

    def put_category_mappings(self, mappings: dict[str, str]) -> None:
        """Store Boverket category → AIda component key mappings."""
        conn = self._get_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO boverket_categories "
            "(boverket_category, aida_component) VALUES (?, ?)",
            [(k.lower().strip(), v) for k, v in mappings.items()],
        )
        conn.commit()

    def get_aida_component(self, boverket_category: str) -> str | None:
        """Look up AIda component key for a Boverket category."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT aida_component FROM boverket_categories WHERE boverket_category = ?",
            (boverket_category.lower().strip(),),
        ).fetchone()
        return row[0] if row else None

    def get_categories_for_aida_key(self, aida_key: str) -> list[str]:
        """Get all Boverket categories that map to an AIda component key."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT boverket_category FROM boverket_categories WHERE aida_component = ?",
            (aida_key,),
        ).fetchall()
        return [r[0] for r in rows]

    def update_cost(self, product_name: str, cost_per_unit: float) -> bool:
        """Update cost_per_unit and mark as price-enriched. Returns True if row existed."""
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE climate_cache SET cost_per_unit = ?, price_enriched = 1 WHERE product_name = ?",
            (cost_per_unit, product_name.lower().strip()),
        )
        conn.commit()
        return cursor.rowcount > 0

    def get_all_boverket(self) -> list[CacheEntry]:
        """Return all unique Boverket products (deduplicated by name)."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT product_name, name, co2e_per_unit, cost_per_unit, unit, "
            "source, source_layer, fetched_at, expires_at, extra_json, price_enriched "
            "FROM climate_cache WHERE source_layer = 'boverket' "
            "GROUP BY name ORDER BY name",
        ).fetchall()
        return [CacheEntry(**dict(r)) for r in rows]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
