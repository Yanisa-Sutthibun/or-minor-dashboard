"""
Minor OR Database — SQLite Adapter v2 (Workflow Edition)
Status flow: scheduled → arrived → in_or → post_op → discharged | cancelled
"""
import re
import sqlite3
import os
import statistics
import pandas as pd
from datetime import datetime, timedelta
from difflib import SequenceMatcher

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_SCRIPT_DIR, 'minor_or.db')


# ============================================================================
# Procedure name fuzzy normalization (shared across heatmap + AI prediction)
# ----------------------------------------------------------------------------
# รวม "หัตถการ" ที่เขียนต่างกันแต่หมายถึงสิ่งเดียวกัน เช่น
#   - off PERM cath / off TCC Rt IJV  →  "Off catheter (PERM/TCC/IJV)"
#   - QS / Q-Switch / ND-YAG          →  "Q-Switch ND:YAG"
#   - excision / Excision             →  "Excision"
# ============================================================================

_PROC_RULES = [
    # Off catheter (PERM cath / TCC / IJV)
    (re.compile(
        r'\boff\b.*\b(perm\s*cath|perm|tcc|ijv|hd\s*cath|cath(eter)?)\b',
        re.I), 'Off catheter (PERM/TCC/IJV)'),
    # "remove cath" / "removal of catheter" (removal-first order)
    (re.compile(r'\b(remove|removal)\b.*\bcath(eter)?\b', re.I),
        'Off catheter (PERM/TCC/IJV)'),
    # "PERM/TCC catheter removal" (catheter-first order)
    (re.compile(r'\bcath(eter)?\b.*\b(remove|removal|off)\b', re.I),
        'Off catheter (PERM/TCC/IJV)'),

    # Nail extraction (รวม partial / total / specific toe)
    (re.compile(r'nail\s*(extract(ion)?|removal|avulsion)', re.I),
        'Nail extraction'),

    # ESWL
    (re.compile(r'\beswl\b', re.I), 'ESWL'),

    # I&D — Incision & Drainage (รวมรูปแบบ "I and D", "I & D", "I+D")
    (re.compile(r'\bi\s*(?:and|&|\+)\s*d\b|\bincision\s*(?:and|&)\s*drainage\b', re.I),
        'I&D'),

    # Excision (รวม Excisional biopsy ทั่วไป)
    (re.compile(r'\bexcis(ion|e|ional)\b', re.I), 'Excision'),

    # EC
    (re.compile(r'^\s*ec\s*$|\bec\b\s*(case|biopsy)?', re.I), 'EC'),

    # Morpheus (laser)
    (re.compile(r'\bmorpheus\b', re.I), 'Morpheus'),

    # Q-Switch ND:YAG laser
    (re.compile(r'\b(?:qs|q[\s\-]*switch|nd[\s:\-]*yag)\b', re.I),
        'Q-Switch ND:YAG'),

    # CO2 Laser (รวม CO2, CO2 Laser, CO2 laser ตัวเล็ก/ใหญ่)
    (re.compile(r'\bco\s*2\b', re.I), 'CO2 Laser'),

    # Change VAC dressing (รวม Change VAC, Change Vac, Change vac dressing)
    (re.compile(r'\bchange\s*vac\b', re.I), 'Change VAC dressing'),

    # Debridement (รวม DB ตัวย่อ + Debride/Debridement/Debriding)
    (re.compile(r'\b(?:debrid(?:e|ement|ing|ed)?|debride?ment)\b', re.I),
        'Debridement'),
    # "DB" prefix — รวม "DB", "DB foot", "DB pressure sore", "DB เปิด..."
    (re.compile(r'^\s*db\b', re.I), 'Debridement'),

    # Arch bar (รวม removal/insertion/replace)
    (re.compile(r'\barch\s*bar\b', re.I), 'Arch bar'),

    # Stitch off / Suture removal
    (re.compile(r'\b(?:stitch|suture)\s*(?:off|out|removal|remove)\b', re.I),
        'Stitch off'),
    (re.compile(r'\boff\s*(?:stitch|suture)\b', re.I), 'Stitch off'),

    # Biopsy (general)
    (re.compile(r'^\s*biopsy\s*$', re.I), 'Biopsy'),

    # Correction upper eyelid / Ptosis
    (re.compile(r'\b(?:correction|repair)\s*(?:upper|lower)?\s*eyelid\b', re.I),
        'Correction eyelid'),
    (re.compile(r'\bptosis\b', re.I), 'Correction eyelid'),
]


def _strip_modifiers(name: str) -> str:
    """ตัดคำขยายที่ไม่ส่งผลต่อชนิดหัตถการ เช่น Rt/Lt/Right/Left และเลขท้าย."""
    s = re.sub(r'\b(rt|lt|right|left|bilateral|bil|both)\b\.?', '', name, flags=re.I)
    s = re.sub(r'\bbig\s*toe\b|\b(1st|2nd|3rd|4th|5th)\s*toe\b', 'toe', s, flags=re.I)
    s = re.sub(r'\s+\d+\s*$', '', s)              # ลบเลขท้าย เช่น "extraction 2"
    s = re.sub(r'[\(\)\[\]\.]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


# Canonical names สำหรับ fuzzy fallback (SequenceMatcher)
_CANONICAL_NAMES = [
    'Off catheter (PERM/TCC/IJV)', 'Nail extraction', 'ESWL', 'I&D',
    'Excision', 'EC', 'Morpheus', 'Q-Switch ND:YAG',
    'CO2 Laser', 'Change VAC dressing', 'Debridement',
    'Arch bar', 'Stitch off', 'Biopsy', 'Correction eyelid',
]


def _fuzzy_match_canonical(name: str, threshold: float = 0.82):
    """SequenceMatcher fallback — จับ typo เช่น Debreidement → Debridement
    คืนค่า canonical ที่คล้ายที่สุด ถ้า ratio ≥ threshold มิฉะนั้น None
    """
    from difflib import SequenceMatcher
    best_score = 0.0
    best_canon = None
    n_lower = name.lower()
    for canon in _CANONICAL_NAMES:
        # เทียบทั้งคำเต็ม + first word (กันชื่อยาว)
        r1 = SequenceMatcher(None, n_lower, canon.lower()).ratio()
        c_first = canon.split()[0].lower()
        n_first = n_lower.split()[0] if n_lower else ''
        r2 = (SequenceMatcher(None, n_first, c_first).ratio()
              if n_first and c_first else 0)
        score = max(r1, r2)
        if score > best_score:
            best_score = score
            best_canon = canon
    return best_canon if best_score >= threshold else None


def _normalize_procedure_name(name) -> str:
    """แปลงชื่อหัตถการดิบ → canonical group ตาม rule + cleanup.

    Layer 1: Regex rules (เร็วสุด — pattern จับ keyword)
    Layer 2: SequenceMatcher fuzzy (จับ typo — เช่น Debreidement → Debridement)
    Layer 3: Cleanup + title case
    """
    if name is None:
        return 'UNKNOWN'
    s = str(name).strip()
    if not s or s.lower() in ('nan', 'none', '-'):
        return 'UNKNOWN'
    # Layer 1: Rule-based
    for pat, canonical in _PROC_RULES:
        if pat.search(s):
            return canonical
    # Layer 2: SequenceMatcher fuzzy (สำหรับ typo)
    fuzzy = _fuzzy_match_canonical(s)
    if fuzzy:
        return fuzzy
    # Layer 3: ตัด side / เลขท้าย แล้ว Title Case
    cleaned = _strip_modifiers(s)
    if not cleaned:
        return s
    # ถ้าเป็นตัวย่อสั้น ๆ ทั้งหมด (≤4 ตัว) เก็บ uppercase ไว้
    if len(cleaned) <= 4 and cleaned.isalpha():
        return cleaned.upper()
    return cleaned[0].upper() + cleaned[1:]


# ============================================================================
# AI prediction helper — local DB history first, ML model fallback
# ============================================================================

def predict_from_local_history(procedure: str, surgeon: str = None,
                                min_cases: int = 3) -> dict | None:
    """ทำนายเวลาผ่าตัดจากประวัติเคสที่ผ่าตัดเสร็จแล้วใน DB ห้องเล็ก

    Tier 1: surgeon × procedure (≥ min_cases)  → confidence "สูงมาก"
    Tier 2: procedure only (≥ min_cases)       → confidence "สูง"
    Returns None if insufficient local history (caller should fall back to ML).

    การ match ใช้ _normalize_procedure_name เพื่อรวม variants
    (เช่น "ESWL Right" + "ESWL" + "ESWL Lt" = canonical "ESWL")
    """
    if not procedure or not str(procedure).strip():
        return None

    target = _normalize_procedure_name(procedure)
    if target == 'UNKNOWN':
        return None

    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT procedure_name, surgeon_name, actual_duration_min
            FROM cases
            WHERE status = 'discharged'
              AND actual_duration_min IS NOT NULL
              AND actual_duration_min > 0
        """).fetchall()
    finally:
        conn.close()

    # Group local cases by canonical procedure name; keep matches only
    matching = []  # list of (surgeon_name, duration)
    for proc_raw, surg_raw, dur in rows:
        if _normalize_procedure_name(proc_raw) == target:
            matching.append((str(surg_raw or '').strip(), int(dur)))

    if not matching:
        return None

    surg_clean = (surgeon or '').strip()

    # Tier 1: surgeon × procedure
    if surg_clean:
        surg_durs = [d for s, d in matching if s == surg_clean]
        if len(surg_durs) >= min_cases:
            return {
                'predicted_min': int(round(statistics.median(surg_durs))),
                'confidence': 'สูงมาก',
                'tier': 1,
                'method_label': (f'ประวัติห้องเล็ก '
                                 f'(หมอ × หัตถการ, n={len(surg_durs)})'),
                'n_cases': len(surg_durs),
                'min_dur': min(surg_durs),
                'max_dur': max(surg_durs),
                'canonical': target,
            }

    # Tier 2: any surgeon, this procedure
    all_durs = [d for _, d in matching]
    if len(all_durs) >= min_cases:
        return {
            'predicted_min': int(round(statistics.median(all_durs))),
            'confidence': 'สูง',
            'tier': 2,
            'method_label': f'ประวัติห้องเล็ก (หัตถการ, n={len(all_durs)})',
            'n_cases': len(all_durs),
            'min_dur': min(all_durs),
            'max_dur': max(all_durs),
            'canonical': target,
        }

    return None


def clear_all_cases() -> int:
    """ลบเคสทั้งหมดในตาราง cases — return จำนวนเคสที่ลบ (เก็บ settings ไว้)"""
    conn = get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        conn.execute("DELETE FROM cases")
        conn.commit()
        return int(n)
    finally:
        conn.close()


def clear_cases_by_date_range(date_from: str, date_to: str) -> int:
    """ลบเคสในช่วงวันที่ที่ระบุ — return จำนวนเคสที่ลบ

    Args:
        date_from, date_to: 'YYYY-MM-DD' (inclusive)

    ไม่แตะ audit_log / room_settings — ลบแค่ cases ในช่วงเวลานั้น
    """
    conn = get_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE op_date BETWEEN ? AND ?",
            (date_from, date_to)).fetchone()[0]
        conn.execute(
            "DELETE FROM cases WHERE op_date BETWEEN ? AND ?",
            (date_from, date_to))
        conn.commit()
        return int(n)
    finally:
        conn.close()


def clear_all_data() -> dict:
    """ลบข้อมูลทุกอย่างในทุก table (clean wipe) — return จำนวนแต่ละ table

    ⚠️ ใช้ระวัง: ลบหมดจริง — เคส, audit_log, room_settings
    เหมาะกับการ reset ก่อน upload ข้อมูลใหม่ทั้งหมด

    หลังลบ + reboot Streamlit → _auto_import_historical() จะวิ่งใหม่
    เพราะ cases count = 0 → ดึงข้อมูลจาก historical_data/ อัตโนมัติ
    ถ้าไม่อยากให้ auto-import → upload CSV ผ่าน UI ก่อน reboot
    """
    conn = get_conn()
    try:
        result = {}
        # นับและลบทีละ table
        for tbl in ('cases', 'audit_log', 'room_settings'):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                conn.execute(f"DELETE FROM {tbl}")
                # reset auto-increment counter ด้วย (ถ้าใช้ INTEGER PRIMARY KEY)
                conn.execute(f"DELETE FROM sqlite_sequence WHERE name='{tbl}'")
                result[tbl] = int(n)
            except sqlite3.OperationalError:
                # table อาจยังไม่ถูก create ใน schema เก่า — ข้าม
                result[tbl] = 0
        conn.commit()
        # VACUUM ต้องรันนอก transaction
        conn.isolation_level = None
        conn.execute("VACUUM")
    finally:
        conn.close()
    # ตั้ง flag กัน auto-import วิ่งทับเมื่อ reboot
    # (จะถูกล้างเมื่อ user upload CSV ผ่าน UI ใน import_schedule)
    _set_app_setting('skip_auto_import', '1')
    return result


def get_cases_count() -> int:
    """Return total cases count (for confirmation UI before clearing)."""
    conn = get_conn()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0])
    finally:
        conn.close()


def get_db_table_counts() -> dict:
    """Return row count of every relevant table (for clean-wipe preview)."""
    conn = get_conn()
    try:
        out = {}
        for tbl in ('cases', 'audit_log', 'room_settings'):
            try:
                out[tbl] = int(conn.execute(
                    f"SELECT COUNT(*) FROM {tbl}").fetchone()[0])
            except sqlite3.OperationalError:
                out[tbl] = 0
        return out
    finally:
        conn.close()


def get_local_history_stats():
    """Return summary of how many procedures have ≥3 local cases (for diagnostics)."""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT procedure_name, surgeon_name, actual_duration_min
            FROM cases
            WHERE status = 'discharged'
              AND actual_duration_min IS NOT NULL
              AND actual_duration_min > 0
        """).fetchall()
    finally:
        conn.close()
    counts = {}
    for proc_raw, _surg, _dur in rows:
        c = _normalize_procedure_name(proc_raw)
        counts[c] = counts.get(c, 0) + 1
    return counts

