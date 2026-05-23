"""
═══════════════════════════════════════════════════════════════════
🚚 Migration Script: SQLite → Supabase PostgreSQL
═══════════════════════════════════════════════════════════════════
ใช้ครั้งเดียวเพื่อย้ายข้อมูลจาก local minor_or.db ขึ้น Supabase

วิธีใช้:
    1. สร้าง .streamlit/secrets.toml ก่อน (copy จาก secrets.toml.example
       แล้วใส่ database_url + password จริง)
    2. ติดตั้ง deps:
           pip install psycopg2-binary toml
    3. รันสคริปต์:
           python supabase/migrate_to_supabase.py
    4. ดู log ว่าครบทุก row แล้ว verify count ตรงกัน
═══════════════════════════════════════════════════════════════════

หมายเหตุ:
- รักษา case_id เดิมไว้ (preserve PKs) แล้ว reset sequence ตอนท้าย
- ถ้า Supabase มีข้อมูลเดิมอยู่แล้ว → จะ skip + รายงานให้ดู (ไม่ overwrite)
- ใช้ execute_values สำหรับ bulk insert (เร็วกว่า single insert ~100x)
"""

from __future__ import annotations

import os
import sys
import sqlite3
from pathlib import Path
from typing import Any

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


# ─── Config loader ──────────────────────────────────────────────────
def load_database_url() -> str:
    """อ่าน database_url จาก secrets.toml (fallback: env var DATABASE_URL)"""
    # 1) อ่านจาก secrets.toml
    if SECRETS_PATH.exists():
        secrets = toml.load(SECRETS_PATH)
        url = secrets.get("database_url", "").strip()
        if url and "YOUR_PASSWORD_HERE" not in url and "[password" not in url.lower():
            return url

    # 2) Fallback: env var
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url

    print("❌ หา database_url ไม่เจอ — แก้ไข .streamlit/secrets.toml ก่อน")
    print(f"   ที่ path: {SECRETS_PATH}")
    sys.exit(1)


# ─── Schema mapping ─────────────────────────────────────────────────
# คอลัมน์ของแต่ละ table (ตาม order ใน schema_postgres.sql)
# ไม่รวม SERIAL PK ที่ auto-generate ยกเว้นเราจะ preserve เอง
TABLES = {
    "cases": {
        "pk": "case_id",
        "preserve_pk": True,  # รักษา case_id เดิมไว้ (เผื่อมี external reference)
        "columns": [
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
        ],
    },
    "audit_log": {
        "pk": "log_id",
        "preserve_pk": True,
        "columns": ["log_id", "case_id", "action", "old_value", "new_value", "detail", "created_at"],
    },
    "prediction_log": {
        "pk": "pred_id",
        "preserve_pk": True,
        "columns": ["pred_id", "case_id", "model_version", "procedure_name", "surgeon_name",
                    "predicted_min", "actual_min", "abs_error", "confidence", "created_at"],
    },
    "backup_log": {
        "pk": "backup_id",
        "preserve_pk": True,
        "columns": ["backup_id", "backup_path", "row_count", "created_at"],
    },
    "room_settings": {
        "pk": "room_no",
        "preserve_pk": True,
        "columns": ["room_no", "enabled", "scrub_json", "circ_json", "updated_at"],
    },
    "app_settings": {
        "pk": "key",
        "preserve_pk": True,
        "columns": ["key", "value"],
    },
}


# ─── Migration logic ────────────────────────────────────────────────
def fetch_sqlite_rows(sqlite_conn: sqlite3.Connection, table: str, columns: list[str]) -> list[tuple]:
    """อ่านทุกแถวจาก SQLite table"""
    cur = sqlite_conn.cursor()
    # ตรวจสอบว่ามีคอลัมน์ครบ — ถ้าไม่มีใน SQLite ให้ใส่ None
    cur.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    available = [c if c in existing else None for c in columns]

    # สร้าง SELECT statement (replace missing cols with NULL)
    select_parts = [c if c else "NULL" for c in available]
    sql = f"SELECT {', '.join(select_parts)} FROM {table}"
    cur.execute(sql)
    return cur.fetchall()


def insert_to_postgres(
    pg_conn: psycopg2.extensions.connection,
    table: str,
    columns: list[str],
    rows: list[tuple],
    preserve_pk: bool,
    pk_name: str,
) -> int:
    """Bulk insert → PostgreSQL ด้วย execute_values"""
    if not rows:
        return 0

    cur = pg_conn.cursor()
    col_list = ", ".join(columns)
    # ON CONFLICT DO NOTHING — ถ้ามี PK ซ้ำให้ skip
    conflict_target = pk_name if preserve_pk else ""
    on_conflict = f"ON CONFLICT ({conflict_target}) DO NOTHING" if conflict_target else ""

    sql = f"INSERT INTO {table} ({col_list}) VALUES %s {on_conflict}"
    execute_values(cur, sql, rows, page_size=500)
    inserted = cur.rowcount
    pg_conn.commit()
    return inserted


