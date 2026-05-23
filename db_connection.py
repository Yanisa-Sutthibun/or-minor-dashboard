"""
═══════════════════════════════════════════════════════════════════
🔌 Database Connection Layer — รองรับทั้ง SQLite และ Supabase PostgreSQL
═══════════════════════════════════════════════════════════════════

หลักการ:
  - อ่าน db_mode จาก .streamlit/secrets.toml
  - "sqlite"  → คืน sqlite3.Connection ตามเดิม (dev local)
  - "supabase" → คืน wrapper ที่ make psycopg2 ดูเหมือน sqlite3 API
                 (conn.execute(), executescript(), row['col'], row[0] ทั้งหมด work)

ทำไมต้องใช้ wrapper:
  - psycopg2 ใช้ `%s` แทน `?` placeholder
  - psycopg2 ไม่มี executescript()
  - psycopg2 default row เป็น tuple ไม่ใช่ dict-like
  → wrap ให้ minor_or_db.py ใช้ syntax เดิมได้

SQL Converter (_convert_sql_for_pg) แปลง SQLite → Postgres:
  - strftime('%Y-%m', col) → TO_CHAR(col::TIMESTAMP, 'YYYY-MM')
  - strftime('%w', col)    → EXTRACT(DOW FROM col::TIMESTAMP)::INTEGER
  - DATE('now')            → to_char(CURRENT_DATE, 'YYYY-MM-DD') (text match)
  - datetime('now','localtime') → bangkok TZ text
  - INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY
  - PRAGMA ... → stripped
  - escape % literal ใน string เป็น %%  (กัน psycopg2 ตีความเป็น placeholder)
  - ? → %s
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

# ─── Config: read db_mode ───────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_SECRETS_PATH = _SCRIPT_DIR / ".streamlit" / "secrets.toml"
DEFAULT_DB_PATH = str(_SCRIPT_DIR / "minor_or.db")


def _read_secrets() -> dict:
    """อ่าน .streamlit/secrets.toml — silent fallback หากไม่มี"""
    if not _SECRETS_PATH.exists():
        return {}
    try:
        # ลองใช้ Streamlit's secrets ก่อน (มี caching ในตัว)
        try:
            import streamlit as st
            return dict(st.secrets)
        except Exception:
            pass
        # Fallback: อ่าน toml ตรงๆ
        import toml
        return toml.load(_SECRETS_PATH)
    except Exception:
        return {}


_SECRETS = _read_secrets()
DB_MODE = (_SECRETS.get("db_mode") or os.environ.get("DB_MODE") or "sqlite").lower().strip()
DATABASE_URL = (_SECRETS.get("database_url") or os.environ.get("DATABASE_URL") or "").strip()

IS_POSTGRES = (DB_MODE == "supabase" or DB_MODE == "postgres") and DATABASE_URL != ""
IS_SQLITE = not IS_POSTGRES


# ─── PostgreSQL: wrapper to mimic sqlite3 API ───────────────────────
if IS_POSTGRES:
    try:
        import psycopg2
        from psycopg2.extras import DictCursor
    except ImportError as e:
        raise ImportError(
            "❌ ต้องการ psycopg2 สำหรับ Supabase mode — รัน: pip install psycopg2-binary"
        ) from e


# ─── strftime format token mapping (SQLite → PostgreSQL TO_CHAR) ────
_STRFTIME_TOKENS = [
    ("%Y", "YYYY"),
    ("%y", "YY"),
    ("%m", "MM"),
    ("%d", "DD"),
    ("%H", "HH24"),
    ("%M", "MI"),
    ("%S", "SS"),
    ("%j", "DDD"),
]


def _translate_strftime_format(sqlite_fmt: str) -> str:
    """'%Y-%m-%d %H:%M:%S' → 'YYYY-MM-DD HH24:MI:SS'"""
    out = sqlite_fmt
    for src, dst in _STRFTIME_TOKENS:
        out = out.replace(src, dst)
    return out


def _convert_strftime(sql: str) -> str:
    """
    แปลง SQLite strftime() → PostgreSQL equivalent
      strftime('%Y-%m', col)  → TO_CHAR(col::TIMESTAMP, 'YYYY-MM')
      strftime('%w', col)     → EXTRACT(DOW FROM col::TIMESTAMP)::INTEGER
    """
    # special case: %w (day of week)
    def _repl_dow(m):
        col = m.group(1).strip()
        return f"EXTRACT(DOW FROM {col}::TIMESTAMP)::INTEGER"

    sql = re.sub(
        r"strftime\s*\(\s*'%w'\s*,\s*([^)]+?)\s*\)",
        _repl_dow, sql, flags=re.IGNORECASE,
    )

    # generic: strftime('FMT', col)
    def _repl_generic(m):
        fmt = m.group(1)
        col = m.group(2).strip()
        pg_fmt = _translate_strftime_format(fmt)
        return f"TO_CHAR({col}::TIMESTAMP, '{pg_fmt}')"

    sql = re.sub(
        r"strftime\s*\(\s*'([^']+)'\s*,\s*([^)]+?)\s*\)",
        _repl_generic, sql, flags=re.IGNORECASE,
    )
    return sql


def _escape_percent_in_strings(sql: str) -> str:
    """
    Escape % → %% เฉพาะใน string literals (single quotes)
    เพื่อป้องกัน psycopg2 ตีความเป็น %s placeholder
    Note: ทำหลังแปลง strftime ทั้งหมดแล้ว (ไม่งั้น '%Y' จะกลายเป็น '%%Y')
    """
    out = []
    i = 0
    n = len(sql)
    in_single = False
    while i < n:
        ch = sql[i]
        if ch == "'":
            # SQL string literal escape: '' = literal '
            if in_single and i + 1 < n and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_single = not in_single
            out.append(ch)
            i += 1
        elif ch == "%" and in_single:
            out.append("%%")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _convert_placeholders(sql: str) -> str:
    """แปลง `?` → `%s` สำหรับ psycopg2 (ฉลาด: ไม่แตะ `?` ใน string literal)"""
    out = []
    in_single = False
    in_double = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == "?" and not in_single and not in_double:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _convert_sql_for_pg(sql: str) -> str:
    """แปลง SQLite-specific syntax → PostgreSQL

    Order matters:
      1. strftime — ต้องแปลงก่อน escape % (ไม่งั้น '%Y' → '%%Y' แล้วแปลงไม่ได้)
      2. DATE('now'), datetime('now')
      3. AUTOINCREMENT, PRAGMA
      4. escape % literal ใน string (กัน psycopg2 ตีความเป็น placeholder)
      5. ?  →  %s (placeholder จริง)
    """
    s = sql

    # 1. strftime() — ต้องก่อน escape %
    s = _convert_strftime(s)

    # 2a. datetime('now','localtime') → text timestamp ที่ Bangkok TZ
    s = re.sub(
        r"datetime\s*\(\s*'now'\s*,\s*'localtime'\s*\)",
        "to_char((NOW() AT TIME ZONE 'Asia/Bangkok'), 'YYYY-MM-DD HH24:MI:SS')",
        s, flags=re.IGNORECASE,
    )

    # 2b. DATE('now') / DATE('now', 'localtime') → text 'YYYY-MM-DD'
    # หมายเหตุ: op_date เก็บเป็น TEXT ใน schema (ทั้ง SQLite + Postgres)
    # ถ้าใช้ CURRENT_DATE ตรงๆ จะเป็น DATE type → Postgres เทียบ text < date ไม่ได้
    # จึงต้อง cast เป็น text ในรูปแบบ 'YYYY-MM-DD' ให้ตรงกับที่เก็บใน column
    s = re.sub(
        r"DATE\s*\(\s*'now'\s*(?:,\s*'localtime'\s*)?\)",
        "to_char(CURRENT_DATE, 'YYYY-MM-DD')",
        s, flags=re.IGNORECASE,
    )

    # 3. INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY
    s = re.sub(
        r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
        "SERIAL PRIMARY KEY",
        s, flags=re.IGNORECASE,
    )
    # PRAGMA → ignore (treated as comment)
    s = re.sub(r"PRAGMA\s+[^;]+;?", "-- PRAGMA stripped", s, flags=re.IGNORECASE)

    # 4. escape % literal ใน string (กัน psycopg2 ตีความเป็น %s)
    s = _escape_percent_in_strings(s)

    # 5. ? → %s
    s = _convert_placeholders(s)
    return s


class _PgCursor:
    """psycopg2 cursor wrapper — เลียนแบบ sqlite3 cursor

    รองรับ:
      - .execute(sql, params) → auto convert `?` → `%s`
      - .executemany(sql, params_seq)
      - .fetchone(), .fetchall(), .fetchmany()
      - .description (pandas ใช้อ่านชื่อคอลัมน์)
      - .rowcount, .lastrowid
      - iteration
    """

    def __init__(self, raw_cursor):
        self._cur = raw_cursor

    def execute(self, sql: str, params=None):
        """รัน SQL — แปลง placeholder อัตโนมัติ"""
        sql = _convert_sql_for_pg(sql)
        if params is None:
            self._cur.execute(sql)
        else:
            self._cur.execute(sql, params)
        return self

    def executemany(self, sql: str, seq_of_params):
        sql = _convert_sql_for_pg(sql)
        self._cur.executemany(sql, seq_of_params)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size=None):
        if size is None:
            return self._cur.fetchmany()
        return self._cur.fetchmany(size)

    @property
    def description(self):
        # pandas ใช้: col[0] = column name
        return self._cur.description

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        # psycopg2 ไม่มี lastrowid — ใช้ RETURNING แทน (caller ต้อง handle)
        return None

    @property
    def arraysize(self):
        return self._cur.arraysize

    @arraysize.setter
    def arraysize(self, value):
        self._cur.arraysize = value

    def __iter__(self):
        return iter(self._cur)

    def close(self):
        self._cur.close()

    def __getattr__(self, name):
        """Fallback: proxy attribute ที่ไม่ได้ override ไปที่ raw cursor

        (ป้องกัน pandas/library อื่นๆ ที่อาจเรียก method ที่ไม่ได้คาดไว้)
        """
        return getattr(self._cur, name)


class _PgConnection:
    """psycopg2 connection wrapper — เลียนแบบ sqlite3.Connection"""

    def __init__(self, raw_conn):
        self._conn = raw_conn
        # ใส่ attr ที่ sqlite3 มีแต่ pg ไม่มี — กัน AttributeError
        self.isolation_level = None  # no-op placeholder

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, value):
        # no-op: เราใช้ DictCursor อยู่แล้ว → row['col'] และ row[0] work ทั้งคู่
        pass

    def execute(self, sql: str, params: Optional[Sequence] = None) -> _PgCursor:
        """รัน SQL หนึ่งคำสั่ง — auto convert placeholder + return cursor"""
        sql = _convert_sql_for_pg(sql)
        cur = self._conn.cursor(cursor_factory=DictCursor)
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, params)
        return _PgCursor(cur)

    def executemany(self, sql: str, seq_of_params: Iterable) -> _PgCursor:
        sql = _convert_sql_for_pg(sql)
        cur = self._conn.cursor(cursor_factory=DictCursor)
        cur.executemany(sql, seq_of_params)
        return _PgCursor(cur)

    def executescript(self, sql_script: str) -> None:
        """แยก multi-statement → execute ทีละอัน (psycopg2 ไม่มี executescript)"""
        sql_script = _convert_sql_for_pg(sql_script)
        # ตัด comment ออกก่อน split (ป้องกัน '--' มี ';' ใน comment)
        cleaned_lines = []
        for line in sql_script.split("\n"):
            stripped = line.strip()
            if stripped.startswith("--"):
                continue
            cleaned_lines.append(line)
        cleaned = "\n".join(cleaned_lines)

        # Split on `;` แล้วรันทีละ statement
        statements = [s.strip() for s in cleaned.split(";") if s.strip()]
        cur = self._conn.cursor()
        for stmt in statements:
            try:
                cur.execute(stmt)
            except psycopg2.errors.DuplicateTable:
                self._conn.rollback()
                continue
            except psycopg2.errors.DuplicateObject:
                self._conn.rollback()
                continue
            except psycopg2.Error as e:
                # ไม่หยุด — log แล้วไปต่อ (เพราะหลาย DDL เป็น IF NOT EXISTS)
                self._conn.rollback()
                continue
        self._conn.commit()
        cur.close()

    def cursor(self):
        return _PgCursor(self._conn.cursor(cursor_factory=DictCursor))

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        return False


# ─── Public API ─────────────────────────────────────────────────────
def get_connection(db_path: str = None, timeout: int = 10):
    """
    คืน connection ตาม db_mode

    Returns:
        - sqlite3.Connection (mode = sqlite)
        - _PgConnection wrapper (mode = supabase)
    """
    if IS_POSTGRES:
        raw = psycopg2.connect(DATABASE_URL, connect_timeout=timeout)
        return _PgConnection(raw)
    else:
        path = db_path or DEFAULT_DB_PATH
        conn = sqlite3.connect(path, timeout=timeout)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.row_factory = sqlite3.Row
        return conn


def get_db_info() -> dict:
    """ข้อมูล mode ปัจจุบัน — สำหรับ debug/UI"""
    return {
        "mode": "supabase" if IS_POSTGRES else "sqlite",
        "is_postgres": IS_POSTGRES,
        "is_sqlite": IS_SQLITE,
        "has_url": bool(DATABASE_URL),
        "sqlite_path": DEFAULT_DB_PATH if IS_SQLITE else None,
    }