DIVISIONS = ['ศัลยกรรมทั่วไป','ศัลยกรรมตกแต่ง','ระบบผิวหนัง',
             'ศัลยกรรมระบบทางเดินปัสสาวะ','ศัลยกรรมหู คอ จมูก',
             'ศัลยกรรมหลอดเลือด','ศัลยกรรมเลเซอร์',
             'กุมารเวชกรรม','ศัลยกรรมเด็ก','อื่นๆ']

DIV_CODE_MAP = {
    '72': 'กุมารเวชกรรม',
    '73': 'ศัลยกรรมเด็ก',
    '74': 'ศัลยกรรมตกแต่ง',
    '75': 'ศัลยกรรมทั่วไป',
    '76': 'ศัลยกรรมระบบทางเดินปัสสาวะ',
    '77': 'ศัลยกรรมหู คอ จมูก',
    '78': 'ศัลยกรรมหลอดเลือด',
    '79': 'ศัลยกรรมเลเซอร์',
    '701': 'ผิวหนัง',
}


PROCEDURE_COSTS = {
    'EXCISION': [2500, 5000, 7500],
    'I&D': [2000, 4000, 6000],
    'DEBRIDEMENT': [2500, 5000, 7500],
    'EC': [300, 600, 900, 1200, 2000],
    'EC.': [300, 600, 900, 1200, 2000],
    'OFF PERM': [1600, 3200],
    'FRENECTOMY': [1600, 3200],
    'FRENOLOTOMY': [1600, 3200],
    'NAIL EXTRACTION': [1000, 2000],
    'MORPHEUS': [10000, 20000, 30000],
}

PATHO_COSTS = [240, 500, 1000]


def lookup_cost(procedure_name: str) -> list:
    """Lookup treatment cost options by procedure name (case-insensitive, partial match).
    Returns list of price options, or empty list if not found."""
    if not procedure_name:
        return []
    p = str(procedure_name).strip().upper()
    # Exact match first
    if p in PROCEDURE_COSTS:
        return PROCEDURE_COSTS[p]
    # Partial match
    for key, costs in PROCEDURE_COSTS.items():
        if key in p or p in key:
            return costs
    return []


def div_name(code):
    """Convert division code to Thai name."""
    if not code:
        return '-'
    return DIV_CODE_MAP.get(str(code).strip(), str(code))
VALID_STATUSES = ('scheduled', 'arrived', 'in_or', 'post_op', 'discharged', 'cancelled')

# Valid status transitions (from → allowed to)
STATUS_TRANSITIONS = {
    'scheduled':  ('arrived', 'cancelled'),
    'arrived':    ('in_or', 'cancelled', 'scheduled'),       # can revert to scheduled
    'in_or':      ('post_op', 'cancelled', 'arrived'),       # can revert to arrived
    'post_op':    ('discharged', 'cancelled', 'in_or'),      # can revert to in_or
    'discharged': ('post_op',),                               # can revert to post_op
    'cancelled':  ('scheduled',),                             # can un-cancel
}

# Whitelist of columns allowed in update_case()
_UPDATABLE_COLS = {
    'status', 'cancel_reason', 'post_op_dest', 'arrived_at', 'in_or_at',
    'op_end_at', 'discharged_at', 'wait_min', 'treatment_cost', 'patho_cost',
    'procedure_name', 'surgeon_name', 'division_code', 'case_category',
    'patient_type', 'op_type', 'estimated_time', 'procnote', 'anesthesia_type',
    'ai_predicted_min', 'user_override_min', 'actual_duration_min',
    'scrub_nurse', 'circ_nurse', 'room_no', 'name', 'hn', 'an',
    'oss_visited', 'oss_by_or', 'or_pre_visit', 'post_call', 'post_call_status',
}


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


class db_session:
    """Context manager for safe DB connections. Auto-commits on success, rollback on error."""
    def __init__(self):
        self.conn = None
    def __enter__(self):
        self.conn = get_conn()
        return self.conn
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
            self.conn.close()
        return False