def reset_sequence(pg_conn: psycopg2.extensions.connection, table: str, pk_name: str) -> None:
    """รีเซ็ต SERIAL sequence ให้ตรงกับ MAX(pk)+1 (ป้องกัน PK conflict ตอน insert ใหม่)"""
    cur = pg_conn.cursor()
    seq_name = f"{table}_{pk_name}_seq"
    try:
        cur.execute(
            f"SELECT setval('{seq_name}', COALESCE((SELECT MAX({pk_name}) FROM {table}), 1), true)"
        )
        pg_conn.commit()
    except psycopg2.Error as e:
        # ไม่ใช่ทุก table มี sequence (เช่น room_settings, app_settings)
        pg_conn.rollback()


def verify_counts(sqlite_conn: sqlite3.Connection, pg_conn: psycopg2.extensions.connection) -> bool:
    """เปรียบเทียบ row count ระหว่าง SQLite กับ Supabase"""
    print("\n" + "═" * 60)
    print("🔍 VERIFY: เปรียบเทียบ row count")
    print("═" * 60)
    all_ok = True
    for table in TABLES:
        sq_cur = sqlite_conn.cursor()
        pg_cur = pg_conn.cursor()
        try:
            sq_count = sq_cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            sq_count = 0
        pg_cur.execute(f"SELECT COUNT(*) FROM {table}")
        pg_count = pg_cur.fetchone()[0]

        status = "✅" if sq_count == pg_count else "⚠️"
        if sq_count != pg_count:
            all_ok = False
        print(f"  {status} {table:20s}  SQLite={sq_count:>5}  Supabase={pg_count:>5}")
    print("═" * 60)
    return all_ok


# ─── Main ───────────────────────────────────────────────────────────
def main() -> None:
    print("═" * 60)
    print("🚚 SQLite → Supabase Migration")
    print("═" * 60)

    if not SQLITE_PATH.exists():
        print(f"❌ ไม่เจอ SQLite: {SQLITE_PATH}")
        sys.exit(1)

    db_url = load_database_url()
    # mask password ตอน print
    masked = db_url.split("@")[1] if "@" in db_url else "..."
    print(f"📦 Source : {SQLITE_PATH.name}")
    print(f"☁️  Target : ...@{masked}\n")

    # เชื่อม
    print("🔌 เชื่อมต่อ Supabase...", end=" ", flush=True)
    try:
        pg_conn = psycopg2.connect(db_url)
        print("✅")
    except psycopg2.OperationalError as e:
        print(f"❌\n   {e}")
        sys.exit(1)

    sqlite_conn = sqlite3.connect(SQLITE_PATH)

    # ─── Migrate ทีละ table ───
    total_inserted = 0
    for table, meta in TABLES.items():
        rows = fetch_sqlite_rows(sqlite_conn, table, meta["columns"])
        if not rows:
            print(f"⏭️  {table:20s}  (ว่าง — skip)")
            continue

        inserted = insert_to_postgres(
            pg_conn, table, meta["columns"], rows,
            preserve_pk=meta["preserve_pk"],
            pk_name=meta["pk"],
        )
        total_inserted += inserted
        skipped = len(rows) - inserted
        skip_note = f" ({skipped} duplicates skipped)" if skipped else ""
        print(f"📥 {table:20s}  {inserted:>5} rows inserted{skip_note}")

        # Reset sequence เผื่อ insert ใหม่จะได้ไม่ชน PK
        if meta["preserve_pk"] and meta["pk"] != "key" and meta["pk"] != "room_no":
            reset_sequence(pg_conn, table, meta["pk"])

    print(f"\n✨ รวม {total_inserted} rows ถูกย้ายเรียบร้อย")

    # ─── Verify ───
    ok = verify_counts(sqlite_conn, pg_conn)

    sqlite_conn.close()
    pg_conn.close()

    if ok:
        print("\n🎉 Migration สำเร็จ! row count ตรงทุก table")
        print("👉 ขั้นต่อไป: เปิด Supabase Table Editor ดู cases ว่าข้อมูลเข้าครบ")
    else:
        print("\n⚠️  มีบาง table ที่ count ไม่ตรง — ตรวจสอบ log ด้านบน")
        sys.exit(1)


if __name__ == "__main__":
    main()
