"""
═══════════════════════════════════════════════════════════════════
🎭 staff_unmask.py — De-mask staff codes สำหรับ UI display
═══════════════════════════════════════════════════════════════════

Architecture:
  - Supabase  : เก็บ masked codes (SURG_001, SCRUB_002, CIRC_003) → PDPA-safe
  - App display: ใช้ module นี้แปลงกลับเป็นชื่อจริง ตอน render UI

Mapping file (gitignored, local only):
  C:\\Dev\\train_model_ORM\\staff_mapping.csv

Behavior:
  - ถ้า mapping file มีอยู่   → unmask (ใช้ใน local dev / hospital workstation)
  - ถ้า mapping file ไม่มี    → no-op (ใช้ใน Streamlit Cloud deploy → แสดง SURG_xxx)
  - ถ้า DB เป็น SQLite (real names) → mapping miss → no-op (return as-is)

Public API:
  unmask(value)                 → single value (SURG_001 → 'พ.ต.อ.หญิง...')
  unmask_multi(value)           → 'SCRUB_001, SCRUB_002' → 'name1, name2'
  unmask_series(series)         → pandas Series
  apply_to_dataframe(df, cols)  → in-place unmask of known columns
  is_available()                → True ถ้า mapping file โหลดได้
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable, Optional

# ─── Config ─────────────────────────────────────────────────────────
_MAPPING_PATH = Path(__file__).resolve().parent / "staff_mapping.csv"

# Pattern จับ masked code (SURG_001, SCRUB_012, CIRC_005, ฯลฯ)
_CODE_PATTERN = re.compile(r"\b(SURG|SCRUB|CIRC)_\d{2,5}\b")

# Cache (load ครั้งเดียว — file ไม่เปลี่ยนระหว่าง session)
_cache: Optional[dict] = None


def _load_mapping() -> dict[str, str]:
    """Load mapping CSV → {masked_code: original_name}"""
    global _cache
    if _cache is not None:
        return _cache
    if not _MAPPING_PATH.exists():
        _cache = {}
        return _cache
    mp: dict[str, str] = {}
    try:
        with open(_MAPPING_PATH, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (row.get("masked_code") or "").strip()
                name = (row.get("original_name") or "").strip()
                if code and name:
                    mp[code] = name
    except Exception:
        pass  # silent fallback
    _cache = mp
    return mp


def reload_mapping() -> int:
    """Force reload mapping file — returns count loaded"""
    global _cache
    _cache = None
    return len(_load_mapping())


def is_available() -> bool:
    """True ถ้ามี mapping file ที่ load ได้"""
    return bool(_load_mapping())


# ─── Single-value unmask ────────────────────────────────────────────
def unmask(value):
    """SURG_001 → 'พ.ต.อ.หญิง...'  (no-op ถ้าหาไม่เจอ)"""
    if not isinstance(value, str) or not value:
        return value
    return _load_mapping().get(value.strip(), value)


def unmask_multi(value):
    """'SCRUB_001, SCRUB_002' → 'name1, name2'

    ฉลาด: replace ทุก token ที่ match SURG_xxx/SCRUB_xxx/CIRC_xxx
    คงเครื่องหมายคั่นไว้ (comma, space ฯลฯ)
    """
    if not isinstance(value, str) or not value:
        return value
    mp = _load_mapping()
    if not mp:
        return value
    return _CODE_PATTERN.sub(lambda m: mp.get(m.group(0), m.group(0)), value)


# ─── Pandas helpers ─────────────────────────────────────────────────
def unmask_series(series):
    """pandas Series of values (handles NaN, mix types)"""
    mp = _load_mapping()
    if not mp:
        return series
    try:
        return series.map(lambda v: unmask_multi(v) if isinstance(v, str) else v)
    except Exception:
        return series


# Standard columns to auto-unmask
DEFAULT_COLUMNS = (
    "surgeon_name",
    "scheduled_surgeon",
    "scrub_nurse",
    "circ_nurse",
    "surgeon",          # alias used in some queries (e.g. get_surgeon_list)
    "name_surgeon",     # alternate naming
    "nurse",            # generic nurse column
)


def apply_to_dataframe(df, columns: Optional[Iterable[str]] = None):
    """In-place unmask known columns in a DataFrame. Returns the DF.

    Usage:
        df = pd.read_sql_query("SELECT surgeon_name, ... FROM cases", conn)
        df = apply_to_dataframe(df)  # auto-unmask known columns
    """
    if df is None:
        return df
    try:
        if df.empty:
            return df
    except AttributeError:
        return df

    if not _load_mapping():
        return df

    cols_to_check = columns if columns is not None else DEFAULT_COLUMNS
    for col in cols_to_check:
        if col in df.columns:
            df[col] = unmask_series(df[col])
    return df


# ─── Diagnostic ─────────────────────────────────────────────────────
def info() -> dict:
    mp = _load_mapping()
    by_role: dict[str, int] = {"SURG": 0, "SCRUB": 0, "CIRC": 0}
    for code in mp:
        prefix = code.split("_")[0] if "_" in code else "?"
        by_role[prefix] = by_role.get(prefix, 0) + 1
    return {
        "mapping_path": str(_MAPPING_PATH),
        "exists": _MAPPING_PATH.exists(),
        "loaded": len(mp),
        "by_role": by_role,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(info(), ensure_ascii=False, indent=2))
    # Quick smoke
    for v in ("SURG_001", "SCRUB_001, SCRUB_002", "no_match", None, 42):
        print(f"  {v!r:35s} → {unmask_multi(v)!r}")