def init_db():
    """Create table + migrate old schema if needed."""
    conn = get_conn()
    # Main table (no CHECK on status — enforce in Python for flexibility)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cases (
            case_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            op_date          TEXT NOT NULL,
            name             TEXT,
            hn               TEXT,
            an               TEXT,
            diagnosis        TEXT,
            procedure_name   TEXT NOT NULL,
            surgeon_name     TEXT,
            division_code    TEXT,
            case_category    TEXT,
            patient_type     TEXT,
            op_type          TEXT,
            estimated_time   TEXT,
            procnote         TEXT,

            -- Status
            status           TEXT DEFAULT 'scheduled',
            cancel_reason    TEXT,

            -- Checkboxes
            oss_visited      INTEGER DEFAULT 0,
            oss_by_or        INTEGER DEFAULT 0,
            or_pre_visit     INTEGER DEFAULT 0,
            post_call        INTEGER DEFAULT 0,
            post_call_status TEXT,

            -- Timing & AI
            ai_predicted_min INTEGER,
            user_override_min INTEGER,
            actual_duration_min INTEGER,
            scrub_nurse      TEXT,
            circ_nurse       TEXT,
            anesthesia_type  TEXT,
            wait_min         INTEGER DEFAULT 0,
            room_no          INTEGER DEFAULT 1,

            -- Workflow timestamps (v2)
            arrived_at       TEXT,
            in_or_at         TEXT,
            op_end_at        TEXT,
            discharged_at    TEXT,
            post_op_dest     TEXT DEFAULT 'transfer',
            treatment_cost   INTEGER DEFAULT 0,

            -- Scheduled surgeon (from schedule.csv) — ไม่ overwrite ตอน intraop import
            scheduled_surgeon TEXT,

            -- Meta
            created_at       TEXT DEFAULT (datetime('now','localtime')),
            updated_at       TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_cases_op_date ON cases(op_date);
        CREATE INDEX IF NOT EXISTS idx_cases_status  ON cases(status);
        CREATE INDEX IF NOT EXISTS idx_cases_hn      ON cases(hn);
        CREATE INDEX IF NOT EXISTS idx_cases_date_status ON cases(op_date, status);

        -- Audit log: ใครแก้อะไรเมื่อไหร่
        CREATE TABLE IF NOT EXISTS audit_log (
            log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id     INTEGER,
            action      TEXT NOT NULL,
            old_value   TEXT,
            new_value   TEXT,
            detail      TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log(case_id);

        -- Prediction log: เก็บทุก ML prediction เพื่อ retrain + วิจัย
        CREATE TABLE IF NOT EXISTS prediction_log (
            pred_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id          INTEGER,
            model_version    TEXT,
            procedure_name   TEXT,
            surgeon_name     TEXT,
            predicted_min    INTEGER,
            actual_min       INTEGER,
            abs_error        INTEGER,
            confidence       TEXT,
            created_at       TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_pred_case ON prediction_log(case_id);

        -- Backup log
        CREATE TABLE IF NOT EXISTS backup_log (
            backup_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            backup_path TEXT,
            row_count   INTEGER,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
        -- Room settings: persist nurse assignments per room
        CREATE TABLE IF NOT EXISTS room_settings (
            room_no     INTEGER PRIMARY KEY,
            enabled     INTEGER DEFAULT 1,
            scrub_json  TEXT DEFAULT '["",""]',
            circ_json   TEXT DEFAULT '["","","",""]',
            updated_at  TEXT DEFAULT (datetime('now','localtime'))
        );
        -- App-level settings (key/value) — used for flags like skip_auto_import
        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)

    # Migration: add columns if upgrading from v1
    _migrate_v2(conn)

    # Re-classify patient_type for existing cases (fix old bad logic)
    _reclassify_patient_type(conn)

    # Backfill AI predictions for cases that don't have one yet
    _backfill_ai_predictions(conn)

    # Fix negative durations from timezone bug
    _fix_negative_durations(conn)

    conn.close()

    # Auto-import historical data if DB is empty
    _auto_import_historical()


def _get_app_setting(key: str, default: str = '') -> str:
    """Read an app_settings value (returns default if missing)."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default
    except sqlite3.OperationalError:
        # table may not exist yet during migration
        return default
    finally:
        conn.close()


def _set_app_setting(key: str, value: str) -> None:
    """Write an app_settings value (upsert)."""
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (key, str(value)))
        conn.commit()
    finally:
        conn.close()


def _auto_import_historical():
    """Auto-import historical CSV data on first boot (when DB is empty).

    ป้องกันด้วย flag `skip_auto_import` ใน app_settings:
    - ถ้า flag = '1' → ข้าม (เคารพการตัดสินใจของ user ที่กด Clean Wipe)
    - flag จะถูกล้างอัตโนมัติเมื่อ user upload CSV ผ่าน UI
    """
    import os as _os
    if _get_app_setting('skip_auto_import', '0') == '1':
        print("[AUTO-IMPORT] Skipped — user requested clean DB "
              "(flag set after Clean Wipe; will clear when user uploads CSV)")
        return
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    conn.close()
    if count > 0:
        return  # DB already has data, skip

    base = _os.path.dirname(_os.path.abspath(__file__))
    hist_dir = _os.path.join(base, 'historical_data')
    sched_path = _os.path.join(hist_dir, 'sched_historical.csv')
    intra_path = _os.path.join(hist_dir, 'intra_historical.csv')

    if not _os.path.exists(sched_path) or not _os.path.exists(intra_path):
        return

    try:
        from import_historical import import_historical
        n, s, _ = import_historical(sched_path, intra_path, dry_run=False)
        print(f"[AUTO-IMPORT] Loaded {n} historical cases ({s} skipped)")
    except Exception as e:
        print(f"[AUTO-IMPORT] Error: {e}")


def _reclassify_patient_type(conn):
    """Re-run patient_type classification on all cases using current logic."""
    rows = conn.execute(
        "SELECT case_id, an, estimated_time, procnote FROM cases"
    ).fetchall()
    for r in rows:
        cid = r[0]
        an = str(r[1] or '').strip()
        est = str(r[2] or '').strip()
        note = str(r[3] or '').strip()

        if an and an.upper() not in ('', 'NAN', 'NONE', '-'):
            pt = 'IPD'
        elif _is_after_hours(est) or 'นอกเวลา' in note:
            pt = 'นอกเวลา'
        else:
            pt = 'OPD'
        conn.execute("UPDATE cases SET patient_type=? WHERE case_id=?", (pt, cid))
    conn.commit()


def _migrate_v2(conn):
    """Migrate v1 table (with CHECK constraint) to v2 (no CHECK on status)."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(cases)").fetchall()}

    # Check if table has CHECK constraint on status
    has_check = False
    try:
        tbl_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='cases'"
        ).fetchone()
        if tbl_sql and tbl_sql[0] and 'CHECK' in tbl_sql[0].upper():
            has_check = True
    except Exception:
        pass

    # Add treatment_cost column if missing (simple migration)
    if 'treatment_cost' not in existing:
        try:
            conn.execute("ALTER TABLE cases ADD COLUMN treatment_cost INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass
        existing.add('treatment_cost')

    # Add patho_cost column if missing
    if 'patho_cost' not in existing:
        try:
            conn.execute("ALTER TABLE cases ADD COLUMN patho_cost INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass
        existing.add('patho_cost')

    # Add diagnosis column if missing
    if 'diagnosis' not in existing:
        try:
            conn.execute("ALTER TABLE cases ADD COLUMN diagnosis TEXT")
            conn.commit()
        except Exception:
            pass
        existing.add('diagnosis')

    # Add scheduled_surgeon column if missing
    # ใช้เก็บแพทย์ที่ "set" ผ่าตัด (จาก schedule.csv) — ไม่ overwrite ตอน intraop import
    if 'scheduled_surgeon' not in existing:
        try:
            conn.execute("ALTER TABLE cases ADD COLUMN scheduled_surgeon TEXT")
            # Backfill: ใช้ surgeon_name ปัจจุบันเป็น scheduled_surgeon (best-effort)
            conn.execute(
                "UPDATE cases SET scheduled_surgeon = surgeon_name "
                "WHERE scheduled_surgeon IS NULL AND surgeon_name IS NOT NULL")
            conn.commit()
        except Exception:
            pass
        existing.add('scheduled_surgeon')

    needs_recreate = has_check or ('arrived_at' not in existing)

    if needs_recreate:
        # 1. Rename old table
        try:
            conn.execute("ALTER TABLE cases RENAME TO cases_v1")
        except Exception:
            return  # no old table, fresh install

        # 2. Create new table (already done by init_db above — but it was
        #    blocked by the old table existing). Drop and recreate.
        conn.execute("DROP TABLE IF EXISTS cases")
        conn.executescript("""
            CREATE TABLE cases (
                case_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                op_date          TEXT NOT NULL,
                name             TEXT,
                hn               TEXT,
                an               TEXT,
                diagnosis        TEXT,
                procedure_name   TEXT NOT NULL,
                surgeon_name     TEXT,
                division_code    TEXT,
                case_category    TEXT,
                patient_type     TEXT,
                op_type          TEXT,
                estimated_time   TEXT,
                procnote         TEXT,
                status           TEXT DEFAULT 'scheduled',
                cancel_reason    TEXT,
                oss_visited      INTEGER DEFAULT 0,
                oss_by_or        INTEGER DEFAULT 0,
                or_pre_visit     INTEGER DEFAULT 0,
                post_call        INTEGER DEFAULT 0,
                post_call_status TEXT,
                ai_predicted_min INTEGER,
                user_override_min INTEGER,
                actual_duration_min INTEGER,
                scrub_nurse      TEXT,
                circ_nurse       TEXT,
                anesthesia_type  TEXT,
                wait_min         INTEGER DEFAULT 0,
                room_no          INTEGER DEFAULT 1,
                arrived_at       TEXT,
                in_or_at         TEXT,
                op_end_at        TEXT,
                discharged_at    TEXT,
                post_op_dest     TEXT DEFAULT 'transfer',
                treatment_cost   INTEGER DEFAULT 0,
                patho_cost       INTEGER DEFAULT 0,
                created_at       TEXT DEFAULT (datetime('now','localtime')),
                updated_at       TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_cases_op_date ON cases(op_date);
            CREATE INDEX IF NOT EXISTS idx_cases_status  ON cases(status);
            CREATE INDEX IF NOT EXISTS idx_cases_hn      ON cases(hn);
        """)

        # 3. Copy old data, mapping 'completed' → 'discharged'
        old_cols = [row[1] for row in conn.execute("PRAGMA table_info(cases_v1)").fetchall()]
        new_cols_set = {row[1] for row in conn.execute("PRAGMA table_info(cases)").fetchall()}
        shared = [c for c in old_cols if c in new_cols_set]
        cols_str = ', '.join(shared)

        conn.execute(f"""
            INSERT INTO cases ({cols_str})
            SELECT {cols_str} FROM cases_v1
        """)
        # Fix old status values
        conn.execute("UPDATE cases SET status='discharged' WHERE status='completed'")
        conn.execute("DROP TABLE cases_v1")
        conn.commit()

    # Add UNIQUE index to prevent duplicate imports (safe — ignores if exists)
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_unique_import ON cases(op_date, hn, procedure_name)")
        conn.commit()
    except Exception:
        pass  # might fail if duplicates already exist


# ============================================================================
# CLASSIFY
# ============================================================================

def classify_case(row: dict) -> dict:
    result = {}

    req_date = row.get('requested_date') or row.get('reqdate') or row.get('rqdate') or ''
    req_time = row.get('request_time') or row.get('reqtime') or row.get('rqtime') or ''
    op_date = row.get('op_date', '')

    # Walk-in = นัดวันเดียวกับวันผ่าตัด (หรือไม่มีวันนัด)
    # เคสนัดหมาย = นัดล่วงหน้าอย่างน้อย 1 วัน
    is_scheduled = False
    if req_date and op_date:
        try:
            rd = pd.to_datetime(str(req_date), dayfirst=True, errors='coerce')
            od = pd.to_datetime(str(op_date), errors='coerce')
            if pd.notna(rd) and pd.notna(od) and (od - rd).days >= 1:
                is_scheduled = True
        except:
            pass
    result['case_category'] = 'เคสนัดหมาย' if is_scheduled else 'Walk-in'

    an = str(row.get('an', '') or '').strip()
    est_time = str(row.get('estimated_time', '') or row.get('estmtime', '') or row.get('esttime', '') or '').strip()
    procnote = str(row.get('procnote', '') or '').strip()

    if an and an.upper() not in ('', 'NAN', 'NONE', '-'):
        result['patient_type'] = 'IPD'
    elif _is_after_hours(est_time) or '\u0e19\u0e2d\u0e01\u0e40\u0e27\u0e25\u0e32' in procnote:
        result['patient_type'] = '\u0e19\u0e2d\u0e01\u0e40\u0e27\u0e25\u0e32'
    else:
        result['patient_type'] = 'OPD'

    pn_lower = procnote.lower()
    if 'or \u0e40\u0e22\u0e35\u0e48\u0e22\u0e21' in pn_lower or 'or \u0e40\u0e25\u0e47\u0e01\u0e40\u0e22\u0e35\u0e48\u0e22\u0e21' in pn_lower:
        result['oss_by_or'] = 1
        result['or_pre_visit'] = 1
    else:
        result['oss_by_or'] = 0

    return result


def _is_after_hours(est_time: str) -> bool:
    """Check if estimated time is after-hours (>= 16:00).
    Handles formats: HH:MM, HH.MM, HHMMSS (e.g. 133000), HHMM."""
    if not est_time:
        return False
    try:
        t = str(est_time).strip()
        # Remove .0 suffix from float-like strings
        if t.endswith('.0'):
            t = t[:-2]
        # Format: HHMMSS (6 digits) or HHMM (4 digits) — no separator
        if t.isdigit() and len(t) >= 4:
            h = int(t[:2]) if len(t) >= 5 else int(t[:1])
            # 6-digit: 133000 → HH=13, 80000 → need to handle
            if len(t) == 6:
                h = int(t[:2])
            elif len(t) == 5:
                # e.g. 80000 → 08:00:00 (leading zero dropped)
                h = int(t[:1])
            elif len(t) == 4:
                h = int(t[:2])
            return h >= 16
        # Format with separator: HH:MM or HH.MM
        t = t.replace('.', ':')
        h = int(t.split(':')[0])
        return h >= 16
    except:
        return False


def auto_assign_room(procedure_name: str) -> int:
    """Auto-assign room based on procedure name.
    ห้อง 1: Morpheus, Laser, Cooltech
    ห้อง 3: ESWL
    ห้อง 4-5: อื่นๆ (default ห้อง 4)
    """
    if not procedure_name:
        return 4
    p = str(procedure_name).upper()
    if any(kw in p for kw in ('MORPHEUS', 'LASER', 'COOLTECH')):
        return 1
    if 'ESWL' in p:
        return 3
    return 4


def _backfill_ai_predictions(conn):
    """Fill ai_predicted_min for existing cases that have NULL."""
    rows = conn.execute(
        "SELECT case_id, procedure_name, surgeon_name, division_code, op_type, op_date "
        "FROM cases WHERE ai_predicted_min IS NULL"
    ).fetchall()
    if not rows:
        return
    for r in rows:
        cid, proc, surg, div, optype, op_date = r
        ai_min = _predict_for_case(proc or 'UNKNOWN', surg or 'UNKNOWN',
                                   div or '75', optype or 'elective', op_date)
        if ai_min is not None:
            conn.execute("UPDATE cases SET ai_predicted_min=? WHERE case_id=?",
                         (ai_min, cid))
    conn.commit()


def _fix_negative_durations(conn):
    """Fix negative actual_duration_min and wait_min caused by timezone bug.
    Recalculate from stored timestamps (in_or_at - arrived_at, op_end_at - in_or_at)."""
    # Fix actual_duration_min
    rows = conn.execute(
        "SELECT case_id, in_or_at, op_end_at FROM cases "
        "WHERE actual_duration_min IS NOT NULL AND actual_duration_min < 0 "
        "AND in_or_at IS NOT NULL AND op_end_at IS NOT NULL"
    ).fetchall()
    for r in rows:
        try:
            ior = datetime.strptime(r['in_or_at'], '%Y-%m-%d %H:%M:%S')
            end = datetime.strptime(r['op_end_at'], '%Y-%m-%d %H:%M:%S')
            dur = int((end - ior).total_seconds() / 60)
            if dur >= 0:
                conn.execute("UPDATE cases SET actual_duration_min=? WHERE case_id=?",
                             (dur, r['case_id']))
        except Exception:
            pass

    # Fix wait_min
    rows2 = conn.execute(
        "SELECT case_id, arrived_at, in_or_at FROM cases "
        "WHERE wait_min IS NOT NULL AND wait_min < 0 "
        "AND arrived_at IS NOT NULL AND in_or_at IS NOT NULL"
    ).fetchall()
    for r in rows2:
        try:
            arr = datetime.strptime(r['arrived_at'], '%Y-%m-%d %H:%M:%S')
            ior = datetime.strptime(r['in_or_at'], '%Y-%m-%d %H:%M:%S')
            wait = int((ior - arr).total_seconds() / 60)
            if wait >= 0:
                conn.execute("UPDATE cases SET wait_min=? WHERE case_id=?",
                             (wait, r['case_id']))
        except Exception:
            pass

    conn.commit()


# ============================================================================
# AI PREDICTION HELPER
# ============================================================================

def _predict_for_case(procedure, surgeon, division, optype, op_date_str):
    """Call predict_surgical_time and return predicted minutes (int or None)."""
    try:
        from minor_or_core import predict_surgical_time
        from datetime import datetime as _dt
        op_dt = _dt.strptime(str(op_date_str), '%Y-%m-%d') if op_date_str else _dt.now()
        result = predict_surgical_time(
            procedure=procedure or 'UNKNOWN',
            age=40,  # default age (not in schedule CSV)
            surgeon=surgeon or 'UNKNOWN',
            division=str(division or '75'),
            op_hour=op_dt.hour if op_dt.hour >= 7 else 9,
            optype=optype or 'elective',
            op_date=op_dt,
        )
        pred = result.get('predicted_min')
        return int(round(pred)) if pred else None
    except Exception:
        return None


# ============================================================================
# IMPORT
# ============================================================================

def import_schedule(df: pd.DataFrame, op_date: str) -> int:
    conn = get_conn()
    count = 0

    col_map = {
        'name': ['dspname', 'name', '\u0e0a\u0e37\u0e48\u0e2d', 'ptname', 'patient_name', 'hn_name'],
        'hn': ['hn', 'HN', 'hosnum'],
        'an': ['an', 'AN', 'admitnum', 'an.1'],
        'diagnosis': ['icd10_name', 'icd10name', 'icd10nm', 'diag', 'diagnosis',
                       'prediag', 'pre_diag', 'วินิจฉัย'],
        'procedure_name': ['icd9cm_name', 'icd9cmnm', 'procedure', 'procedure_name',
                           'procname', '\u0e2b\u0e31\u0e15\u0e16\u0e01\u0e32\u0e23', 'opname'],
        'procedure_icd9': ['icd9cm'],
        'surgeon_name': ['surgstfnm', 'dctnm', 'surgeon', 'surgeon_name',
                         '\u0e41\u0e1e\u0e17\u0e22\u0e4c', 'doctor'],
        'division_code': ['division', 'div', 'divname', '\u0e2a\u0e32\u0e02\u0e32', 'specialty'],
        'estimated_time': ['estmtime', 'estimated_time', 'esttime', 'est_time',
                           'opetime', '\u0e40\u0e27\u0e25\u0e32\u0e1b\u0e23\u0e30\u0e21\u0e32\u0e13'],
        'procnote': ['procnote', 'note', '\u0e2b\u0e21\u0e32\u0e22\u0e40\u0e2b\u0e15\u0e38', 'remark'],
        'op_type': ['optype_var', 'optypenm', 'op_type', 'optype', 'case_type',
                    '\u0e1b\u0e23\u0e30\u0e40\u0e20\u0e17'],
        'requested_date': ['reqdate', 'requested_date', 'rqdate', 'request_date'],
        'request_time': ['reqtime', 'request_time', 'rqtime'],
        'anesthesia_type': ['anesthesia', 'anes', 'an_type', 'anesthesia_type'],
    }

    def find_col(df, aliases):
        for a in aliases:
            for c in df.columns:
                if c.strip().lower() == a.lower():
                    return c
        return None

    mapped = {}
    for key, aliases in col_map.items():
        found = find_col(df, aliases)
        if found:
            mapped[key] = found

    import_schedule._last_mapped = dict(mapped)
    import_schedule._last_csv_cols = list(df.columns)

    for _, row in df.iterrows():
        data = {key: str(row.get(mapped.get(key, ''), '') or '').strip()
                for key in col_map}
        data['op_date'] = op_date

        proc = data.get('procedure_name', '').strip()
        if not proc or proc.upper() in ('NAN', 'NONE', ''):
            continue

        cls = classify_case(data)
        data.update(cls)

        existing = conn.execute(
            "SELECT case_id FROM cases WHERE op_date=? AND hn=? AND procedure_name=?",
            (op_date, data.get('hn', ''), proc)
        ).fetchone()
        if existing:
            # Backfill diagnosis for existing cases if missing
            _diag_tmp = data.get('diagnosis', '').strip()
            if _diag_tmp and _diag_tmp.upper() not in ('', 'NAN', 'NONE', '-'):
                conn.execute(
                    "UPDATE cases SET diagnosis=? WHERE case_id=? AND (diagnosis IS NULL OR diagnosis='')",
                    (_diag_tmp, existing[0])
                )
            # 🆕 Update scheduled_surgeon จาก CSV เสมอ (fix migration backfill)
            # — re-upload schedule จะ overwrite ค่าที่ผิดของเดิม
            _sched_surg_new = (data.get('surgeon_name') or '').strip()
            if _sched_surg_new and _sched_surg_new.upper() not in ('NAN', 'NONE'):
                conn.execute(
                    "UPDATE cases SET scheduled_surgeon=? WHERE case_id=?",
                    (_sched_surg_new, existing[0])
                )
            continue

        an_val = data.get('an')
        if an_val in ('', 'nan', 'None'):
            an_val = None

        # AI prediction — prefer ICD-9 full name for better matching
        proc_for_ai = data.get('procedure_icd9', '').strip()
        if not proc_for_ai or proc_for_ai.upper() in ('NAN', 'NONE', ''):
            proc_for_ai = proc  # fallback to icd9cm_name
        ai_min = _predict_for_case(proc_for_ai, data.get('surgeon_name', ''),
                                   data.get('division_code', '75'),
                                   data.get('op_type', 'elective'), op_date)

        room = auto_assign_room(proc)
        diag_val = data.get('diagnosis', '').strip()
        if diag_val.upper() in ('', 'NAN', 'NONE', '-'):
            diag_val = None
        # scheduled_surgeon = แพทย์ที่ set ผ่าตัด (จาก schedule)
        # surgeon_name = ตอน import schedule = ผู้ set
        # หลัง intraop import → surgeon_name จะถูกอัพเดตเป็นผู้ทำจริง
        _sched_surg = data.get('surgeon_name')
        conn.execute("""
            INSERT INTO cases (op_date, name, hn, an, diagnosis, procedure_name,
                              surgeon_name, scheduled_surgeon,
                              division_code, case_category, patient_type,
                              op_type, estimated_time, procnote, anesthesia_type,
                              oss_by_or, or_pre_visit, ai_predicted_min, room_no)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            op_date, data.get('name'), data.get('hn'),
            an_val, diag_val, proc, _sched_surg, _sched_surg,
            data.get('division_code'),
            cls['case_category'], cls['patient_type'], data.get('op_type'),
            data.get('estimated_time'), data.get('procnote'),
            data.get('anesthesia_type'),
            cls.get('oss_by_or', 0), cls.get('or_pre_visit', 0),
            ai_min, room,
        ))
        count += 1

    conn.commit()
    conn.close()
    # User upload สำเร็จ → ล้าง flag กัน auto-import (ถ้าเคยกด Clean Wipe ไว้)
    if count > 0:
        try:
            _set_app_setting('skip_auto_import', '0')
        except Exception:
            pass
    return count


def add_walkin_case(op_date, name, hn, procedure, surgeon, division,
                    patient_type='OPD', an=None):
    conn = get_conn()
    proc_clean = procedure.strip().upper()
    ai_min = _predict_for_case(proc_clean, surgeon, division, 'elective', op_date)
    room = auto_assign_room(proc_clean)
    cur = conn.execute("""
        INSERT INTO cases (op_date, name, hn, an, procedure_name, surgeon_name,
                          division_code, case_category, patient_type, ai_predicted_min, room_no)
        VALUES (?,?,?,?,?,?,?,'Walk-in',?,?,?)
    """, (op_date, name, hn, an, proc_clean, surgeon,
          division, patient_type, ai_min, room))
    cid = cur.lastrowid
    conn.commit()
    conn.close()
    # User เพิ่ม walk-in สำเร็จ → ล้าง flag กัน auto-import
    try:
        _set_app_setting('skip_auto_import', '0')
    except Exception:
        pass
    return cid


# ============================================================================
# WORKFLOW ACTIONS (step-by-step)
# ============================================================================

def _now():
    from datetime import timezone, timedelta as _td
    _TH = timezone(_td(hours=7))
    return datetime.now(_TH).strftime('%Y-%m-%d %H:%M:%S')


def _now_dt():
    """Return current datetime in Thailand timezone (naive, for diff calculations)."""
    from datetime import timezone, timedelta as _td
    _TH = timezone(_td(hours=7))
    return datetime.now(_TH).replace(tzinfo=None)


def _log_prediction(conn, case_id, procedure, surgeon, predicted, actual):
    """Log ML prediction vs actual to prediction_log for research."""
    try:
        from minor_or_core import load_ml_assets
        assets = load_ml_assets()
        model_ver = assets.get('model_data', {}).get('model_name', 'unknown') if assets.get('model_loaded') else 'none'
    except Exception:
        model_ver = 'unknown'
    try:
        conn.execute(
            "INSERT INTO prediction_log (case_id, model_version, procedure_name, surgeon_name, "
            "predicted_min, actual_min, abs_error, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (case_id, model_ver, procedure, surgeon, predicted, actual,
             abs(actual - predicted) if predicted else None, _now()))
    except Exception:
        pass


def _validate_transition(conn, case_id: int, new_status: str):
    """Validate status transition. Returns current status or raises ValueError."""
    row = conn.execute("SELECT status FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not row:
        raise ValueError(f"Case {case_id} not found")
    cur = row['status']
    allowed = STATUS_TRANSITIONS.get(cur, ())
    if new_status not in allowed:
        raise ValueError(f"Cannot transition {cur} → {new_status} (allowed: {allowed})")
    return cur


def _log_audit(conn, case_id: int, action: str, old_val: str = None, new_val: str = None, detail: str = None):
    """Write to audit_log table."""
    try:
        conn.execute(
            "INSERT INTO audit_log (case_id, action, old_value, new_value, detail, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (case_id, action, old_val, new_val, detail, _now()))
    except Exception:
        pass  # audit log should never break main flow


def mark_arrived(case_id: int):
    """Patient arrived at OR waiting area."""
    with db_session() as conn:
        old = _validate_transition(conn, case_id, 'arrived')
        conn.execute("UPDATE cases SET status='arrived', arrived_at=?, updated_at=? WHERE case_id=?",
                     (_now(), _now(), case_id))
        _log_audit(conn, case_id, 'status_change', old, 'arrived')


def mark_in_or(case_id: int):
    """Patient enters operating room."""
    with db_session() as conn:
        old = _validate_transition(conn, case_id, 'in_or')
        row = conn.execute("SELECT arrived_at FROM cases WHERE case_id=?", (case_id,)).fetchone()
        wait = 0
        if row and row['arrived_at']:
            try:
                arr = datetime.strptime(row['arrived_at'], '%Y-%m-%d %H:%M:%S')
                wait = int((_now_dt() - arr).total_seconds() / 60)
            except Exception:
                pass
        conn.execute("""UPDATE cases SET status='in_or', in_or_at=?, wait_min=?, updated_at=?
                        WHERE case_id=?""", (_now(), wait, _now(), case_id))
        _log_audit(conn, case_id, 'status_change', old, 'in_or', f'wait={wait}min')


def mark_op_end(case_id: int, dest: str = 'transfer'):
    """Surgery finished. dest = 'transfer' or 'recovery'."""
    with db_session() as conn:
        old = _validate_transition(conn, case_id, 'post_op')
        row = conn.execute("SELECT in_or_at, procedure_name, surgeon_name, ai_predicted_min FROM cases WHERE case_id=?",
                           (case_id,)).fetchone()
        dur = 0
        if row and row['in_or_at']:
            try:
                ior = datetime.strptime(row['in_or_at'], '%Y-%m-%d %H:%M:%S')
                dur = int((_now_dt() - ior).total_seconds() / 60)
            except Exception:
                pass
        conn.execute("""UPDATE cases SET status='post_op', op_end_at=?,
                        actual_duration_min=?, post_op_dest=?, updated_at=?
                        WHERE case_id=?""", (_now(), dur, dest, _now(), case_id))
        _log_audit(conn, case_id, 'status_change', old, 'post_op', f'dur={dur}min dest={dest}')

        # Log prediction vs actual
        if row and row['ai_predicted_min'] and dur > 0:
            _log_prediction(conn, case_id,
                            row['procedure_name'], row['surgeon_name'],
                            int(row['ai_predicted_min']), dur)


def mark_discharged(case_id: int):
    """Patient discharged from transfer area."""
    with db_session() as conn:
        old = _validate_transition(conn, case_id, 'discharged')
        conn.execute("""UPDATE cases SET status='discharged', discharged_at=?, updated_at=?
                        WHERE case_id=?""", (_now(), _now(), case_id))
        _log_audit(conn, case_id, 'status_change', old, 'discharged')


def cancel_case(case_id: int, reason: str = None):
    with db_session() as conn:
        old = _validate_transition(conn, case_id, 'cancelled')
        conn.execute("UPDATE cases SET status='cancelled', cancel_reason=?, updated_at=? WHERE case_id=?",
                     (reason, _now(), case_id))
        _log_audit(conn, case_id, 'cancelled', old, 'cancelled', reason)


# Backward compat
def mark_done(case_id: int):
    mark_op_end(case_id, 'transfer')
    mark_discharged(case_id)


# ============================================================================
# READ / QUERY
# ============================================================================

def get_cases(op_date: str = None, status: str = None) -> pd.DataFrame:
    with db_session() as conn:
        q = "SELECT * FROM cases WHERE 1=1"
        params = []
        if op_date:
            q += " AND op_date=?"
            params.append(op_date)
        if status:
            q += " AND status=?"
            params.append(status)
        q += " ORDER BY case_id"
        return pd.read_sql_query(q, conn, params=params)


def get_pending_calls(days_back: int = 7) -> pd.DataFrame:
    with db_session() as conn:
        cutoff = (_now_dt() - timedelta(days=days_back)).strftime('%Y-%m-%d')
        return pd.read_sql_query("""
            SELECT * FROM cases
            WHERE status='discharged' AND patient_type != 'IPD'
            AND post_call=0 AND op_date >= ?
            ORDER BY op_date, case_id
        """, conn, params=[cutoff])


def update_case(case_id: int, **kwargs):
    """Update case fields â with column whitelist to prevent SQL injection."""
    # Filter to only allowed columns
    safe = {k: v for k, v in kwargs.items() if k in _UPDATABLE_COLS}
    if not safe:
        return
    with db_session() as conn:
        sets = ', '.join(f"{k}=?" for k in safe)
        vals = list(safe.values()) + [_now(), case_id]
        conn.execute(f"UPDATE cases SET {sets}, updated_at=? WHERE case_id=?", vals)
        # Audit: log each changed field
        for k, v in safe.items():
            _log_audit(conn, case_id, f'update_{k}', None, str(v)[:100])


def update_checkbox(case_id: int, field: str, value: int):
    if field not in _UPDATABLE_COLS:
        return
    update_case(case_id, **{field: value})


def update_postcall(case_id: int, status_text: str):
    update_case(case_id, post_call=1, post_call_status=status_text)


def get_summary(date_from=None, date_to=None) -> dict:
    """สรุปยอดสะสม — ใช้ filter เดียวกับ KPI Highlights
    (status IN post_op/discharged/done) เพื่อให้ตัวเลข "เคสรวม" = "เคสสะสม" ตรงกัน
    เคสสะสม = เคสที่มี **ทั้ง schedule + intraop data** (ผ่าตัดสำเร็จจริง)
    """
    conn = get_conn()  # read-only, no commit needed
    # 🆕 ใช้ DONE filter เดียวกับ KPI → ตัวเลขตรงกันทุกที่
    where = f"WHERE status IN {_DONE_STATUSES}"
    params = []
    if date_from:
        where += " AND op_date >= ?"
        params.append(date_from)
    if date_to:
        where += " AND op_date <= ?"
        params.append(date_to)

    def q1(sql):
        return conn.execute(sql, params).fetchone()[0]

    total = q1(f"SELECT COUNT(*) FROM cases {where}")
    completed = total  # เคสในรายการนี้ = เคสสำเร็จทั้งหมด
    # cancelled — query แยก (ไม่นับใน total) เพื่อแสดงเป็นข้อมูลเสริม
    cancelled_where = where.replace(f"status IN {_DONE_STATUSES}", "status='cancelled'")
    cancelled = q1(f"SELECT COUNT(*) FROM cases {cancelled_where}")
    n_set = q1(f"SELECT COUNT(*) FROM cases {where} AND case_category IN ('SET','เคสนัดหมาย')")
    n_walkin = q1(f"SELECT COUNT(*) FROM cases {where} AND case_category IN ('WALK-IN','Walk-in')")
    n_opd = q1(f"SELECT COUNT(*) FROM cases {where} AND patient_type='OPD'")
    n_ipd = q1(f"SELECT COUNT(*) FROM cases {where} AND patient_type='IPD'")
    n_after = q1(f"SELECT COUNT(*) FROM cases {where} AND patient_type='นอกเวลา'")

    top_procs = pd.read_sql_query(f"""
        SELECT UPPER(procedure_name) as procedure_name, COUNT(*) as n FROM cases {where}
        AND status != 'cancelled'
        GROUP BY UPPER(procedure_name) ORDER BY n DESC LIMIT 5
    """, conn, params=params)

    div_stats = pd.read_sql_query(f"""
        SELECT division_code, COUNT(*) as n FROM cases {where}
        AND status != 'cancelled'
        GROUP BY division_code ORDER BY n DESC
    """, conn, params=params)


    active_w = f"{where} AND status != 'cancelled'"
    oss_needed = q1(f"SELECT COUNT(*) FROM cases {active_w} AND oss_by_or=0")
    oss_done = q1(f"SELECT COUNT(*) FROM cases {active_w} AND oss_by_or=0 AND oss_visited=1")
    or_visit_total = q1(f"SELECT COUNT(*) FROM cases {active_w}")
    or_visit_done = q1(f"SELECT COUNT(*) FROM cases {active_w} AND or_pre_visit=1")
    call_needed = q1(f"SELECT COUNT(*) FROM cases {active_w} AND patient_type != 'IPD' AND status='discharged'")
    called = q1(f"SELECT COUNT(*) FROM cases {active_w} AND patient_type != 'IPD' AND post_call=1")
    call_ok = 0
    call_miss = 0

    # AI accuracy — เฉพาะเคสในเวลาเท่านั้น (นอกเวลาไม่มี AI prediction)
    ai_df = pd.read_sql_query(f"""
        SELECT ai_predicted_min, actual_duration_min,
               procedure_name, surgeon_name, division_code
        FROM cases {where}
        AND status IN ('post_op','discharged')
        AND ai_predicted_min IS NOT NULL
        AND actual_duration_min IS NOT NULL
        AND actual_duration_min > 0
        AND (patient_type IS NULL OR patient_type != 'นอกเวลา')
    """, conn, params=params)

    # Revenue: treatment_cost + patho_cost
    total_treatment = q1(f"SELECT COALESCE(SUM(treatment_cost),0) FROM cases {where} AND status != 'cancelled'")
    total_patho = q1(f"SELECT COALESCE(SUM(patho_cost),0) FROM cases {where} AND status != 'cancelled'")
    total_revenue = total_treatment + total_patho
    n_patho_sent = q1(f"SELECT COUNT(*) FROM cases {where} AND status != 'cancelled' AND patho_cost > 0")

    conn.close()
    return {
        'total': total, 'completed': completed, 'cancelled': cancelled,
        'n_set': n_set, 'n_walkin': n_walkin,
        'n_opd': n_opd, 'n_ipd': n_ipd, 'n_after': n_after,
        'top_procs': top_procs, 'div_stats': div_stats,
        'ai_df': ai_df,
        'oss_needed': oss_needed, 'oss_done': oss_done,
        'or_visit_total': or_visit_total, 'or_visit_done': or_visit_done,
        'call_needed': call_needed, 'called': called,
        'call_ok': call_ok, 'call_miss': call_miss,
        'total_treatment': total_treatment, 'total_patho': total_patho,
        'total_revenue': total_revenue, 'n_patho_sent': n_patho_sent,
    }


def get_db_stats() -> dict:
    conn = get_conn()
    today = _now_dt().strftime('%Y-%m-%d')
    today_total = conn.execute(
        "SELECT COUNT(*) FROM cases WHERE op_date=?", (today,)).fetchone()[0]
    today_done = conn.execute(
        "SELECT COUNT(*) FROM cases WHERE op_date=? AND status IN ('post_op','discharged')",
        (today,)).fetchone()[0]
    pending_calls = conn.execute(
        "SELECT COUNT(*) FROM cases WHERE status='discharged' AND patient_type != 'IPD' AND post_call=0 AND op_date >= ?",
        ((_now_dt() - timedelta(days=7)).strftime('%Y-%m-%d'),)).fetchone()[0]
    # 🆕 total_all = เฉพาะเคสที่ผ่าตัดสำเร็จ (consistent กับ KPI)
    total_all = conn.execute(
        f"SELECT COUNT(*) FROM cases WHERE status IN {_DONE_STATUSES}"
    ).fetchone()[0]
    conn.close()
    return {
        'today': today_total,
        'today_done': today_done,
        'pending_calls': pending_calls,
        'total_all': total_all,
    }


# ============================================================================
# ROOM SETTINGS — persist to DB
# ============================================================================

def save_room_settings(room_no: int, enabled: bool, scrub_list: list, circ_list: list):
    """Save room nurse assignments to DB (scrub/circ as JSON arrays)."""
    import json
    with db_session() as conn:
        conn.execute("""INSERT INTO room_settings (room_no, enabled, scrub_json, circ_json, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(room_no) DO UPDATE SET
                            enabled=excluded.enabled,
                            scrub_json=excluded.scrub_json,
                            circ_json=excluded.circ_json,
                            updated_at=excluded.updated_at""",
                     (room_no, int(enabled), json.dumps(scrub_list, ensure_ascii=False),
                      json.dumps(circ_list, ensure_ascii=False), _now()))


def load_room_settings() -> dict:
    """Load all room settings from DB. Returns {room_no: {'enabled': bool, 'scrub': list, 'circ': list}}."""
    import json
    conn = get_conn()
    rows = conn.execute("SELECT room_no, enabled, scrub_json, circ_json FROM room_settings").fetchall()
    conn.close()
    result = {}
    for r in rows:
        try:
            scrub = json.loads(r['scrub_json']) if r['scrub_json'] else ['', '']
            circ = json.loads(r['circ_json']) if r['circ_json'] else ['', '', '', '']
        except (json.JSONDecodeError, TypeError):
            scrub, circ = ['', ''], ['', '', '', '']
        result[r['room_no']] = {
            'enabled': bool(r['enabled']),
            'scrub': scrub,
            'circ': circ,
        }
    return result


def mark_in_or_with_nurses(case_id: int, scrub_nurse: str = '', circ_nurse: str = ''):
    """Atomic: set nurses + mark in_or in one transaction."""
    with db_session() as conn:
        old = _validate_transition(conn, case_id, 'in_or')
        row = conn.execute("SELECT arrived_at FROM cases WHERE case_id=?", (case_id,)).fetchone()
        wait = 0
        if row and row['arrived_at']:
            try:
                arr = datetime.strptime(row['arrived_at'], '%Y-%m-%d %H:%M:%S')
                wait = int((_now_dt() - arr).total_seconds() / 60)
            except Exception:
                pass
        conn.execute("""UPDATE cases SET status='in_or', in_or_at=?, wait_min=?,
                        scrub_nurse=?, circ_nurse=?, updated_at=?
                        WHERE case_id=?""",
                     (_now(), wait, scrub_nurse, circ_nurse, _now(), case_id))
        _log_audit(conn, case_id, 'status_change', old, 'in_or', f'wait={wait}min')


# ============================================================================
# BACKUP
# ============================================================================

def backup_db() -> str:
    """Create timestamped backup of the DB. Returns backup path."""
    import shutil
    backup_dir = os.path.join(_SCRIPT_DIR, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    ts = _now_dt().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(backup_dir, f'minor_or_{ts}.db')
    shutil.copy2(DB_PATH, backup_path)
    # Log it
    with db_session() as conn:
        n = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        conn.execute("INSERT INTO backup_log (backup_path, row_count, created_at) VALUES (?,?,?)",
                     (backup_path, n, _now()))
    # Keep only last 10 backups
    backups = sorted([f for f in os.listdir(backup_dir) if f.endswith('.db')])
    while len(backups) > 10:
        os.remove(os.path.join(backup_dir, backups.pop(0)))
    return backup_path


# ============================================================================
# PREDICTION RESEARCH QUERIES
# ============================================================================

def get_prediction_accuracy() -> pd.DataFrame:
    """Get prediction log for ML research analysis."""
    with db_session() as conn:
        return pd.read_sql_query("""
            SELECT p.*, c.division_code, c.op_date, c.patient_type
            FROM prediction_log p
            LEFT JOIN cases c ON p.case_id = c.case_id
            ORDER BY p.created_at DESC
        """, conn)


def get_audit_trail(case_id: int = None) -> pd.DataFrame:
    """Get audit trail â optionally filtered by case_id."""
    with db_session() as conn:
        if case_id:
            return pd.read_sql_query(
                "SELECT * FROM audit_log WHERE case_id=? ORDER BY created_at DESC",
                conn, params=[case_id])
        return pd.read_sql_query(
            "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 200", conn)


# ============================================================================
# ADMIN DASHBOARD QUERIES
# ============================================================================

def get_room_status(op_date: str = None) -> list:
    """สถานะห้องผ่าตัดแต่ละห้อง — ใช้ใน Admin Dashboard.

    สำหรับ active case (กำลังผ่า) จะเพิ่ม:
      - _ai_n_cases:   จำนวนเคสที่ใช้ใน local history (จาก predict_from_local_history)
      - _ai_confidence: ระดับความมั่นใจของ AI (สูงมาก/สูง/ปานกลาง/ต่ำ)
      - _ai_source:    'local_history' หรือ 'ml_model' หรือ 'fallback'
    """
    if not op_date:
        op_date = _now_dt().strftime('%Y-%m-%d')
    conn = get_conn()
    rooms = [1, 3, 4, 5]
    result = []
    for rm in rooms:
        cases = pd.read_sql_query("""
            SELECT case_id, name, hn, diagnosis, procedure_name, surgeon_name,
                   status, in_or_at, op_end_at, ai_predicted_min,
                   actual_duration_min
            FROM cases
            WHERE op_date=? AND room_no=? AND status != 'cancelled'
            ORDER BY case_id
        """, conn, params=[op_date, rm])
        active = cases[cases['status'] == 'in_or']
        done = cases[cases['status'].isin(['post_op', 'discharged'])]
        waiting = cases[cases['status'].isin(['scheduled', 'arrived'])]

        active_case = active.iloc[0].to_dict() if len(active) > 0 else None
        # Enrich active case with AI confidence + n_cases used
        if active_case:
            try:
                pred = predict_from_local_history(
                    active_case.get('procedure_name'),
                    active_case.get('surgeon_name'),
                )
                if pred:
                    active_case['_ai_n_cases'] = pred['n_cases']
                    active_case['_ai_confidence'] = pred['confidence']
                    active_case['_ai_source'] = 'local_history'
                else:
                    # No local history → using ML model
                    active_case['_ai_n_cases'] = 0
                    active_case['_ai_confidence'] = 'ต่ำ'
                    active_case['_ai_source'] = 'ml_model'
            except Exception:
                active_case['_ai_n_cases'] = 0
                active_case['_ai_confidence'] = '-'
                active_case['_ai_source'] = '-'

        result.append({
            'room_no': rm,
            'total': len(cases),
            'done': len(done),
            'waiting': len(waiting),
            'active_case': active_case,
            'cases': cases,
        })
    conn.close()
    return result


def get_kpi(op_date: str = None) -> dict:
    """KPI วันนี้ — จำนวนเคส, utilization, turnover time."""
    if not op_date:
        op_date = _now_dt().strftime('%Y-%m-%d')
    conn = get_conn()

    total = conn.execute("SELECT COUNT(*) FROM cases WHERE op_date=? AND status != 'cancelled'",
                         (op_date,)).fetchone()[0]
    done = conn.execute("SELECT COUNT(*) FROM cases WHERE op_date=? AND status IN ('post_op','discharged')",
                        (op_date,)).fetchone()[0]
    in_or = conn.execute("SELECT COUNT(*) FROM cases WHERE op_date=? AND status='in_or'",
                         (op_date,)).fetchone()[0]
    waiting = conn.execute("SELECT COUNT(*) FROM cases WHERE op_date=? AND status IN ('scheduled','arrived')",
                           (op_date,)).fetchone()[0]
    cancelled = conn.execute("SELECT COUNT(*) FROM cases WHERE op_date=? AND status='cancelled'",
                             (op_date,)).fetchone()[0]

    # Utilization: sum of actual_duration / (available minutes * active rooms)
    dur_df = pd.read_sql_query("""
        SELECT room_no, actual_duration_min, in_or_at, op_end_at
        FROM cases WHERE op_date=? AND status IN ('post_op','discharged')
        AND actual_duration_min > 0
    """, conn, params=[op_date])
    total_op_min = int(dur_df['actual_duration_min'].sum()) if len(dur_df) > 0 else 0
    active_rooms = dur_df['room_no'].nunique() if len(dur_df) > 0 else 1
    available_min = 480 * active_rooms  # 8 ชั่วโมง x จำนวนห้องที่ใช้
    utilization = round(total_op_min / available_min * 100, 1) if available_min > 0 else 0.0

    # Turnover time: เวลาระหว่างเคส (op_end_at ของเคสก่อน → in_or_at ของเคสถัดไป)
    turnovers = []
    for rm in [1, 3, 4, 5]:
        rm_cases = pd.read_sql_query("""
            SELECT in_or_at, op_end_at FROM cases
            WHERE op_date=? AND room_no=? AND status IN ('post_op','discharged')
            AND in_or_at IS NOT NULL AND op_end_at IS NOT NULL
            ORDER BY in_or_at
        """, conn, params=[op_date, rm])
        for i in range(1, len(rm_cases)):
            try:
                prev_end = datetime.strptime(rm_cases.iloc[i-1]['op_end_at'], '%Y-%m-%d %H:%M:%S')
                curr_start = datetime.strptime(rm_cases.iloc[i]['in_or_at'], '%Y-%m-%d %H:%M:%S')
                gap = (curr_start - prev_end).total_seconds() / 60
                if 0 < gap < 180:  # สมเหตุสมผล < 3 ชม.
                    turnovers.append(gap)
            except:
                pass
    avg_turnover = round(sum(turnovers) / len(turnovers), 1) if turnovers else 0.0

    conn.close()
    return {
        'total': total, 'done': done, 'in_or': in_or,
        'waiting': waiting, 'cancelled': cancelled,
        'total_op_min': total_op_min, 'utilization': utilization,
        'avg_turnover': avg_turnover, 'n_turnovers': len(turnovers),
        'active_rooms': active_rooms,
    }


def get_delay_alerts(op_date: str = None) -> list:
    """เคสที่มีปัญหา / delay — ใช้ใน Admin Dashboard."""
    if not op_date:
        op_date = _now_dt().strftime('%Y-%m-%d')
    conn = get_conn()
    now = _now_dt()
    alerts = []

    # 1) เคสที่อยู่ in_or นานเกิน predicted + 30%
    overrun = pd.read_sql_query("""
        SELECT case_id, name, hn, procedure_name, surgeon_name, room_no,
               in_or_at, ai_predicted_min
        FROM cases
        WHERE op_date=? AND status='in_or' AND in_or_at IS NOT NULL
    """, conn, params=[op_date])
    for _, row in overrun.iterrows():
        try:
            start = datetime.strptime(row['in_or_at'], '%Y-%m-%d %H:%M:%S')
            elapsed = (now - start).total_seconds() / 60
            predicted = row['ai_predicted_min'] or 60
            if elapsed > predicted * 1.3:
                alerts.append({
                    'type': 'overrun',
                    'severity': 'high' if elapsed > predicted * 1.5 else 'medium',
                    'room_no': row['room_no'],
                    'case_id': row['case_id'],
                    'name': row['name'],
                    'procedure': row['procedure_name'],
                    'message': f"เกินเวลาทำนาย — ผ่านมา {int(elapsed)} นาที (ทำนาย {predicted} นาที)",
                })
        except:
            pass

    # 2) เคสที่ arrived แต่ยังไม่เข้าห้อง > 60 นาที
    long_wait = pd.read_sql_query("""
        SELECT case_id, name, hn, procedure_name, arrived_at, room_no
        FROM cases
        WHERE op_date=? AND status='arrived' AND arrived_at IS NOT NULL
    """, conn, params=[op_date])
    for _, row in long_wait.iterrows():
        try:
            arrived = datetime.strptime(row['arrived_at'], '%Y-%m-%d %H:%M:%S')
            wait = (now - arrived).total_seconds() / 60
            if wait > 60:
                alerts.append({
                    'type': 'long_wait',
                    'severity': 'high' if wait > 120 else 'medium',
                    'room_no': row['room_no'],
                    'case_id': row['case_id'],
                    'name': row['name'],
                    'procedure': row['procedure_name'],
                    'message': f"รอเข้าห้องนาน {int(wait)} นาที",
                })
        except:
            pass

    # 3) เคส cancelled วันนี้
    cancels = pd.read_sql_query("""
        SELECT case_id, name, hn, procedure_name, cancel_reason, room_no
        FROM cases WHERE op_date=? AND status='cancelled'
    """, conn, params=[op_date])
    for _, row in cancels.iterrows():
        alerts.append({
            'type': 'cancelled',
            'severity': 'info',
            'room_no': row['room_no'],
            'case_id': row['case_id'],
            'name': row['name'],
            'procedure': row['procedure_name'],
            'message': f"ยกเลิก — {row['cancel_reason'] or 'ไม่ระบุเหตุผล'}",
        })

    conn.close()
    return sorted(alerts, key=lambda a: {'high': 0, 'medium': 1, 'info': 2}[a['severity']])


def get_workload(op_date: str = None) -> dict:
    """ภาระงาน — Top แพทย์, สาขา, SET/Walk-in, ประเภทผู้ป่วย."""
    if not op_date:
        op_date = _now_dt().strftime('%Y-%m-%d')
    conn = get_conn()
    w = "WHERE op_date=? AND status != 'cancelled'"
    p = [op_date]

    top_surgeons = pd.read_sql_query(f"""
        SELECT surgeon_name, COUNT(*) as n,
               SUM(CASE WHEN status IN ('post_op','discharged') THEN 1 ELSE 0 END) as done
        FROM cases {w} AND surgeon_name IS NOT NULL AND surgeon_name != ''
        GROUP BY surgeon_name ORDER BY n DESC LIMIT 8
    """, conn, params=p)

    div_stats = pd.read_sql_query(f"""
        SELECT division_code, COUNT(*) as n FROM cases {w}
        GROUP BY division_code ORDER BY n DESC
    """, conn, params=p)

    cat_stats = conn.execute(f"""
        SELECT
            SUM(CASE WHEN case_category IN ('SET','เคสนัดหมาย') THEN 1 ELSE 0 END) as n_set,
            SUM(CASE WHEN case_category IN ('WALK-IN','Walk-in') THEN 1 ELSE 0 END) as n_walkin
        FROM cases {w}
    """, p).fetchone()

    type_stats = conn.execute(f"""
        SELECT
            SUM(CASE WHEN patient_type='OPD' THEN 1 ELSE 0 END) as n_opd,
            SUM(CASE WHEN patient_type='IPD' THEN 1 ELSE 0 END) as n_ipd,
            SUM(CASE WHEN patient_type='นอกเวลา' THEN 1 ELSE 0 END) as n_after
        FROM cases {w}
    """, p).fetchone()

    conn.close()
    return {
        'top_surgeons': top_surgeons,
        'div_stats': div_stats,
        'n_set': cat_stats[0] or 0, 'n_walkin': cat_stats[1] or 0,
        'n_opd': type_stats[0] or 0, 'n_ipd': type_stats[1] or 0, 'n_after': type_stats[2] or 0,
    }


def get_nurse_stats(date_from: str = None, date_to: str = None) -> dict:
    """สถิติพยาบาล — จำนวนเคส, ตำแหน่ง scrub/circ, หัตถการ, เวลาเฉลี่ย.
    ใช้สำหรับ track progress ของ novice nurse."""
    conn = get_conn()
    where = "WHERE status IN ('in_or','post_op','discharged')"
    params = []
    if date_from:
        where += " AND op_date >= ?"
        params.append(date_from)
    if date_to:
        where += " AND op_date <= ?"
        params.append(date_to)

    # ดึง raw data ทุกเคสที่มีพยาบาล
    df = pd.read_sql_query(f"""
        SELECT case_id, op_date, procedure_name, surgeon_name, division_code,
               scrub_nurse, circ_nurse, actual_duration_min, room_no
        FROM cases {where}
        AND (scrub_nurse IS NOT NULL OR circ_nurse IS NOT NULL)
        ORDER BY op_date, case_id
    """, conn, params=params)
    conn.close()

    if df.empty:
        return {'nurse_summary': pd.DataFrame(), 'nurse_cases': pd.DataFrame()}

    # Unpivot: สร้าง row per nurse per role (รองรับ comma-separated หลายชื่อ)
    rows = []
    for _, r in df.iterrows():
        for role, col in [('Scrub', 'scrub_nurse'), ('Circulate', 'circ_nurse')]:
            raw = r[col]
            if not raw or not str(raw).strip():
                continue
            # Split comma-separated names
            names = [n.strip() for n in str(raw).split(',') if n.strip()]
            for name in names:
                rows.append({
                    'nurse_name': name,
                    'role': role,
                    'case_id': r['case_id'],
                    'op_date': r['op_date'],
                    'procedure_name': r['procedure_name'],
                    'surgeon_name': r['surgeon_name'],
                    'division_code': r['division_code'],
                    'actual_duration_min': r['actual_duration_min'],
                    'room_no': r['room_no'],
                })
    if not rows:
        return {'nurse_summary': pd.DataFrame(), 'nurse_cases': pd.DataFrame()}

    nurse_df = pd.DataFrame(rows)

    # Summary per nurse
    summary = nurse_df.groupby('nurse_name').agg(
        total_cases=('case_id', 'count'),
        n_scrub=('role', lambda x: (x == 'Scrub').sum()),
        n_circ=('role', lambda x: (x == 'Circulate').sum()),
        unique_procedures=('procedure_name', 'nunique'),
        avg_duration=('actual_duration_min', lambda x: x.dropna().mean()),
        first_date=('op_date', 'min'),
        last_date=('op_date', 'max'),
    ).reset_index().sort_values('total_cases', ascending=False)

    return {
        'nurse_summary': summary,
        'nurse_cases': nurse_df,
    }


# ============================================================================
# HISTORICAL ANALYTICS
# ============================================================================

_DONE_STATUSES = "('post_op','discharged','done')"


def get_historical_analytics(date_from=None, date_to=None):
    conn = get_conn()
    where_parts = ["status IN " + _DONE_STATUSES]
    params = []
    if date_from:
        where_parts.append("op_date >= ?")
        params.append(date_from)
    if date_to:
        where_parts.append("op_date <= ?")
        params.append(date_to)
    where_sql = " AND ".join(where_parts)

    daily_df = pd.read_sql_query(
        f"SELECT op_date, room_no, COUNT(*) as n FROM cases WHERE {where_sql} GROUP BY op_date, room_no ORDER BY op_date",
        conn, params=params)
    daily_total = daily_df.groupby('op_date')['n'].sum().reset_index()
    daily_total.columns = ['op_date', 'n_cases']

    peak_date, peak_count = None, 0
    if not daily_total.empty:
        peak_row = daily_total.loc[daily_total['n_cases'].idxmax()]
        peak_date = peak_row['op_date']
        peak_count = int(peak_row['n_cases'])

    # Heatmap "ภาระงานห้องผ่าตัดเล็ก" — นับเคสที่อยู่ในแต่ละ (dow, hour)
    # เคสคร่อมชั่วโมงจะถูกนับในทุก hour bucket ที่มันคร่อม
    # ตัวอย่าง: เคส 13:18 → 14:50  → นับ +1 ใน slot 13:00 และ +1 ใน slot 14:00
    # ที่ frontend จะหารด้วยจำนวน dow ในช่วง → ได้ "เฉลี่ย X เคสต่อครั้ง"
    hour_df = pd.read_sql_query(
        f"SELECT op_date, in_or_at, op_end_at FROM cases WHERE {where_sql} "
        f"AND in_or_at IS NOT NULL AND op_end_at IS NOT NULL",
        conn, params=params)

    peak_hour, peak_hour_count = 9, 0
    records = []
    for _, row in hour_df.iterrows():
        op_date = row.get('op_date')
        if not op_date:
            continue
        try:
            t_start = datetime.strptime(row['in_or_at'], '%Y-%m-%d %H:%M:%S')
            t_end = datetime.strptime(row['op_end_at'], '%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            continue
        if t_end <= t_start:
            continue
        try:
            dow = pd.to_datetime(op_date).dayofweek
        except (ValueError, TypeError):
            continue
        # ทุก hour bucket ที่เคสคร่อม — นับ +1 ครั้ง
        cur = t_start.replace(minute=0, second=0, microsecond=0)
        while cur < t_end:
            if 7 <= cur.hour <= 17:
                records.append({'dow': int(dow), 'hour': int(cur.hour)})
            cur = cur + timedelta(hours=1)

    if records:
        df_rec = pd.DataFrame(records)
        heatmap_df = (df_rec.groupby(['dow', 'hour']).size()
                            .reset_index(name='n'))
        if not heatmap_df.empty:
            ps = heatmap_df.loc[heatmap_df['n'].idxmax()]
            peak_hour = int(ps['hour'])
            peak_hour_count = int(ps['n'])
    else:
        df_rec = pd.DataFrame(columns=['dow', 'hour'])
        heatmap_df = pd.DataFrame(columns=['dow', 'hour', 'n'])

    # ============================================================
    # Top day-of-week (จ.-ศ. เท่านั้น) + ช่วงเวลาเคสเยอะของวันนั้น
    # ============================================================
    THAI_DAY_FULL = ['จันทร์','อังคาร','พุธ','พฤหัสบดี','ศุกร์','เสาร์','อาทิตย์']
    top_dow_idx = -1
    top_dow_count = 0
    top_dow_name = '-'
    top_dow_hour = peak_hour
    if not daily_total.empty:
        _dt = daily_total.copy()
        _dt['dow'] = _dt['op_date'].apply(
            lambda d: pd.to_datetime(d).dayofweek if d else None)
        _wd = _dt[_dt['dow'].isin([0, 1, 2, 3, 4])]
        if not _wd.empty:
            _dow_sum = _wd.groupby('dow')['n_cases'].sum().reset_index()
            _top = _dow_sum.loc[_dow_sum['n_cases'].idxmax()]
            top_dow_idx = int(_top['dow'])
            top_dow_count = int(_top['n_cases'])
            top_dow_name = THAI_DAY_FULL[top_dow_idx]
            # หา peak hour ของ dow นั้นโดยเฉพาะ
            if not df_rec.empty:
                _hh = df_rec[df_rec['dow'] == top_dow_idx]
                if not _hh.empty:
                    _hc = _hh.groupby('hour').size().reset_index(name='n')
                    top_dow_hour = int(_hc.loc[_hc['n'].idxmax()]['hour'])

    # ============================================================
    # Utilization Rate (Coverage 8:00-16:00, นับเฉพาะวันที่มีเคส)
    # = นาทีที่มีเคสในห้อง (union ของช่วงเวลา) ÷ (วันมีเคส × 480 นาที)
    # ============================================================
    WORK_START_HOUR = 8
    WORK_END_HOUR = 16
    WORK_MIN_PER_DAY = (WORK_END_HOUR - WORK_START_HOUR) * 60  # 480
    util_active_min = 0
    util_n_days = 0
    if not hour_df.empty:
        cases_by_date = {}
        for _, _r in hour_df.iterrows():
            _od = _r.get('op_date')
            if not _od:
                continue
            try:
                _ts = datetime.strptime(_r['in_or_at'], '%Y-%m-%d %H:%M:%S')
                _te = datetime.strptime(_r['op_end_at'], '%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                continue
            if _te <= _ts:
                continue
            cases_by_date.setdefault(_od, []).append((_ts, _te))

        for _od, _ivs in cases_by_date.items():
            try:
                _dd = datetime.strptime(_od, '%Y-%m-%d')
            except (ValueError, TypeError):
                continue
            _ws = _dd.replace(hour=WORK_START_HOUR, minute=0, second=0)
            _we = _dd.replace(hour=WORK_END_HOUR, minute=0, second=0)
            # clip เคสให้อยู่ในช่วง 8-16
            _clipped = []
            for _s, _e in _ivs:
                _cs = max(_s, _ws)
                _ce = min(_e, _we)
                if _cs < _ce:
                    _clipped.append((_cs, _ce))
            if not _clipped:
                continue
            # union ของ intervals (รวม overlap)
            _clipped.sort()
            _merged = [_clipped[0]]
            for _s, _e in _clipped[1:]:
                _ls, _le = _merged[-1]
                if _s <= _le:
                    _merged[-1] = (_ls, max(_le, _e))
                else:
                    _merged.append((_s, _e))
            for _s, _e in _merged:
                util_active_min += (_e - _s).total_seconds() / 60
            util_n_days += 1

    util_total_min = util_n_days * WORK_MIN_PER_DAY
    util_rate = round(util_active_min / util_total_min * 100, 1) if util_total_min > 0 else 0

    div_df = pd.read_sql_query(
        f"SELECT division_code, COUNT(*) as n FROM cases WHERE {where_sql} GROUP BY division_code ORDER BY n DESC",
        conn, params=params)
    top_div_name, top_div_count, top_div_pct = '-', 0, 0
    top_div_name, top_div_count, top_div_pct = '-', 0, 0
    if not div_df.empty:
        div_df['division_name'] = div_df['division_code'].apply(div_name)
        top_div_name = div_df.iloc[0]['division_name']
        top_div_count = int(div_df.iloc[0]['n'])
        top_div_pct = round(top_div_count / div_df['n'].sum() * 100, 1)

    # NOTE: LIMIT bumped to 200 — fuzzy grouping in minor_or_admin.py needs
    # the long tail to merge variants like "off PERM cath" + "off TCC Rt IJV".
    proc_df = pd.read_sql_query(
        f"SELECT procedure_name, COUNT(*) as n, AVG(actual_duration_min) as avg_min FROM cases WHERE {where_sql} GROUP BY procedure_name ORDER BY n DESC LIMIT 200",
        conn, params=params)

    total_cases = conn.execute(f"SELECT COUNT(*) FROM cases WHERE {where_sql}", params).fetchone()[0]
    conn.close()

    # นับจำนวนแต่ละวันในสัปดาห์ที่ปรากฏใน date range
    # ใช้สำหรับหารหา "ห้องเฉลี่ยที่วิ่งพร้อมกัน" ใน heatmap
    # เช่น ถ้าช่วงคือ 4 สัปดาห์ → จันทร์มี 4 วัน, ศุกร์มี 4 วัน เป็นต้น
    dow_counts = {}
    try:
        if date_from and date_to:
            for d in pd.date_range(start=date_from, end=date_to, freq='D'):
                dow_counts[int(d.dayofweek)] = dow_counts.get(int(d.dayofweek), 0) + 1
    except (ValueError, TypeError):
        pass

    return {
        'total_cases': total_cases,
        'daily_df': daily_df, 'daily_total': daily_total,
        'peak_date': peak_date, 'peak_count': peak_count,
        'heatmap_df': heatmap_df,
        'dow_counts': dow_counts,
        'peak_hour': peak_hour, 'peak_hour_count': peak_hour_count,
        # Top day-of-week (จ.-ศ.)
        'top_dow_idx': top_dow_idx,
        'top_dow_name': top_dow_name,
        'top_dow_count': top_dow_count,
        'top_dow_hour': top_dow_hour,
        # Utilization Rate (Coverage 8-16, วันมีเคสเท่านั้น)
        'util_rate': util_rate,
        'util_active_min': int(util_active_min),
        'util_total_min': int(util_total_min),
        'util_n_days': util_n_days,
        'div_df': div_df,
        'top_div_name': top_div_name, 'top_div_count': top_div_count, 'top_div_pct': top_div_pct,
        'proc_df': proc_df,
    }



def export_cases_csv(date_from=None, date_to=None):
    conn = get_conn()
    where_parts = ["1=1"]
    params = []
    if date_from:
        where_parts.append("op_date >= ?")
        params.append(date_from)
    if date_to:
        where_parts.append("op_date <= ?")
        params.append(date_to)
    where_sql = " AND ".join(where_parts)
    sql = f"""
        SELECT case_id, op_date, name, hn, an, procedure_name, surgeon_name,
               division_code, case_category, patient_type, op_type,
               status, room_no, scrub_nurse, circ_nurse,
               ai_predicted_min, user_override_min, actual_duration_min,
               wait_min, arrived_at, in_or_at, op_end_at, discharged_at,
               post_op_dest, cancel_reason
        FROM cases WHERE {where_sql} ORDER BY op_date, case_id
    """
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    if not df.empty and 'division_code' in df.columns:
        df['division_name'] = df['division_code'].apply(div_name)
    return df


# ---------------------------------------------------------------------------
# Wait-time statistics
# ---------------------------------------------------------------------------
def get_wait_stats(date_from: str = None, date_to: str = None) -> dict:
    """สถิติเวลารอ — เคสรอเกิน 60 นาที, avg wait per day, top wait days."""
    conn = get_conn()
    where_parts, params = ["patient_type != 'นอกเวลา'", "wait_min IS NOT NULL", "wait_min > 0"], []
    if date_from:
        where_parts.append("op_date >= ?"); params.append(date_from)
    if date_to:
        where_parts.append("op_date <= ?"); params.append(date_to)
    where_sql = " AND ".join(where_parts)

    # 1) เคสรอเกิน 60 นาที
    long_wait = pd.read_sql_query(f"""
        SELECT case_id, op_date, name, hn, procedure_name, surgeon_name,
               division_code, room_no, wait_min
        FROM cases WHERE {where_sql} AND wait_min > 60
        ORDER BY wait_min DESC
    """, conn, params=params)
    if not long_wait.empty and 'division_code' in long_wait.columns:
        long_wait['division_name'] = long_wait['division_code'].apply(div_name)

    # 2) avg wait per day
    daily_wait = pd.read_sql_query(f"""
        SELECT op_date,
               ROUND(AVG(wait_min), 1) AS avg_wait,
               MAX(wait_min) AS max_wait,
               COUNT(*) AS n_cases
        FROM cases WHERE {where_sql}
        GROUP BY op_date ORDER BY op_date
    """, conn, params=params)

    # 3) overall stats
    overall = pd.read_sql_query(f"""
        SELECT ROUND(AVG(wait_min), 1) AS avg_all,
               MAX(wait_min) AS max_all,
               COUNT(*) AS total,
               SUM(CASE WHEN wait_min > 60 THEN 1 ELSE 0 END) AS over_60
        FROM cases WHERE {where_sql}
    """, conn, params=params)

    conn.close()
    row = overall.iloc[0] if not overall.empty else {}
    return {
        'long_wait_cases': long_wait,
        'daily_wait': daily_wait,
        'avg_all': row.get('avg_all', 0) or 0,
        'max_all': row.get('max_all', 0) or 0,
        'total': int(row.get('total', 0) or 0),
        'over_60': int(row.get('over_60', 0) or 0),
    }


# ---------------------------------------------------------------------------
# Handover statistics  (เคสที่ยังไม่ discharge ณ 15:30 น.)
# ---------------------------------------------------------------------------
def get_turnover_stats(date_from: str = None, date_to: str = None,
                        min_min: int = 1, max_min: int = 90) -> dict:
    """🔄 Turnover Time Analytics — ช่วงเวลาระหว่างเคสในห้องเดียวกัน

    Turnover = (เคสถัดไป.in_or_at) - (เคสก่อนหน้า.op_end_at)
    เรียงตาม op_date + room_no + in_or_at — เคสที่อยู่ในห้องเดียวกันวันเดียวกัน

    Args:
        min_min, max_min: ช่วง turnover ที่ valid (กรอง outlier เช่น เคสคู่ขนาน หรือ pause นาน)

    Returns dict: avg, median, p90, max, n, daily DF, top5 DF, heatmap DF
    """
    import pandas as pd
    conn = get_conn()
    where_parts = ["status IN ('post_op','discharged','done')",
                   "in_or_at IS NOT NULL", "op_end_at IS NOT NULL"]
    params = []
    if date_from:
        where_parts.append("op_date >= ?"); params.append(date_from)
    if date_to:
        where_parts.append("op_date <= ?"); params.append(date_to)
    where_sql = " AND ".join(where_parts)

    df = pd.read_sql_query(
        f"SELECT case_id, op_date, room_no, procedure_name, "
        f"in_or_at, op_end_at FROM cases WHERE {where_sql} "
        f"ORDER BY op_date, room_no, in_or_at",
        conn, params=params)
    conn.close()

    empty = {
        'avg': 0, 'median': 0, 'p90': 0, 'max': 0, 'n': 0,
        'daily': pd.DataFrame(columns=['op_date', 'avg_turnover']),
        'top5': pd.DataFrame(columns=['op_date', 'prev_proc', 'next_proc', 'turnover_min']),
        'heatmap': pd.DataFrame(columns=['dow', 'hour', 'avg_turnover']),
        'raw': pd.DataFrame(columns=['op_date', 'room_no', 'turnover_min']),
    }
    if df.empty:
        return empty

    df['_in_dt'] = pd.to_datetime(df['in_or_at'], errors='coerce')
    df['_end_dt'] = pd.to_datetime(df['op_end_at'], errors='coerce')
    df = df.dropna(subset=['_in_dt', '_end_dt'])

    turnovers = []
    for (op_date, room_no), grp in df.groupby(['op_date', 'room_no']):
        grp = grp.sort_values('_in_dt')
        prev_end, prev_proc = None, None
        for _, r in grp.iterrows():
            if prev_end is not None:
                tt_min = (r['_in_dt'] - prev_end).total_seconds() / 60
                if min_min <= tt_min <= max_min:
                    turnovers.append({
                        'op_date': op_date,
                        'room_no': room_no,
                        'turnover_min': round(tt_min, 1),
                        'prev_proc': prev_proc or '-',
                        'next_proc': r['procedure_name'] or '-',
                        'dow': int(r['_in_dt'].dayofweek),
                        'hour': int(r['_in_dt'].hour),
                    })
            prev_end = r['_end_dt']
            prev_proc = r['procedure_name']

    if not turnovers:
        return empty

    tdf = pd.DataFrame(turnovers)
    daily = tdf.groupby('op_date')['turnover_min'].mean().reset_index()
    daily.columns = ['op_date', 'avg_turnover']
    daily['avg_turnover'] = daily['avg_turnover'].round(1)

    top5 = tdf.nlargest(5, 'turnover_min')[
        ['op_date', 'prev_proc', 'next_proc', 'turnover_min']].copy()

    # Heatmap: dow (จ-ศ = 0-4) × hour (8-17)
    heat = tdf[(tdf['dow'].between(0, 4)) & (tdf['hour'].between(8, 17))].copy()
    heatmap = (heat.groupby(['dow', 'hour'])['turnover_min']
                   .mean().round(1).reset_index())
    heatmap.columns = ['dow', 'hour', 'avg_turnover']

    return {
        'avg': round(float(tdf['turnover_min'].mean()), 1),
        'median': round(float(tdf['turnover_min'].median()), 1),
        'p90': round(float(tdf['turnover_min'].quantile(0.9)), 1),
        'max': round(float(tdf['turnover_min'].max()), 1),
        'n': len(tdf),
        'daily': daily,
        'top5': top5,
        'heatmap': heatmap,
        'raw': tdf[['op_date', 'room_no', 'turnover_min']].copy(),
    }


def get_surgeon_list(date_from: str = None, date_to: str = None,
                     sort_by: str = 'scheduled') -> pd.DataFrame:
    """รายชื่อแพทย์ + จำนวนเคส set/ทำจริง/มอบหมาย

    sort_by: 'scheduled' (เรียงตามที่ set) หรือ 'actual' (เรียงตามที่ทำจริง)

    Returns DataFrame: surgeon, n_scheduled, n_actual, n_delegated
    """
    conn = get_conn()
    df = pd.read_sql_query(
        """SELECT scheduled_surgeon, surgeon_name
           FROM cases
           WHERE op_date BETWEEN ? AND ?
             AND status != 'cancelled'""",
        conn, params=(date_from or '1900-01-01', date_to or '2999-12-31'))
    conn.close()
    if df.empty:
        return pd.DataFrame(columns=['surgeon', 'n_scheduled',
                                     'n_actual', 'n_delegated'])

    # Normalize ทั้ง 2 คอลัมน์ (ตัดยศ/คำนำหน้า)
    df['sched_clean'] = df['scheduled_surgeon'].fillna('').astype(str).apply(
        _normalize_proxy_for_surgeon)
    df['actual_clean'] = df['surgeon_name'].fillna('').astype(str).apply(
        _normalize_proxy_for_surgeon)

    # นับ scheduled / actual / delegated
    n_sched = (df[df['sched_clean'] != '']
               .groupby('sched_clean').size()
               .reset_index(name='n_scheduled'))
    n_sched.columns = ['surgeon', 'n_scheduled']

    n_actual = (df[df['actual_clean'] != '']
                .groupby('actual_clean').size()
                .reset_index(name='n_actual'))
    n_actual.columns = ['surgeon', 'n_actual']

    # Delegated = scheduled แต่ ไม่ตรงกับ actual ในเคสเดียวกัน
    df['is_delegated'] = (
        (df['sched_clean'] != '') & (df['actual_clean'] != '') &
        (df['sched_clean'] != df['actual_clean']))
    n_del = (df[df['is_delegated']]
             .groupby('sched_clean').size()
             .reset_index(name='n_delegated'))
    n_del.columns = ['surgeon', 'n_delegated']

    # Merge (outer = แสดงทั้งฝั่ง schedule + intraop แม้ไม่ตรงกัน)
    out = n_sched.merge(n_actual, on='surgeon', how='outer')
    out = out.merge(n_del, on='surgeon', how='outer')
    out = out.fillna(0)
    for c in ['n_scheduled', 'n_actual', 'n_delegated']:
        out[c] = out[c].astype(int)
    # เพิ่ม column สำหรับเรียง — ใช้ค่าที่มากกว่าระหว่าง 2 ฝั่ง
    # → ผู้ที่ set 100/actual 0 จะอยู่ลำดับสูงเสมอ
    out['_sort_key'] = out[['n_scheduled', 'n_actual']].max(axis=1)
    # secondary sort: ตามที่ user เลือก
    sort_col = 'n_scheduled' if sort_by == 'scheduled' else 'n_actual'
    out = out.sort_values(['_sort_key', sort_col],
                          ascending=[False, False]).reset_index(drop=True)
    out = out.drop(columns=['_sort_key'])
    # ตัดแถวที่ scheduled=0 AND actual=0
    out = out[(out['n_scheduled'] > 0) | (out['n_actual'] > 0)]
    return out


def get_surgeon_detail(surgeon: str, date_from: str = None,
                       date_to: str = None) -> dict:
    """รายละเอียดแพทย์รายคน — counts + Top procedures (จาก intraop)

    surgeon: ชื่อหลัง normalize (ตัดยศแล้ว)

    Returns dict: n_scheduled, n_actual, n_delegated,
                  top_procedures (DataFrame)
    """
    conn = get_conn()
    df = pd.read_sql_query(
        """SELECT scheduled_surgeon, surgeon_name, procedure_name, op_date
           FROM cases
           WHERE op_date BETWEEN ? AND ?
             AND status != 'cancelled'""",
        conn, params=(date_from or '1900-01-01', date_to or '2999-12-31'))
    conn.close()
    if df.empty:
        return {'n_scheduled': 0, 'n_actual': 0, 'n_delegated': 0,
                'top_procedures': pd.DataFrame()}

    df['sched_clean'] = df['scheduled_surgeon'].fillna('').astype(str).apply(
        _normalize_proxy_for_surgeon)
    df['actual_clean'] = df['surgeon_name'].fillna('').astype(str).apply(
        _normalize_proxy_for_surgeon)

    n_sched = int((df['sched_clean'] == surgeon).sum())
    n_actual = int((df['actual_clean'] == surgeon).sum())
    n_del = int(((df['sched_clean'] == surgeon) &
                 (df['actual_clean'] != '') &
                 (df['actual_clean'] != surgeon)).sum())

    # Top procedures — นับจากที่แพทย์คนนี้ "ทำจริง" (actual)
    actual_cases = df[df['actual_clean'] == surgeon].copy()
    if not actual_cases.empty:
        actual_cases['proc'] = actual_cases['procedure_name'].fillna('-').apply(
            _normalize_procedure_name)
        top_proc = (actual_cases.groupby('proc')
                    .size().reset_index(name='n_cases')
                    .sort_values('n_cases', ascending=False).head(5))
        top_proc.columns = ['procedure', 'n_cases']
    else:
        top_proc = pd.DataFrame(columns=['procedure', 'n_cases'])

    return {
        'n_scheduled': n_sched,
        'n_actual': n_actual,
        'n_delegated': n_del,
        'top_procedures': top_proc,
    }


# ===== Surgeon name normalizer (proxy ที่ใช้ _normalize_nurse_name) =====
# Used by get_surgeon_list / get_surgeon_detail
def _normalize_proxy_for_surgeon(name) -> str:
    """Wrapper เรียก normalize ที่อยู่ใน minor_or_admin.py (ผ่าน import lazy)"""
    if not name or not isinstance(name, str):
        return ''
    s = name.strip()
    if not s:
        return ''
    # ลบ ยศ/คำนำหน้า ด้วย regex inline (เลี่ยง circular import)
    import re as _re
    _TITLE_RE = _re.compile(
        r'^\s*(?:ว่าที่\s*)?'
        # Compound civilian (LONG first)
        r'(?:นายแพทย์|ทันตแพทย์|เภสัชกรหญิง|เภสัชกรชาย|เภสัชกร|'
        r'แพทย์หญิง|แพทย์ชาย|แพทย์|'
        # ตำรวจ
        r'พล\.?ต\.?[อทต]\.?|พ\.?ต\.?[อทต]\.?|ร\.?ต\.?[อทต]\.?|ด\.?ต\.?|'
        r'จ\.?ส\.?ต\.?|จ\.?ส\.?[อทต]\.?|ส\.?ต\.?[อทต]\.?|'
        # ทหาร
        r'พล\.?[อทต]\.?|พล\.?จ\.?|พ\.?[อทต]\.?|ร\.?[อทต]\.?|'
        # พลเรือน (นางสาว ก่อน นาย+นาง)
        r'นางสาว|นาย|นาง|น\.?ส\.?|'
        r'เด็กชาย|เด็กหญิง|ด\.?ช\.?|ด\.?ญ\.?|'
        # ตัวย่อ
        r'นพ\.?|พญ\.?|ดร\.?|ผศ\.?|รศ\.?|ศ\.?)'
        r'\s*(?:หญิง|ชาย)?\s*')   # \s+ → \s*
    prev = None
    while prev != s:
        prev = s
        s = _TITLE_RE.sub('', s)
    return _re.sub(r'\s+', ' ', s).strip()


def get_on_time_start_stats(date_from: str = None, date_to: str = None,
                            target_hour: int = 9, target_min: int = 0,
                            tolerance_min: int = 30) -> dict:
    """🎯 On-Time First Case Start Rate

    เปรียบเทียบ in_or_at เคสแรกของแต่ละวัน (จ-ศ, ไม่นับนอกเวลา)
    กับเวลามาตรฐาน (default 09:00 น.) — ยอมเกินได้ tolerance_min นาที

    Returns dict: rate (%), n_on_time, n_total, daily DF, late_top5 DF
    """
    import pandas as pd
    conn = get_conn()
    df = pd.read_sql_query(
        """SELECT op_date, room_no, in_or_at, procedure_name, surgeon_name,
                  patient_type
           FROM cases
           WHERE status IN ('post_op','discharged','done')
             AND in_or_at IS NOT NULL
             AND (patient_type IS NULL OR patient_type != 'นอกเวลา')
             AND (? IS NULL OR op_date >= ?)
             AND (? IS NULL OR op_date <= ?)
           ORDER BY op_date, room_no, in_or_at""",
        conn, params=(date_from, date_from, date_to, date_to))
    conn.close()

    empty = {
        'rate': 0, 'n_on_time': 0, 'n_total': 0,
        'target_str': f"{target_hour:02d}:{target_min:02d}",
        'tolerance_min': tolerance_min,
        'daily': pd.DataFrame(columns=['op_date', 'room_no', 'in_or_at',
                                       'delay_min', 'on_time']),
        'late_top5': pd.DataFrame(),
    }
    if df.empty:
        return empty

    # Filter Mon-Fri
    df['_dt'] = pd.to_datetime(df['op_date'])
    df = df[df['_dt'].dt.dayofweek.between(0, 4)]
    if df.empty:
        return empty

    df['_in_dt'] = pd.to_datetime(df['in_or_at'], errors='coerce')
    df = df.dropna(subset=['_in_dt'])
    if df.empty:
        return empty

    # หาเคสแรกของแต่ละ (op_date, room_no)
    df_first = df.sort_values('_in_dt').groupby(
        ['op_date', 'room_no'], as_index=False).first()

    # คำนวณ delay (นาที) จาก 09:00 น.
    df_first['_target'] = (df_first['_in_dt'].dt.normalize() +
                           pd.Timedelta(hours=target_hour, minutes=target_min))
    df_first['delay_min'] = (
        (df_first['_in_dt'] - df_first['_target'])
        .dt.total_seconds() / 60).round(1)
    df_first['on_time'] = df_first['delay_min'] <= tolerance_min

    daily = df_first[['op_date', 'room_no', 'in_or_at', 'procedure_name',
                      'surgeon_name', 'delay_min', 'on_time']].copy()

    # Top 5 ช้าสุด
    late_top5 = daily[daily['delay_min'] > tolerance_min].nlargest(
        5, 'delay_min')[['op_date', 'room_no', 'in_or_at',
                         'procedure_name', 'surgeon_name', 'delay_min']]

    n_total = len(daily)
    n_on = int(daily['on_time'].sum())
    rate = round(n_on / max(n_total, 1) * 100, 1)

    return {
        'rate': rate,
        'n_on_time': n_on,
        'n_total': n_total,
        'target_str': f"{target_hour:02d}:{target_min:02d}",
        'tolerance_min': tolerance_min,
        'daily': daily,
        'late_top5': late_top5,
    }


def get_nurse_skill_map(date_from: str = None, date_to: str = None,
                        top_n_procedures: int = 10) -> dict:
    """👯 Nurse Skill Map — พยาบาล × หัตถการ = จำนวนครั้ง

    แยก scrub และ circulate เป็น 2 ตาราง
    ใช้ _normalize_procedure_name เพื่อรวม fuzzy match

    Returns dict: scrub_df, circ_df (รวม normalize แล้ว), top_procs list
    """
    import pandas as pd
    conn = get_conn()
    df = pd.read_sql_query(
        """SELECT scrub_nurse, circ_nurse, procedure_name, op_date
           FROM cases
           WHERE status IN ('post_op','discharged','done')
             AND procedure_name IS NOT NULL AND procedure_name != ''
             AND (? IS NULL OR op_date >= ?)
             AND (? IS NULL OR op_date <= ?)""",
        conn, params=(date_from, date_from, date_to, date_to))
    conn.close()

    empty = {
        'scrub_df': pd.DataFrame(),
        'circ_df': pd.DataFrame(),
        'top_procs': [],
    }
    if df.empty:
        return empty

    # Normalize procedure names
    df['proc'] = df['procedure_name'].apply(_normalize_procedure_name)
    # หา top N procedures
    top_procs = (df['proc'].value_counts().head(top_n_procedures)
                 .index.tolist())

    def _pivot(role_col: str) -> pd.DataFrame:
        sub = df[df[role_col].notna() & (df[role_col] != '')].copy()
        if sub.empty:
            return pd.DataFrame()
        # Normalize nurse name (strip titles)
        sub['nurse'] = sub[role_col].astype(str).str.strip()
        # Filter only top procedures
        sub = sub[sub['proc'].isin(top_procs)]
        if sub.empty:
            return pd.DataFrame()
        pivot = (sub.groupby(['nurse', 'proc'])
                 .size().unstack(fill_value=0))
        # เรียง column ตาม top_procs
        for p in top_procs:
            if p not in pivot.columns:
                pivot[p] = 0
        pivot = pivot[top_procs]
        # เพิ่มคอลัมน์ total
        pivot['รวม'] = pivot.sum(axis=1)
        pivot = pivot.sort_values('รวม', ascending=False)
        return pivot

    return {
        'scrub_df': _pivot('scrub_nurse'),
        'circ_df': _pivot('circ_nurse'),
        'top_procs': top_procs,
    }


def get_handover_stats(date_from: str = None, date_to: str = None) -> dict:
    """สถิติรับเวร — เคสที่ผ่าตัดเสร็จหลัง 15:30 น. เฉพาะวันธรรมดา (จ.-ศ.)

    - ไม่นับเคสนอกเวลา (เคสนอกเวลา = คนละหมวด)
    - ไม่นับวันเสาร์-อาทิตย์ (ราชการไม่ได้ทำงาน)
    - คำนวณ overtime_hours = ชั่วโมงเลย 15:30 น.
    """
    conn = get_conn()
    where_parts = [
        "patient_type != 'นอกเวลา'",
        # SQLite strftime('%w', d): Sun=0, Mon=1, ..., Sat=6
        "CAST(strftime('%w', op_date) AS INTEGER) BETWEEN 1 AND 5"
    ]
    params = []
    if date_from:
        where_parts.append("op_date >= ?"); params.append(date_from)
    if date_to:
        where_parts.append("op_date <= ?"); params.append(date_to)
    where_sql = " AND ".join(where_parts)

    # Handover cases + overtime hours (เลย 15:30 = 930 นาที)
    handover_cases = pd.read_sql_query(f"""
        SELECT case_id, op_date, name, hn, procedure_name, surgeon_name,
               division_code, room_no, status,
               arrived_at, in_or_at, op_end_at, discharged_at,
               CASE
                 WHEN discharged_at IS NOT NULL THEN
                   ROUND((CAST(SUBSTR(discharged_at, 12, 2) AS INTEGER) * 60
                          + CAST(SUBSTR(discharged_at, 15, 2) AS INTEGER) - 930)
                         / 60.0, 2)
                 ELSE NULL
               END AS overtime_hours
        FROM cases
        WHERE {where_sql}
          AND (
              (discharged_at IS NOT NULL AND SUBSTR(discharged_at, 12, 5) > '15:30')
              OR (status NOT IN ('discharged', 'cancelled') AND op_date < DATE('now'))
          )
        ORDER BY op_date DESC, discharged_at DESC
    """, conn, params=params)
    if not handover_cases.empty and 'division_code' in handover_cases.columns:
        handover_cases['division_name'] = handover_cases['division_code'].apply(div_name)

    # สรุปรายเดือน — เคสกี่เคส + รวม overtime กี่ชั่วโมง
    monthly = pd.read_sql_query(f"""
        SELECT strftime('%Y-%m', op_date) AS month,
               COUNT(*) AS n_cases,
               ROUND(SUM(
                   (CAST(SUBSTR(discharged_at, 12, 2) AS INTEGER) * 60
                    + CAST(SUBSTR(discharged_at, 15, 2) AS INTEGER) - 930) / 60.0
               ), 1) AS overtime_hours
        FROM cases
        WHERE {where_sql}
          AND discharged_at IS NOT NULL
          AND SUBSTR(discharged_at, 12, 5) > '15:30'
        GROUP BY month
        ORDER BY month
    """, conn, params=params)

    # สรุปรายวัน (เก็บไว้สำหรับ chart)
    daily_handover = pd.read_sql_query(f"""
        SELECT op_date, COUNT(*) AS n_handover
        FROM cases
        WHERE {where_sql}
          AND (
              (discharged_at IS NOT NULL AND SUBSTR(discharged_at, 12, 5) > '15:30')
              OR (status NOT IN ('discharged', 'cancelled') AND op_date < DATE('now'))
          )
        GROUP BY op_date ORDER BY op_date
    """, conn, params=params)

    # Total weekday cases (ไม่รวม cancelled)
    total_row = pd.read_sql_query(f"""
        SELECT COUNT(*) AS total
        FROM cases WHERE {where_sql} AND status != 'cancelled'
    """, conn, params=params)

    conn.close()
    total = int(total_row.iloc[0]['total']) if not total_row.empty else 0
    n_handover = int(handover_cases.shape[0])
    return {
        'handover_cases': handover_cases,
        'daily_handover': daily_handover,
        'monthly': monthly,
        'n_handover': n_handover,
        'total': total,
        'pct': round(n_handover / total * 100, 1) if total > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Excel export wrapper — delegate to minor_or_export.py
# ---------------------------------------------------------------------------
def export_summary_excel(date_from=None, date_to=None) -> bytes:
    """Thin wrapper so callers can just pass date range."""
    from minor_or_export import export_summary_excel as _export
    return _export(get_summary, export_cases_csv, div_name, date_from, date_to)
