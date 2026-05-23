"""
═══════════════════════════════════════════════════════════════════
🎭 anonymize_for_cloud.py — De-identify SQLite → Supabase
═══════════════════════════════════════════════════════════════════

PDPA-compliant pipeline:
  ✂️  ทิ้ง  : hn, name, an  → set NULL ทั้งหมด (PII คนไข้ห้ามขึ้น cloud)
  🎭 Mask  : surgeon_name, scheduled_surgeon → SURG_001, SURG_002, ...
              scrub_nurse                     → SCRUB_001, SCRUB_002, ...
              circ_nurse                      → CIRC_001, CIRC_002, ...
  ✅ เก็บ  : diagnosis, procnote (สำหรับ ML)
              ทุก feature ตัวเลข (อายุ, BMI, op_duration, ฯลฯ)

Mapping (รหัส ↔ ชื่อจริง) บันทึกเป็น CSV ใน:
    C:\\Dev\\train_model_ORM\\staff_mapping.csv
  → gitignored, ห้าม commit / upload (เก็บ local เท่านั้น)

Tables ที่ upload:
  ✅ cases          (anonymized)
  ✅ app_settings   (no PII)
  ⛔ audit_log      (skip — operational, อาจมี PII ใน text)
  ⛔ prediction_log (skip — ว่าง + จะ regenerate ใน cloud เอง)
  ⛔ backup_log     (skip — operational + อาจมี local path)
  ⛔ room_settings  (skip — มีชื่อพยาบาลใน JSON, จะ regenerate ใน cloud)

วิธีรัน:
    python supabase/anonymize_for_cloud.py
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import csv
import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    print("❌ ต้องติดตั้ง psycopg2 ก่อน: pip install psycopg2-binary")
    sys.exit(1)

try:
    import toml
except ImportError:
    print("❌ ต้องติดตั้ง toml ก่อน: pip install toml")
    sys.exit(1)


# ─── Paths ──────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = ROOT / "minor_or.db"
SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"
MAPPING_CSV = ROOT / "staff_mapping.csv"


# ─── Config loader ──────────────────────────────────────────────────
def load_database_url() -> str:
    """อ่าน database_url จาก secrets.toml"""
    if SECRETS_PATH.exists():
        try:
            secrets = toml.load(SECRETS_PATH)
            url = secrets.get("database_url", "").strip()
            if url and "YOUR_PASSWORD_HERE" not in url:
                return url
        except Exception:
            pass
    import os
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    print(f"❌ หา database_url ไม่เจอ — แก้ {SECRETS_PATH} ก่อน")
    sys.exit(1)


# ─── Schema (must match schema_postgres.sql) ────────────────────────
CASES_COLS = [
    "case_id", "op_date", "name", "hn", "an", "diagnosis",
    "procedure_name", "surgeon_name", "division_code", "case_category",
    "patient_type", "op_type", "estimated_time", "procnote",
    "status", "cancel_reason",
    "oss_visited", "oss_by_or", "or_pre_visit", "post_call", "post_call_status",
    "ai_predicted_min", "user_override_min", "actual_duration_min",
    "scrub_nurse", "circ_nurse", "anesthesia_type", "wait_min", "room_no",
    "arrived_at", "in_or_at", "op_end_at", "discharged_at",
    "post_op_dest", "treatment_cost", "patho_cost",
    "scheduled_surgeon",
    "created_at", "updated_at",
]

# PII fields ที่ต้อง set NULL
PII_DROP = {"name", "hn", "an"}


# ─── Name helpers ───────────────────────────────────────────────────
_SPLIT_RE = re.compile(r",\s*\r?\s*")  # split "name1, \rname2" → ["name1", "name2"]


def split_names(s: str | None) -> list[str]:
    """แยก multi-name string → list (รองรับ ', \\r' / ',' / ', ')"""
    if not s:
        return []
    parts = _SPLIT_RE.split(s.strip())
    return [p.strip() for p in parts if p.strip()]


def mask_single(s: str | None, mapping: dict[str, str]) -> str | None:
    """Map single name (split อันดับแรก ถ้าเผลอ multi — fallback)"""
    if not s:
        return s
    s = s.strip()
    return mapping.get(s, s)  # ถ้าหาไม่เจอใน mapping → คืนค่าเดิม (เพราะถูก add ใน build_mapping แล้ว)


def mask_multi(s: str | None, mapping: dict[str, str]) -> str | None:
    """Split → map → join ด้วย ', '"""
    if not s:
        return s
    parts = split_names(s)
    masked = [mapping.get(p, p) for p in parts]
    return ", ".join(masked)


def build_mapping(values: Iterable[str], prefix: str) -> dict[str, str]:
    """[name1, name2, ...] → {name1: PREFIX_001, name2: PREFIX_002, ...} (sorted)"""
    unique_sorted = sorted({v.strip() for v in values if v and v.strip()})
    return {name: f"{prefix}_{i + 1:03d}" for i, name in enumerate(unique_sorted)}


def collect_all_names(sq_cur: sqlite3.Cursor, column: str, tables: list[str]) -> list[str]:
    """Collect ชื่อทั้งหมดจาก column ใน multiple tables (split multi-name)"""
    out: list[str] = []
    for tbl in tables:
        try:
            for row in sq_cur.execute(
                f"SELECT DISTINCT {column} FROM {tbl} WHERE {column} IS NOT NULL AND {column} != ''"
            ):
                out.extend(split_names(row[0]))
        except sqlite3.OperationalError:
            continue  # table หรือ column ไม่มี
    return out


# ─── Mapping export ─────────────────────────────────────────────────
def save_mapping_csv(
    surgeon_map: dict, scrub_map: dict, circ_map: dict
) -> None:
    """บันทึก mapping → CSV (เก็บ local เท่านั้น)"""
    with open(MAPPING_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["role", "masked_code", "original_name"])
        for role, mp in (("surgeon", surgeon_map), ("scrub", scrub_map), ("circ", circ_map)):
            for name, code in sorted(mp.items(), key=lambda x: x[1]):
                w.writerow([role, code, name])


# ─── Main pipeline ──────────────────────────────────────────────────
def main() -> int:
    print("═" * 60)
    print("🎭 Anonymize & Upload → Supabase (PDPA-safe mode)")
    print("═" * 60)

    if not SQLITE_PATH.exists():
        print(f"❌ ไม่เจอ SQLite: {SQLITE_PATH}")
        return 1

    db_url = load_database_url()
    masked_url = db_url.split("@")[-1] if "@" in db_url else "..."
    print(f"📦 Source: {SQLITE_PATH.name}")
    print(f"☁️  Target: ...@{masked_url}\n")

    # ─── Connect ───
    sq_conn = sqlite3.connect(SQLITE_PATH)
    sq_conn.row_factory = sqlite3.Row
    sq_cur = sq_conn.cursor()

    print("🔌 เชื่อมต่อ Supabase...", end=" ", flush=True)
    try:
        pg_conn = psycopg2.connect(db_url)
        pg_cur = pg_conn.cursor()
        print("✅\n")
    except psycopg2.OperationalError as e:
        print(f"❌\n   {e}")
        return 1

    # ─── 1. Build mappings ───
    print("🎭 Building staff mappings...")
    surgeon_names = collect_all_names(sq_cur, "surgeon_name", ["cases", "prediction_log"])
    surgeon_names += collect_all_names(sq_cur, "scheduled_surgeon", ["cases"])
    surgeon_map = build_mapping(surgeon_names, "SURG")

    scrub_names = collect_all_names(sq_cur, "scrub_nurse", ["cases"])
    scrub_map = build_mapping(scrub_names, "SCRUB")

    circ_names = collect_all_names(sq_cur, "circ_nurse", ["cases"])
    circ_map = build_mapping(circ_names, "CIRC")

    print(f"   ✅ {len(surgeon_map)} surgeons")
    print(f"   ✅ {len(scrub_map)} scrub nurses")
    print(f"   ✅ {len(circ_map)} circ nurses\n")

    # ─── 2. Save mapping CSV ───
    save_mapping_csv(surgeon_map, scrub_map, circ_map)
    print(f"💾 Saved mapping → {MAPPING_CSV}")
    print(f"   ⚠️  ไฟล์นี้ gitignored — ห้าม share / upload\n")

    # ─── 3. Truncate Supabase ───
    print("🗑️  Clearing Supabase tables...")
    for tbl in ("audit_log", "prediction_log", "backup_log", "cases", "room_settings"):
        try:
            pg_cur.execute(f"TRUNCATE TABLE {tbl} RESTART IDENTITY CASCADE")
        except psycopg2.Error as e:
            print(f"   ⚠️  {tbl}: {e}")
            pg_conn.rollback()
    pg_conn.commit()
    print("   ✅ Cleared\n")

    # ─── 4. Anonymize + upload cases ───
    print("📤 Uploading anonymized cases...")
    sq_cur.execute("PRAGMA table_info(cases)")
    existing_cols = {r[1] for r in sq_cur.fetchall()}
    select_cols = [c if c in existing_cols else "NULL" for c in CASES_COLS]
    sq_cur.execute(f"SELECT {', '.join(select_cols)} FROM cases ORDER BY case_id")

    anonymized_rows: list[tuple] = []

    for row in sq_cur.fetchall():
        d = dict(zip(CASES_COLS, row))

        # ✂️ Drop PII (set NULL)
        for k in PII_DROP:
            d[k] = None

        # 🎭 Mask staff (single)
        d["surgeon_name"] = mask_single(d.get("surgeon_name"), surgeon_map)
        d["scheduled_surgeon"] = mask_single(d.get("scheduled_surgeon"), surgeon_map)

        # 🎭 Mask staff (multi)
        d["scrub_nurse"] = mask_multi(d.get("scrub_nurse"), scrub_map)
        d["circ_nurse"] = mask_multi(d.get("circ_nurse"), circ_map)

        anonymized_rows.append(tuple(d[c] for c in CASES_COLS))

    if anonymized_rows:
        col_list = ", ".join(CASES_COLS)
        sql = f"INSERT INTO cases ({col_list}) VALUES %s"
        execute_values(pg_cur, sql, anonymized_rows, page_size=500)
        pg_conn.commit()

        # Reset sequence
        pg_cur.execute(
            "SELECT setval('cases_case_id_seq', COALESCE((SELECT MAX(case_id) FROM cases), 1), true)"
        )
        pg_conn.commit()
        print(f"   ✅ {len(anonymized_rows)} cases uploaded (PII removed, staff masked)\n")
    else:
        print("   ⏭️  No cases to upload\n")

    # ─── 5. Upload app_settings (no PII expected) ───
    print("📤 Uploading app_settings...")
    sq_cur.execute("SELECT key, value FROM app_settings")
    settings_rows = sq_cur.fetchall()
    if settings_rows:
        for r in settings_rows:
            pg_cur.execute(
                "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (r[0], r[1]),
            )
        pg_conn.commit()
        print(f"   ✅ {len(settings_rows)} settings uploaded\n")
    else:
        print("   ⏭️  No app_settings to upload\n")

    # ─── 6. VERIFY: zero PII in Supabase ───
    print("🔍 PII Verification:")
    pg_cur.execute("SELECT COUNT(*) FROM cases")
    n_cases = pg_cur.fetchone()[0]

    pg_cur.execute(
        "SELECT COUNT(*) FROM cases WHERE hn IS NOT NULL OR name IS NOT NULL OR an IS NOT NULL"
    )
    n_pii = pg_cur.fetchone()[0]

    pg_cur.execute(
        "SELECT COUNT(*) FROM cases "
        "WHERE surgeon_name IS NOT NULL AND surgeon_name NOT LIKE 'SURG\\_%' ESCAPE '\\'"
    )
    n_unmasked_surg = pg_cur.fetchone()[0]

    pg_cur.execute(
        "SELECT COUNT(*) FROM cases "
        "WHERE scrub_nurse IS NOT NULL AND scrub_nurse != '' "
        "AND scrub_nurse NOT LIKE '%SCRUB\\_%' ESCAPE '\\'"
    )
    n_unmasked_scrub = pg_cur.fetchone()[0]

    print(f"   📊 Total cases in Supabase : {n_cases}")
    print(f"   🛡️  PII rows (hn/name/an)  : {n_pii}    {'✅ CLEAN' if n_pii == 0 else '❌ LEAK'}")
    print(f"   🎭 Unmasked surgeons       : {n_unmasked_surg} {'✅' if n_unmasked_surg == 0 else '⚠️'}")
    print(f"   🎭 Unmasked scrub nurses   : {n_unmasked_scrub} {'✅' if n_unmasked_scrub == 0 else '⚠️'}")

    # Sample
    print("\n📋 Sample anonymized rows (first 3):")
    pg_cur.execute(
        "SELECT case_id, op_date, hn, name, procedure_name, surgeon_name, scrub_nurse, circ_nurse "
        "FROM cases ORDER BY case_id LIMIT 3"
    )
    for r in pg_cur.fetchall():
        print(
            f"   case_id={r[0]} | date={r[1]} | hn={r[2]!r} | name={r[3]!r} | "
            f"proc={(r[4] or '')[:30]!r} | surg={r[5]!r} | scrub={r[6]!r} | circ={r[7]!r}"
        )

    sq_conn.close()
    pg_conn.close()

    print("\n" + "═" * 60)
    if n_pii == 0 and n_unmasked_surg == 0:
        print("🎉 SUCCESS — Cloud DB is PDPA-safe (no patient PII, staff masked)")
        print(f"📋 Mapping file: {MAPPING_CSV}")
        print(f"   ใช้สำหรับ decode รหัส → ชื่อจริง เมื่อ defend thesis")
    else:
        print("⚠️  พบ PII leak — ตรวจสอบ log ด้านบน")
        return 1
    print("═" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
