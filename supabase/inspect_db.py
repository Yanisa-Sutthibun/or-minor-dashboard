"""
═══════════════════════════════════════════════════════════════════
🔍 inspect_db.py — Inspect Supabase database
═══════════════════════════════════════════════════════════════════
Connect ผ่าน database_url ใน secrets.toml แล้ว print:
  📋 Tables + row counts
  🛡️  PII leak check (hn / name / an ต้องเป็น 0)
  🎭 Mask coverage (surgeon/scrub/circ ต้องเริ่มด้วย SURG_/SCRUB_/CIRC_)
  📊 Sample 3 rows ของ cases

วิธีรัน:
    python supabase/inspect_db.py
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("❌ ต้องติดตั้ง psycopg2 ก่อน: pip install psycopg2-binary")
    sys.exit(1)

try:
    import toml
except ImportError:
    print("❌ ต้องติดตั้ง toml ก่อน: pip install toml")
    sys.exit(1)


ROOT = Path(__file__).resolve().parent.parent
SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"


def load_database_url() -> str:
    if SECRETS_PATH.exists():
        try:
            secrets = toml.load(SECRETS_PATH)
            url = secrets.get("database_url", "").strip()
            if url and "YOUR_PASSWORD_HERE" not in url:
                return url
        except Exception:
            pass
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    print(f"❌ หา database_url ไม่เจอ — แก้ {SECRETS_PATH} ก่อน")
    sys.exit(1)


def fmt_value(v, max_len: int = 30) -> str:
    """Truncate ค่ายาวๆ ให้สั้นลง"""
    if v is None:
        return "NULL"
    s = str(v)
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s


def main() -> int:
    print("═" * 70)
    print("🔍 Supabase Database Inspection")
    print("═" * 70)

    db_url = load_database_url()
    masked = db_url.split("@")[-1] if "@" in db_url else "..."
    print(f"☁️  Target: ...@{masked}\n")

    print("🔌 Connecting...", end=" ", flush=True)
    try:
        conn = psycopg2.connect(db_url, connect_timeout=10)
        cur = conn.cursor()
        print("✅\n")
    except psycopg2.OperationalError as e:
        print(f"❌\n   {e}")
        return 1

    # ─── 1. List tables + counts ─────────────────────────────────────
    print("📋 Tables in public schema:")
    print("─" * 70)
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name"
    )
    tables = [r[0] for r in cur.fetchall()]
    if not tables:
        print("   (no tables)")
        return 1

    for tbl in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            n = cur.fetchone()[0]
            # Column count
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name = %s AND table_schema = 'public'",
                (tbl,)
            )
            ncols = cur.fetchone()[0]
            print(f"   📊 {tbl:20s}  {n:>6} rows  ({ncols} columns)")
        except Exception as e:
            print(f"   ⚠️  {tbl:20s}  ERROR: {e}")
            conn.rollback()

    # ─── 2. PII Leak check ───────────────────────────────────────────
    if "cases" in tables:
        print("\n🛡️  PII Leak Check (cases table):")
        print("─" * 70)
        cur.execute("SELECT COUNT(*) FROM cases")
        n_total = cur.fetchone()[0]

        checks = [
            ("hn",   "SELECT COUNT(*) FROM cases WHERE hn IS NOT NULL AND hn != ''"),
            ("name", "SELECT COUNT(*) FROM cases WHERE name IS NOT NULL AND name != ''"),
            ("an",   "SELECT COUNT(*) FROM cases WHERE an IS NOT NULL AND an != ''"),
        ]
        all_clean = True
        for col, sql in checks:
            cur.execute(sql)
            n = cur.fetchone()[0]
            status = "✅ CLEAN" if n == 0 else f"❌ LEAK ({n} rows)"
            print(f"   {col:6s}  {n:>5} / {n_total:<5}  {status}")
            if n > 0:
                all_clean = False

        # ─── 3. Mask coverage ───────────────────────────────────────
        print("\n🎭 Mask Coverage (cases table):")
        print("─" * 70)
        mask_checks = [
            ("surgeon_name",       "SURG_",
             "WHERE surgeon_name IS NOT NULL AND surgeon_name != '' AND surgeon_name NOT LIKE 'SURG\\_%' ESCAPE '\\'"),
            ("scheduled_surgeon",  "SURG_",
             "WHERE scheduled_surgeon IS NOT NULL AND scheduled_surgeon != '' AND scheduled_surgeon NOT LIKE 'SURG\\_%' ESCAPE '\\'"),
            ("scrub_nurse",        "SCRUB_",
             "WHERE scrub_nurse IS NOT NULL AND scrub_nurse != '' AND scrub_nurse NOT LIKE '%SCRUB\\_%' ESCAPE '\\'"),
            ("circ_nurse",         "CIRC_",
             "WHERE circ_nurse IS NOT NULL AND circ_nurse != '' AND circ_nurse NOT LIKE '%CIRC\\_%' ESCAPE '\\'"),
        ]
        for col, prefix, where in mask_checks:
            cur.execute(f"SELECT COUNT(*) FROM cases {where}")
            n_bad = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM cases WHERE {col} IS NOT NULL AND {col} != ''")
            n_filled = cur.fetchone()[0]
            status = "✅" if n_bad == 0 else f"❌ {n_bad} unmasked"
            print(f"   {col:20s}  filled={n_filled:>4}  unmasked={n_bad:>4}  {status}")
            if n_bad > 0:
                all_clean = False

        # ─── 4. Sample rows ─────────────────────────────────────────
        print("\n📋 Sample anonymized rows (first 3):")
        print("─" * 70)
        cur.execute(
            "SELECT case_id, op_date, hn, name, "
            "procedure_name, surgeon_name, scheduled_surgeon, "
            "scrub_nurse, circ_nurse, diagnosis "
            "FROM cases ORDER BY case_id LIMIT 3"
        )
        for r in cur.fetchall():
            (cid, dt, hn, nm, proc, surg, sched, scrub, circ, diag) = r
            print(f"   case_id={cid} | date={dt}")
            print(f"     hn={fmt_value(hn)}  name={fmt_value(nm)}")
            print(f"     procedure={fmt_value(proc, 40)}")
            print(f"     surgeon={fmt_value(surg)}  scheduled={fmt_value(sched)}")
            print(f"     scrub={fmt_value(scrub, 40)}")
            print(f"     circ={fmt_value(circ, 40)}")
            print(f"     diagnosis={fmt_value(diag, 40)}")
            print()

        # ─── 5. Distinct masked codes ───────────────────────────────
        print("🎭 Distinct masked codes:")
        print("─" * 70)
        cur.execute("SELECT COUNT(DISTINCT surgeon_name) FROM cases WHERE surgeon_name LIKE 'SURG\\_%' ESCAPE '\\'")
        n_surg = cur.fetchone()[0]
        print(f"   surgeons (SURG_*) : {n_surg}")

        cur.execute(
            "SELECT COUNT(DISTINCT unnest_part) FROM ("
            "  SELECT unnest(string_to_array(scrub_nurse, ', ')) AS unnest_part FROM cases"
            "  WHERE scrub_nurse IS NOT NULL AND scrub_nurse != ''"
            ") sub WHERE unnest_part LIKE 'SCRUB\\_%' ESCAPE '\\'"
        )
        n_scrub = cur.fetchone()[0]
        print(f"   scrubs (SCRUB_*)  : {n_scrub}")

        cur.execute(
            "SELECT COUNT(DISTINCT unnest_part) FROM ("
            "  SELECT unnest(string_to_array(circ_nurse, ', ')) AS unnest_part FROM cases"
            "  WHERE circ_nurse IS NOT NULL AND circ_nurse != ''"
            ") sub WHERE unnest_part LIKE 'CIRC\\_%' ESCAPE '\\'"
        )
        n_circ = cur.fetchone()[0]
        print(f"   circs (CIRC_*)    : {n_circ}")
    else:
        all_clean = False
        print("\n⚠️  ไม่มี cases table — skip PII check")

    conn.close()

    print("\n" + "═" * 70)
    if all_clean:
        print("🎉 CLEAN — Database พร้อม deploy บน Streamlit Cloud (PDPA-safe)")
    else:
        print("⚠️  พบปัญหา — รัน supabase/anonymize_for_cloud.py อีกครั้ง")
    print("═" * 70)
    return 0 if all_clean else 1


if __name__ == "__main__":
    sys.exit(main())
