"""
🧪 Smoke Test — ทดสอบ minor_or_db + db_connection หลัง refactor
ใช้: python supabase/test_db_module.py
"""

import sys
from pathlib import Path

# add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    print("═" * 60)
    print("🧪 Test: db_connection + minor_or_db (post-refactor)")
    print("═" * 60)

    # 1. โหลด config
    try:
        from db_connection import get_db_info, IS_POSTGRES
        info = get_db_info()
        print(f"\n📋 Database mode: {info['mode']}")
        print(f"   - is_postgres : {info['is_postgres']}")
        print(f"   - has_url     : {info['has_url']}")
        if not IS_POSTGRES:
            print("⚠️  Mode = sqlite — ตั้ง db_mode='supabase' ใน secrets.toml ก่อนเทส")
    except Exception as e:
        print(f"❌ Import db_connection ไม่ได้: {e}")
        return 1

    # 2. ทดสอบ connection
    print("\n🔌 ทดสอบ get_conn()...", end=" ", flush=True)
    try:
        from minor_or_db import get_conn
        conn = get_conn()
        print("✅")
    except Exception as e:
        print(f"❌\n   {e}")
        return 1

    # 3. นับ rows
    print("\n📊 อ่านข้อมูลจริง:")
    try:
        for tbl in ('cases', 'audit_log', 'prediction_log', 'app_settings'):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                print(f"   ✅ {tbl:20s} {n:>5} rows")
            except Exception as e:
                print(f"   ❌ {tbl:20s} ERROR: {e}")
    except Exception as e:
        print(f"❌ Query failed: {e}")
        return 1

    # 4. ทดสอบ row access (sqlite3.Row vs DictRow compat)
    print("\n🧩 ทดสอบ row access:")
    try:
        row = conn.execute(
            "SELECT case_id, procedure_name, surgeon_name FROM cases LIMIT 1"
        ).fetchone()
        if row:
            print(f"   ✅ row[0] (index)         = {row[0]}")
            print(f"   ✅ row['procedure_name']  = {row['procedure_name']!r}")
            print(f"   ✅ row['surgeon_name']    = {row['surgeon_name']!r}")
        else:
            print("   ⚠️  No data in cases")
    except Exception as e:
        print(f"   ❌ Row access failed: {e}")
        return 1

    # 5. ทดสอบ placeholder conversion
    print("\n🔄 ทดสอบ placeholder conversion (? → %s):")
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE op_date >= ?",
            ("2026-01-01",)
        ).fetchone()
        print(f"   ✅ Query with `?` worked: {row[0]} cases from 2026-01-01")
    except Exception as e:
        print(f"   ❌ Placeholder failed: {e}")
        return 1

    # 6. ทดสอบ init_db (no-op สำหรับ postgres)
    print("\n⚙️  ทดสอบ init_db():")
    try:
        from minor_or_db import init_db
        init_db()
        print("   ✅ init_db() ผ่าน (no schema changes)")
    except Exception as e:
        print(f"   ❌ init_db failed: {e}")
        return 1

    # 7. ทดสอบ app_settings upsert
    print("\n💾 ทดสอบ _set_app_setting + _get_app_setting:")
    try:
        from minor_or_db import _set_app_setting, _get_app_setting
        _set_app_setting('_test_key', 'hello_supabase')
        v = _get_app_setting('_test_key', 'NOT_FOUND')
        if v == 'hello_supabase':
            print(f"   ✅ Upsert + read OK: {v!r}")
            # cleanup
            try:
                conn2 = get_conn()
                conn2.execute("DELETE FROM app_settings WHERE key=?", ('_test_key',))
                conn2.commit()
                conn2.close()
            except Exception:
                pass
        else:
            print(f"   ❌ Got: {v!r}, expected 'hello_supabase'")
            return 1
    except Exception as e:
        print(f"   ❌ App settings failed: {e}")
        return 1

    conn.close()

    # 8. ทดสอบ db_session context manager
    print("\n🛡️  ทดสอบ db_session context manager:")
    try:
        from minor_or_db import db_session
        with db_session() as c:
            n = c.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
            print(f"   ✅ db_session OK: {n} cases")
    except Exception as e:
        print(f"   ❌ db_session failed: {e}")
        return 1

    print("\n" + "═" * 60)
    print("🎉 ทุก test ผ่าน! พร้อมรัน Streamlit app บน Supabase")
    print("═" * 60)
    print("\nขั้นต่อไป:  streamlit run minor_or_app.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
